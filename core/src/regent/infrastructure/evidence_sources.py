"""Evidence source connectors for product discovery."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from regent.application.p1_ports import EvidenceSourceRequest, EvidenceSourceSnapshot
from regent.infrastructure.artifact_store import FileArtifactStore

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "you are now",
    "<|im_start|>",
    "```system",
)


def _normalize_domain(host: str) -> str:
    host = host.strip().lower().rstrip(".")
    if host.startswith("www."):
        return host[4:]
    return host


def _domain_allowed(host: str, allowed_domains: frozenset[str]) -> bool:
    domain = _normalize_domain(host)
    if not domain:
        return False
    for allowed in allowed_domains:
        base = _normalize_domain(allowed)
        if domain == base or domain.endswith("." + base):
            return True
    return False


def _detect_injection_flags(text: str) -> list[str]:
    lowered = text.lower()
    return [pattern for pattern in _INJECTION_PATTERNS if pattern in lowered]


def _parse_feed_entries(content: bytes, *, limit: int = 20) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    entries: list[dict[str, str]] = []
    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()
        if title or link:
            entries.append({"title": title[:300], "link": link[:500], "summary": summary[:500]})
        if len(entries) >= limit:
            return entries
    # Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//atom:entry", ns):
        title = (item.findtext("atom:title", default="", namespaces=ns) or "").strip()
        link_el = item.find("atom:link", ns)
        link = ""
        if link_el is not None:
            link = (link_el.attrib.get("href") or "").strip()
        summary = (item.findtext("atom:summary", default="", namespaces=ns) or "").strip()
        if title or link:
            entries.append({"title": title[:300], "link": link[:500], "summary": summary[:500]})
        if len(entries) >= limit:
            break
    return entries


class InMemoryEvidenceSourceConnector:
    """Deterministic connector for orchestration tests; performs no network side effects."""

    def __init__(self, snapshots: Iterable[EvidenceSourceSnapshot]) -> None:
        self._snapshots = tuple(snapshots)
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        return list(self._snapshots)


class GoalIntentEvidenceConnector:
    """Persist the goal query as an immutable SourceSnapshot artifact.

    P1 treats the confirmed user Goal text as first-class evidence of declared
    intent. This is not model commons knowledge: content is hashed, stored under
    the artifact store, and cited by discovery via evidence UUIDs.
    """

    def __init__(self, artifacts: FileArtifactStore) -> None:
        self._artifacts = artifacts
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        query = request.query.strip()
        if not query:
            return []

        scope = uuid.uuid5(uuid.NAMESPACE_URL, f"regent:evidence:{request.correlation_id}")
        captured_at = datetime.now(UTC).isoformat()
        # Hash only stable intent content so repeated fetches are idempotent.
        stable_payload = {
            "kind": "goal-intent",
            "query": query,
            "source_types": list(request.source_types),
            "correlation_id": request.correlation_id,
        }
        content = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        stored = self._artifacts.put(scope, f"evidence/{digest[:2]}/{digest}.json", content)
        return [
            EvidenceSourceSnapshot(
                source_uri=f"regent://goal-intent/{request.correlation_id}",
                captured_at=captured_at,
                content_artifact_uri=stored.uri,
                content_hash=stored.content_hash,
                metadata={
                    "connector": "goal-intent-v1",
                    "kind": "goal-intent",
                    "byte_size": stored.size,
                    "budget": dict(request.budget),
                },
                trust_label="DECLARED_INTENT",
                source_type="goal-intent",
            )
        ]


class CompositeEvidenceSourceConnector:
    """Fan out to multiple connectors and merge snapshots."""

    def __init__(self, connectors: list[object]) -> None:
        self._connectors = list(connectors)

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        snapshots: list[EvidenceSourceSnapshot] = []
        seen: set[str] = set()
        for connector in self._connectors:
            for item in await connector.fetch(request):  # type: ignore[attr-defined]
                if item.content_hash in seen:
                    continue
                seen.add(item.content_hash)
                snapshots.append(item)
        return snapshots


class AllowlistedHttpEvidenceConnector:
    """Generic allowlisted HTTP snapshot port (capability implementation).

    Core does not own product feeds (RSS, news sites, etc.). This connector only
    fetches URLs explicitly authorized on the EvidenceSourceRequest, and only when
    the host is inside the operator platform allowlist and egress proxy is configured.
    Fail-closed otherwise. Bytes are untrusted evidence, never instructions.
    """

    def __init__(
        self,
        artifacts: FileArtifactStore,
        *,
        allowed_domains: Sequence[str],
        egress_proxy: str | None,
        max_bytes: int = 262_144,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._allowed_domains = frozenset(
            _normalize_domain(item) for item in allowed_domains if item.strip()
        )
        self._egress_proxy = egress_proxy
        self._max_bytes = max(1024, max_bytes)
        self._timeout_seconds = timeout_seconds
        self._client = client
        self.requests: list[EvidenceSourceRequest] = []

    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]:
        self.requests.append(request)
        if not self._allowed_domains:
            return []
        if not self._egress_proxy or urlparse(self._egress_proxy).scheme not in {"http", "https"}:
            logger.warning("http evidence skipped: egress proxy not configured")
            return []

        targets = self._candidate_urls(request)
        if not targets:
            return []

        snapshots: list[EvidenceSourceSnapshot] = []
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            proxy=self._egress_proxy,
            timeout=self._timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "RegentEvidenceBot/1.0"},
        )
        try:
            for url in targets:
                snapshot = await self._fetch_one(client, request, url)
                if snapshot is not None:
                    snapshots.append(snapshot)
        finally:
            if owns_client:
                await client.aclose()
        return snapshots

    def _candidate_urls(self, request: EvidenceSourceRequest) -> list[str]:
        from_query = [match.rstrip(").,;]") for match in _URL_RE.findall(request.query)]
        ordered = list(
            dict.fromkeys([*request.authorized_urls, *from_query])
        )
        allowed: list[str] = []
        for url in ordered:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            if not _domain_allowed(parsed.hostname or "", self._allowed_domains):
                logger.info(
                    "http evidence skipped: domain not allowlisted",
                    extra={"url": url, "host": parsed.hostname},
                )
                continue
            allowed.append(url)
            if len(allowed) >= 5:
                break
        return allowed

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        request: EvidenceSourceRequest,
        url: str,
    ) -> EvidenceSourceSnapshot | None:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception:
            logger.warning("http evidence fetch failed", extra={"url": url}, exc_info=True)
            return None

        final_url = str(response.url)
        final_host = urlparse(final_url).hostname or ""
        if not _domain_allowed(final_host, self._allowed_domains):
            logger.warning(
                "http evidence rejected after redirect outside allowlist",
                extra={"url": url, "final_url": final_url},
            )
            return None

        body = response.content[: self._max_bytes]
        media_type = (response.headers.get("content-type") or "application/octet-stream").split(
            ";", 1
        )[0].strip()
        text_excerpt = ""
        try:
            text_excerpt = body.decode("utf-8", errors="replace")[:4000]
        except Exception:
            text_excerpt = ""
        entries: list[dict[str, str]] = []
        head = body[:200].lstrip()
        looks_like_feed = (
            "xml" in media_type
            or "rss" in media_type
            or "atom" in media_type
            or head.startswith((b"<?xml", b"<rss", b"<feed"))
        )
        if looks_like_feed:
            entries = _parse_feed_entries(body)
        scope = uuid.uuid5(uuid.NAMESPACE_URL, f"regent:http-evidence:{request.correlation_id}")
        digest = hashlib.sha256(body).hexdigest()
        stored = self._artifacts.put(scope, f"evidence/http/{digest[:2]}/{digest}.bin", body)
        captured_at = datetime.now(UTC).isoformat()
        metadata: dict[str, Any] = {
            "connector": "allowlisted-http-source-v1",
            "kind": "http-snapshot",
            "media_type": media_type,
            "requested_url": url,
            "final_url": final_url,
            "byte_size": len(body),
            "truncated": len(response.content) > len(body),
            "text_excerpt": text_excerpt,
            "entries": entries,
            "injection_flags": _detect_injection_flags(text_excerpt),
            "access_policy_ref": "evidence-allowlist-v1",
            "budget": dict(request.budget),
        }
        return EvidenceSourceSnapshot(
            source_uri=final_url,
            captured_at=captured_at,
            content_artifact_uri=stored.uri,
            content_hash=stored.content_hash,
            metadata=metadata,
            trust_label="UNTRUSTED_DATA",
            source_type="http-snapshot",
        )

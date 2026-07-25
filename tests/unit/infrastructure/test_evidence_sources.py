import uuid

import httpx
import pytest
from regent.application.evidence_policy import (
    collect_authorized_urls,
    goal_requires_external_evidence,
)
from regent.application.p1_ports import EvidenceSourceRequest
from regent.infrastructure.artifact_store import FileArtifactStore
from regent.infrastructure.evidence_sources import (
    AllowlistedHttpEvidenceConnector,
    CompositeEvidenceSourceConnector,
    GoalIntentEvidenceConnector,
)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Demo Feed</title>
<item><title>Observed Headline One</title><link>https://techcrunch.com/a</link>
<description>Summary one</description></item>
<item><title>Observed Headline Two</title><link>https://techcrunch.com/b</link>
<description>Summary two</description></item>
</channel></rss>
"""


def test_collect_authorized_urls_from_goal_only() -> None:
    urls = collect_authorized_urls(
        "Build digest using https://techcrunch.com/feed/ and https://hnrss.org/frontpage",
        {"fixed_sources": "names only, no urls"},
    )
    assert urls == [
        "https://techcrunch.com/feed/",
        "https://hnrss.org/frontpage",
    ]


def test_goal_requires_external_evidence_for_news() -> None:
    assert goal_requires_external_evidence("AI industry news digest", {}) is True
    assert goal_requires_external_evidence("hello world timestamp page", {}) is False


def test_goal_requires_external_evidence_ignores_paste_summary_tools() -> None:
    """Weekly-report / paste-summarize Goals must not inherit default news feeds."""
    assert (
        goal_requires_external_evidence(
            "做一个内部团队周报汇总 Web 工具，支持粘贴文本并生成结构化摘要页",
            {},
        )
        is False
    )
    assert goal_requires_external_evidence("科技资讯列表并支持按关键词过滤", {}) is True


@pytest.mark.asyncio
async def test_allowlisted_http_fetches_only_authorized_urls(tmp_path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = AllowlistedHttpEvidenceConnector(
        store,
        allowed_domains=["techcrunch.com"],
        egress_proxy="http://regent-egress:3128",
        client=client,
    )
    # No URL in query and no authorized_urls → no Core seeds → empty
    empty = await connector.fetch(
        EvidenceSourceRequest(query="Build an AI news digest", correlation_id=str(uuid.uuid4()))
    )
    assert empty == []
    assert seen == []

    snapshots = await connector.fetch(
        EvidenceSourceRequest(
            query="Build an AI news digest",
            correlation_id=str(uuid.uuid4()),
            authorized_urls=["https://techcrunch.com/feed/"],
        )
    )
    assert len(snapshots) == 1
    assert snapshots[0].metadata["kind"] == "http-snapshot"
    assert snapshots[0].metadata["entries"][0]["title"] == "Observed Headline One"
    await client.aclose()


@pytest.mark.asyncio
async def test_allowlisted_http_fail_closed_without_proxy(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = AllowlistedHttpEvidenceConnector(
        store,
        allowed_domains=["techcrunch.com"],
        egress_proxy=None,
    )
    snapshots = await connector.fetch(
        EvidenceSourceRequest(
            query="news",
            correlation_id=str(uuid.uuid4()),
            authorized_urls=["https://techcrunch.com/feed/"],
        )
    )
    assert snapshots == []


@pytest.mark.asyncio
async def test_allowlisted_http_rejects_non_allowlisted_domain(tmp_path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = AllowlistedHttpEvidenceConnector(
        store,
        allowed_domains=["techcrunch.com"],
        egress_proxy="http://regent-egress:3128",
        client=client,
    )
    snapshots = await connector.fetch(
        EvidenceSourceRequest(
            query="see https://evil.example/x",
            correlation_id=str(uuid.uuid4()),
            authorized_urls=["https://evil.example/feed"],
        )
    )
    assert snapshots == []
    assert called is False
    await client.aclose()


@pytest.mark.asyncio
async def test_composite_merges_goal_intent_and_authorized_http(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS, headers={"content-type": "application/rss+xml"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    store = FileArtifactStore(tmp_path / "artifacts")
    composite = CompositeEvidenceSourceConnector(
        [
            GoalIntentEvidenceConnector(store),
            AllowlistedHttpEvidenceConnector(
                store,
                allowed_domains=["techcrunch.com"],
                egress_proxy="http://regent-egress:3128",
                client=client,
            ),
        ]
    )
    snapshots = await composite.fetch(
        EvidenceSourceRequest(
            query="AI news digest app https://techcrunch.com/feed/",
            correlation_id=str(uuid.uuid4()),
            authorized_urls=["https://techcrunch.com/feed/"],
        )
    )
    kinds = {item.metadata.get("kind") for item in snapshots}
    assert kinds == {"goal-intent", "http-snapshot"}
    await client.aclose()


@pytest.mark.asyncio
async def test_goal_intent_evidence_is_persisted_and_hashed(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = GoalIntentEvidenceConnector(store)
    request = EvidenceSourceRequest(
        query="Build a hello world timestamp page",
        correlation_id=str(uuid.uuid4()),
    )
    snapshots = await connector.fetch(request)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.metadata["kind"] == "goal-intent"
    assert snap.content_hash
    assert snap.content_artifact_uri.startswith("file:")
    again = await connector.fetch(request)
    assert again[0].content_hash == snap.content_hash


@pytest.mark.asyncio
async def test_goal_intent_evidence_skips_empty_query(tmp_path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    connector = GoalIntentEvidenceConnector(store)
    snapshots = await connector.fetch(
        EvidenceSourceRequest(query="   ", correlation_id=str(uuid.uuid4()))
    )
    assert snapshots == []

"""Versioned workspace file manifest policy (M1-5).

Replaces dual suffix allowlists with a single versioned policy used by
snapshot, materialization, and verification integrity checks.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_POLICY_VERSION = "file-manifest/v1"

# Explicit text extensions (plus extensionless text files when UTF-8 decodable).
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".svg",
        ".sql",
        ".json",
        ".md",
        ".txt",
        ".toml",
        ".yml",
        ".yaml",
        ".ini",
        ".cfg",
        ".env.example",
    }
)

EXPLICIT_FILENAMES: frozenset[str] = frozenset(
    {
        "requirements.txt",
        "pyproject.toml",
        "Dockerfile",
        "Procfile",
        "README",
        "LICENSE",
        "Makefile",
        ".gitignore",
        ".dockerignore",
    }
)

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".regent",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".turbo",
        "coverage",
    }
)

SECRET_NAME_MARKERS: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    "id_rsa",
    "id_ed25519",
    ".env",
    "credentials",
    "secrets.json",
    "service-account",
)

DEFAULT_MAX_FILES = 200
DEFAULT_MAX_FILE_BYTES = 200_000
DEFAULT_MAX_TOTAL_BYTES = 8_000_000


@dataclass(slots=True)
class ManifestEntry:
    path: str
    bytes: int
    sha256: str
    included: bool
    reason: str | None = None


@dataclass(slots=True)
class WorkspaceManifest:
    policy_version: str = MANIFEST_POLICY_VERSION
    entries: list[ManifestEntry] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    integrity_ok: bool = True
    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    total_bytes_included: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "truncated": self.truncated,
            "integrity_ok": self.integrity_ok,
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "total_bytes_included": self.total_bytes_included,
            "file_count": len(self.files),
            "files": sorted(self.files.keys()),
            "entries": [asdict(e) for e in self.entries],
            "skipped": [asdict(e) for e in self.entries if not e.included],
        }

    @property
    def content_hash(self) -> str:
        payload = "|".join(
            f"{e.path}:{e.sha256}" for e in self.entries if e.included
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_excluded_path(rel: str) -> bool | str:
    parts = Path(rel).parts
    for part in parts[:-1]:
        if part in EXCLUDED_DIR_NAMES:
            return f"excluded_dir:{part}"
    name = parts[-1] if parts else rel
    lower = name.lower()
    if lower == ".env" or lower.endswith(".env"):
        return "secret_or_env"
    for marker in SECRET_NAME_MARKERS:
        if marker in lower and lower not in {".env.example", "credentials.example.json"}:
            if marker == ".env" and lower == ".env.example":
                continue
            if marker in lower:
                return f"secret_marker:{marker}"
    return False


def _is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    if path.name in EXPLICIT_FILENAMES or path.name.lower() in {
        n.lower() for n in EXPLICIT_FILENAMES
    }:
        return True
    # Extensionless: allow if later UTF-8 decode succeeds.
    return suffix == ""


def build_workspace_manifest(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> WorkspaceManifest:
    """Scan workspace and produce an integrity-aware manifest."""
    root = root.resolve()
    files: dict[str, str] = {}
    entries: list[ManifestEntry] = []
    total = 0
    truncated = False

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".regent") or rel in {
            ".regent_budget_exhausted.json",
            ".regent_agent_transcript.json",
            ".regent_run_ledger.json",
            ".regent_smoke_probe.py",
        }:
            continue
        excluded = _is_excluded_path(rel)
        if excluded:
            entries.append(
                ManifestEntry(
                    path=rel,
                    bytes=path.stat().st_size,
                    sha256="",
                    included=False,
                    reason=str(excluded),
                )
            )
            continue
        if not _is_text_candidate(path):
            entries.append(
                ManifestEntry(
                    path=rel,
                    bytes=path.stat().st_size,
                    sha256="",
                    included=False,
                    reason="non_text_extension",
                )
            )
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) > max_file_bytes:
            truncated = True
            entries.append(
                ManifestEntry(
                    path=rel,
                    bytes=len(raw),
                    sha256=digest,
                    included=False,
                    reason="max_file_bytes",
                )
            )
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            entries.append(
                ManifestEntry(
                    path=rel,
                    bytes=len(raw),
                    sha256=digest,
                    included=False,
                    reason="binary_or_undecodable",
                )
            )
            continue
        if len(files) >= max_files:
            truncated = True
            entries.append(
                ManifestEntry(
                    path=rel,
                    bytes=len(raw),
                    sha256=digest,
                    included=False,
                    reason="max_files",
                )
            )
            continue
        if total + len(raw) > max_total_bytes:
            truncated = True
            entries.append(
                ManifestEntry(
                    path=rel,
                    bytes=len(raw),
                    sha256=digest,
                    included=False,
                    reason="max_total_bytes",
                )
            )
            continue
        files[rel] = text
        total += len(raw)
        entries.append(
            ManifestEntry(
                path=rel,
                bytes=len(raw),
                sha256=digest,
                included=True,
                reason=None,
            )
        )

    integrity_ok = not truncated and not any(
        e.reason in {"max_files", "max_file_bytes", "max_total_bytes"} for e in entries
    )
    return WorkspaceManifest(
        entries=entries,
        files=files,
        truncated=truncated,
        integrity_ok=integrity_ok,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        total_bytes_included=total,
    )

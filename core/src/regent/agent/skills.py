"""Minimal Agent Skills package (M5): manifest, load-by-need, version/hash.

Skills never grant extra permissions — Permit remains the authority.

Progressive disclosure (agent-matrix catalog / agentskills.io):
  1. Load catalog index (id + description only)
  2. Route by goal keywords / gap codes
  3. Load full guidance only for selected skills
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILLS_ROOT = Path(__file__).resolve().parent / "skill_packs"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """Lightweight discovery row — no guidance body."""

    skill_id: str
    version: str
    title: str
    description: str
    applies_when: tuple[str, ...]
    gap_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "applies_when": list(self.applies_when),
            "gap_codes": list(self.gap_codes),
        }


@dataclass(frozen=True, slots=True)
class SkillManifest:
    skill_id: str
    version: str
    title: str
    description: str
    applies_when: tuple[str, ...]
    anti_examples: tuple[str, ...]
    guidance: str
    gap_codes: tuple[str, ...]
    content_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "applies_when": list(self.applies_when),
            "anti_examples": list(self.anti_examples),
            "guidance": self.guidance,
            "gap_codes": list(self.gap_codes),
            "content_hash": self.content_hash,
        }


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _parse_simple_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-ish frontmatter without requiring PyYAML (agentskills SKILL.md)."""
    text = raw if raw.endswith("\n") else f"{raw}\n"
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter — treat whole file as guidance.
        return {}, raw.strip()
    meta_block, body = match.group(1), match.group(2)
    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in meta_block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key:
            meta.setdefault(current_list_key, []).append(stripped[2:].strip().strip("\"'"))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "" or value == "[]":
            current_list_key = key
            meta[key] = [] if value == "[]" else meta.get(key, [])
            continue
        current_list_key = None
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [
                p.strip().strip("\"'") for p in inner.split(",") if p.strip()
            ]
        else:
            meta[key] = value.strip("\"'")
    return meta, body.strip()


def _skill_dir_has_manifest(path: Path) -> bool:
    return (path / "SKILL.json").is_file() or (path / "SKILL.md").is_file()


def load_skill_catalog(*, root: Path | None = None) -> list[SkillCatalogEntry]:
    """Stage-1 discovery: prefer index.json; fall back to dir scan metadata only."""
    base = root or SKILLS_ROOT
    index_path = base / "index.json"
    if index_path.is_file():
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        entries: list[SkillCatalogEntry] = []
        for item in raw.get("skills") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("skill_id") or "").strip()
            if not sid:
                continue
            entries.append(
                SkillCatalogEntry(
                    skill_id=sid,
                    version=str(item.get("version") or "0"),
                    title=str(item.get("title") or sid),
                    description=str(item.get("description") or ""),
                    applies_when=tuple(str(x) for x in (item.get("applies_when") or ())),
                    gap_codes=tuple(str(x) for x in (item.get("gap_codes") or ())),
                )
            )
        if entries:
            return entries
    # Fallback: skim SKILL.json / SKILL.md without loading GUIDANCE.md bodies.
    out: list[SkillCatalogEntry] = []
    for skill_id in list_builtin_skill_ids(root=base):
        json_path = base / skill_id / "SKILL.json"
        if json_path.is_file():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            out.append(
                SkillCatalogEntry(
                    skill_id=str(data.get("skill_id") or skill_id),
                    version=str(data.get("version") or "0"),
                    title=str(data.get("title") or skill_id),
                    description=str(data.get("description") or ""),
                    applies_when=tuple(str(x) for x in (data.get("applies_when") or ())),
                    gap_codes=tuple(str(x) for x in (data.get("gap_codes") or ())),
                )
            )
            continue
        md_path = base / skill_id / "SKILL.md"
        if md_path.is_file():
            meta, _ = _parse_simple_frontmatter(md_path.read_text(encoding="utf-8"))
            sid = str(meta.get("name") or meta.get("skill_id") or skill_id)
            out.append(
                SkillCatalogEntry(
                    skill_id=sid,
                    version=str(meta.get("version") or "0"),
                    title=str(meta.get("title") or sid),
                    description=str(meta.get("description") or ""),
                    applies_when=tuple(str(x) for x in (meta.get("applies_when") or ())),
                    gap_codes=tuple(str(x) for x in (meta.get("gap_codes") or ())),
                )
            )
    return out


def load_skill_manifest(
    skill_id: str,
    *,
    root: Path | None = None,
    lessons_workspace: Path | None = None,
) -> SkillManifest:
    """Stage-3: load full guidance for a selected skill.

    Prefer SKILL.json + GUIDANCE.md (legacy Regent). Fall back to agentskills
    SKILL.md (frontmatter + body). When ``lessons_workspace`` is set, append
    evolved LESSONS.md overlays (PenguinHarness-style harness state).
    """
    base = root or SKILLS_ROOT
    json_path = base / skill_id / "SKILL.json"
    md_path = base / skill_id / "SKILL.md"
    if json_path.is_file():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        guidance_path = base / skill_id / "GUIDANCE.md"
        guidance = guidance_path.read_text(encoding="utf-8") if guidance_path.is_file() else ""
        # Optional SKILL.md body can append (agentskills dual-format packs).
        if md_path.is_file() and not guidance:
            _, guidance = _parse_simple_frontmatter(md_path.read_text(encoding="utf-8"))
        guidance = _merge_lessons(guidance, skill_id, lessons_workspace)
        payload = {**raw, "guidance": guidance}
        digest = _hash_payload(payload)
        return SkillManifest(
            skill_id=str(raw["skill_id"]),
            version=str(raw["version"]),
            title=str(raw["title"]),
            description=str(raw.get("description") or ""),
            applies_when=tuple(str(x) for x in (raw.get("applies_when") or ())),
            anti_examples=tuple(str(x) for x in (raw.get("anti_examples") or ())),
            guidance=guidance,
            gap_codes=tuple(str(x) for x in (raw.get("gap_codes") or ())),
            content_hash=digest,
        )
    if md_path.is_file():
        meta, guidance = _parse_simple_frontmatter(md_path.read_text(encoding="utf-8"))
        sid = str(meta.get("name") or meta.get("skill_id") or skill_id)
        guidance = _merge_lessons(guidance, sid, lessons_workspace)
        payload = {**meta, "guidance": guidance}
        digest = _hash_payload(payload)
        applies = meta.get("applies_when") or []
        gaps = meta.get("gap_codes") or []
        anti = meta.get("anti_examples") or []
        if isinstance(applies, str):
            applies = [applies]
        if isinstance(gaps, str):
            gaps = [gaps]
        if isinstance(anti, str):
            anti = [anti]
        return SkillManifest(
            skill_id=sid,
            version=str(meta.get("version") or "1.0.0"),
            title=str(meta.get("title") or sid),
            description=str(meta.get("description") or ""),
            applies_when=tuple(str(x) for x in applies),
            anti_examples=tuple(str(x) for x in anti),
            guidance=guidance,
            gap_codes=tuple(str(x) for x in gaps),
            content_hash=digest,
        )
    raise FileNotFoundError(f"skill not found: {skill_id}")


def _merge_lessons(
    guidance: str, skill_id: str, lessons_workspace: Path | None
) -> str:
    """Append evolved LESSONS.md when present under workspace harness-lessons/."""
    if lessons_workspace is None:
        return guidance
    path = Path(lessons_workspace) / "harness-lessons" / skill_id / "LESSONS.md"
    if not path.is_file():
        # Also allow lessons_workspace to already be the harness-lessons root.
        alt = Path(lessons_workspace) / skill_id / "LESSONS.md"
        path = alt if alt.is_file() else path
    if not path.is_file():
        return guidance
    lessons = path.read_text(encoding="utf-8").strip()
    if not lessons:
        return guidance
    block = (
        "\n\n## Evolved harness lessons (self-evolution)\n"
        "These lessons were accepted only after strict score improvement. "
        "Follow them; do not weaken product QA gates.\n\n"
        f"{lessons}\n"
    )
    return (guidance or "") + block


def list_builtin_skill_ids(*, root: Path | None = None) -> list[str]:
    base = root or SKILLS_ROOT
    if not base.is_dir():
        return []
    catalog = None
    index_path = base / "index.json"
    if index_path.is_file():
        try:
            catalog = load_skill_catalog(root=base)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            catalog = None
    if catalog:
        ids = [e.skill_id for e in catalog]
        # Include on-disk packs missing from index (dev packs).
        for p in base.iterdir():
            if p.is_dir() and _skill_dir_has_manifest(p) and p.name not in ids:
                ids.append(p.name)
        return sorted(ids)
    return sorted(
        p.name for p in base.iterdir() if p.is_dir() and _skill_dir_has_manifest(p)
    )


def route_skills_for_gaps(
    gap_codes: list[str], *, root: Path | None = None
) -> list[SkillManifest]:
    """Map verification gap codes → skill guidance (verifier does not mutate artifacts)."""
    selected: list[SkillManifest] = []
    seen: set[str] = set()
    for entry in load_skill_catalog(root=root):
        matched = False
        for code in gap_codes:
            for gc in entry.gap_codes:
                if code == gc or code.startswith(gc) or gc in code:
                    matched = True
                    break
            if matched:
                break
        if matched and entry.skill_id not in seen:
            seen.add(entry.skill_id)
            selected.append(load_skill_manifest(entry.skill_id, root=root))
    return selected


# W4-P1-2: Chinese aliases folded into routing (substring match on original text).
_CJK_WEB_HINTS = (
    "网站",
    "网页",
    "应用",
    "系统",
    "平台",
    "地图",
    "看板",
    "档案",
    "百科",
    "检索",
    "上传",
    "表单",
    "待办",
    "笔记",
    "接口",
    "服务",
)


def select_skills_for_goal(
    goal_text: str,
    *,
    enabled: bool = True,
    root: Path | None = None,
    lessons_workspace: Path | None = None,
    gap_codes: list[str] | None = None,
) -> list[SkillManifest]:
    """Lightweight keyword router with progressive disclosure (catalog → full load)."""
    if not enabled:
        return []
    raw = goal_text or ""
    text = raw.lower()
    catalog = load_skill_catalog(root=root)
    chosen_ids: list[str] = []
    for entry in catalog:
        if any(token.lower() in text or token in raw for token in entry.applies_when):
            chosen_ids.append(entry.skill_id)
    # Route by open delivery / live-QA gap codes (PenguinHarness evaluate→skill).
    for gap in gap_codes or []:
        head = str(gap).split(":", 1)[-1].strip()
        for entry in catalog:
            if head in entry.gap_codes or str(gap) in entry.gap_codes:
                chosen_ids.append(entry.skill_id)
    # English web shapes.
    if not chosen_ids and any(k in text for k in ("app", "web", "flask", "api", "site")):
        ids = {e.skill_id for e in catalog}
        if "runtime-contract" in ids:
            chosen_ids.append("runtime-contract")
    # W4: Chinese product goals often miss English tokens — inject defaults.
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in raw)
    if not chosen_ids and (
        any(h in raw for h in _CJK_WEB_HINTS)
        or (has_cjk and len(raw.strip()) >= 4)
    ):
        ids = {e.skill_id for e in catalog}
        for sid in ("runtime-contract", "web-app-scaffold", "persistence", "ui", "product"):
            if sid in ids:
                chosen_ids.append(sid)
    # Deduplicate preserving order; load full guidance only now.
    seen: set[str] = set()
    chosen: list[SkillManifest] = []
    for sid in chosen_ids:
        if sid in seen:
            continue
        seen.add(sid)
        chosen.append(
            load_skill_manifest(
                sid, root=root, lessons_workspace=lessons_workspace
            )
        )
    return chosen


def skill_ablation_report(
    *,
    on_pass: int,
    on_total: int,
    off_pass: int,
    off_total: int,
) -> dict[str, Any]:
    """Engineering gate helper — no significance claims for small n."""
    on_rate = (on_pass / on_total) if on_total else 0.0
    off_rate = (off_pass / off_total) if off_total else 0.0
    return {
        "on_pass_rate": on_rate,
        "off_pass_rate": off_rate,
        "delta": on_rate - off_rate,
        "on_n": on_total,
        "off_n": off_total,
        "engineering_gate_only": True,
        "no_significance_claims": True,
    }


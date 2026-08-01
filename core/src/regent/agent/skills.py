"""Minimal Agent Skills package (M5): manifest, load-by-need, version/hash.

Skills never grant extra permissions — Permit remains the authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILLS_ROOT = Path(__file__).resolve().parent / "skill_packs"


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


def load_skill_manifest(skill_id: str, *, root: Path | None = None) -> SkillManifest:
    base = root or SKILLS_ROOT
    path = base / skill_id / "SKILL.json"
    if not path.is_file():
        raise FileNotFoundError(f"skill not found: {skill_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    guidance_path = base / skill_id / "GUIDANCE.md"
    guidance = guidance_path.read_text(encoding="utf-8") if guidance_path.is_file() else ""
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


def list_builtin_skill_ids(*, root: Path | None = None) -> list[str]:
    base = root or SKILLS_ROOT
    if not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir() if p.is_dir() and (p / "SKILL.json").is_file()
    )


def route_skills_for_gaps(
    gap_codes: list[str], *, root: Path | None = None
) -> list[SkillManifest]:
    """Map verification gap codes → skill guidance (verifier does not mutate artifacts)."""
    selected: list[SkillManifest] = []
    seen: set[str] = set()
    for skill_id in list_builtin_skill_ids(root=root):
        manifest = load_skill_manifest(skill_id, root=root)
        matched = False
        for code in gap_codes:
            for gc in manifest.gap_codes:
                if code == gc or code.startswith(gc) or gc in code:
                    matched = True
                    break
            if matched:
                break
        if matched and manifest.skill_id not in seen:
            seen.add(manifest.skill_id)
            selected.append(manifest)
    return selected


def select_skills_for_goal(
    goal_text: str,
    *,
    enabled: bool = True,
    root: Path | None = None,
) -> list[SkillManifest]:
    """Lightweight keyword router for on/off ablation (M5-4)."""
    if not enabled:
        return []
    text = (goal_text or "").lower()
    chosen: list[SkillManifest] = []
    for skill_id in list_builtin_skill_ids(root=root):
        manifest = load_skill_manifest(skill_id, root=root)
        if any(token.lower() in text for token in manifest.applies_when):
            chosen.append(manifest)
    # Always offer runtime-contract for web shapes when any skill matches or goal mentions app.
    if not chosen and any(k in text for k in ("app", "web", "flask", "api", "site")):
        if "runtime-contract" in list_builtin_skill_ids(root=root):
            chosen.append(load_skill_manifest("runtime-contract", root=root))
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

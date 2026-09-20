"""责编 soft → 局部修稿闭环（默认 shadow，不改正式正文）。

必须在 ``chapter_accept.finalize_chapter_acceptance`` **之前**调用。
复用 ``prose_patch.apply_patch``；每章最多一轮、最多三个表达类问题。
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from regent.novel.domain.prose_patch import (
    PatchError,
    apply_patch,
    content_hash,
    paragraphs_payload,
    split_paragraphs,
)

EditorialMode = Literal["off", "shadow", "auto"]
EDITORIAL_POLICY_VERSION = "editorial-repair-v1"

# 第一批可自动局部修：表达问题；事实类转既有硬核验，不在此自动改。
AUTO_ELIGIBLE_RULES: frozenset[str] = frozenset(
    {
        "redundant_beats",
        "metaphor_overuse",
        "closed_set_without_scope",
        "jargon_first_gloss",
    }
)

# 不得仅作润色；命中则升级/保留给硬通道。
FACTUAL_ESCALATE_RULES: frozenset[str] = frozenset(
    {
        "intra_chapter_contradiction",
        "title_cast_mismatch",
    }
)

MAX_ISSUES_PER_ROUND = 3
MAX_TOUCH_RATIO = 0.15
MAX_TOUCH_CHARS = 1200


class EditorialIssue(BaseModel):
    issue_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    quote: str = Field(default="")
    reason: str = Field(default="")
    repair_goal: str = Field(default="")
    kind: Literal["expression", "factual", "structural", "preference"] = "expression"
    paragraph_ids: list[str] = Field(default_factory=list)
    status: str = "detected"


class ParagraphReplacement(BaseModel):
    paragraph_ids: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)


class ChapterTextPatch(BaseModel):
    base_content_hash: str = Field(min_length=16)
    purpose: str = Field(default="")
    resolved_issue_ids: list[str] = Field(default_factory=list)
    replacements: list[ParagraphReplacement] = Field(min_length=1)


class EditorialVerifyResult(BaseModel):
    accept: bool = False
    issues_resolved: list[str] = Field(default_factory=list)
    new_hard_problems: list[str] = Field(default_factory=list)
    notes: str = Field(default="")


ModelCall = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class EditorialRepairOutcome:
    mode: EditorialMode
    attempted: bool = False
    applied: bool = False
    kept_original: bool = True
    issues_targeted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    base_content_hash: str = ""
    candidate_content_hash: str = ""
    candidate_content: str | None = None
    editorial: dict[str, Any] = field(default_factory=dict)

    def as_context(self) -> dict[str, Any]:
        return {
            "policy_version": EDITORIAL_POLICY_VERSION,
            "mode": self.mode,
            "attempted": self.attempted,
            "applied": self.applied,
            "kept_original": self.kept_original,
            "issues_targeted": list(self.issues_targeted),
            "notes": list(self.notes),
            "base_content_hash": self.base_content_hash,
            "candidate_content_hash": self.candidate_content_hash,
            **self.editorial,
        }


def resolve_editorial_mode(raw: str | None = None) -> EditorialMode:
    if raw is None:
        raw = os.environ.get("EDITOR_REPAIR_MODE") or os.environ.get(
            "REGENT_EDITOR_REPAIR_MODE"
        )
    if raw is None:
        try:
            from regent.config import get_settings

            raw = get_settings().editor_repair_mode
        except Exception:  # noqa: BLE001
            raw = "shadow"
    text = (str(raw) if raw is not None else "shadow").strip().lower()
    if text in {"0", "false", "off", "never"}:
        return "off"
    if text in {"auto", "apply", "on", "1", "true"}:
        return "auto"
    if text in {"shadow", "dry-run", "dryrun"}:
        return "shadow"
    return "shadow"


def _auto_percent() -> int:
    try:
        from regent.config import get_settings

        return int(get_settings().editor_repair_auto_percent)
    except Exception:  # noqa: BLE001
        raw = os.environ.get("EDITOR_REPAIR_AUTO_PERCENT") or os.environ.get(
            "REGENT_EDITOR_REPAIR_AUTO_PERCENT"
        )
        try:
            return max(0, min(100, int(raw or 0)))
        except ValueError:
            return 0


def work_in_auto_canary(work_id: str | None) -> bool:
    """Stable per-work bucket for auto apply gray."""
    percent = _auto_percent()
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    if not work_id:
        return False
    digest = hashlib.sha256(str(work_id).encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < percent


def resolve_editorial_mode_for_work(
    work_id: str | None = None, *, raw: str | None = None
) -> EditorialMode:
    """Resolve mode with auto canary: mode=auto but outside bucket → shadow."""
    mode = resolve_editorial_mode(raw)
    if mode != "auto":
        return mode
    if work_in_auto_canary(work_id):
        return "auto"
    return "shadow"


def issue_id_for(*, rule_id: str, base_hash: str, quote: str, index: int) -> str:
    digest = hashlib.sha256(
        f"{rule_id}|{base_hash}|{quote}|{index}".encode()
    ).hexdigest()[:16]
    return f"{rule_id}:{digest}"


_SOFT_RE = re.compile(
    r"^\[editor(?:-soft)?:(?P<rule>[^\]]+)\]\s*(?P<reason>.*?)(?:｜摘录「(?P<quote>.*)」)?\s*$"
)


def parse_editor_soft_strings(
    soft_issues: Sequence[str],
    *,
    chapter: str,
    base_hash: str,
) -> list[EditorialIssue]:
    """从兼容字符串投影结构化 Issue；quote 必须能在正文定位才 eligible。"""
    paragraphs = split_paragraphs(chapter)
    out: list[EditorialIssue] = []
    for index, raw in enumerate(soft_issues):
        text = str(raw or "").strip()
        if not text or text.startswith("[editor-soft:skipped]"):
            continue
        match = _SOFT_RE.match(text)
        if match:
            rule_id = match.group("rule") or "unknown"
            reason = (match.group("reason") or "").strip()
            quote = (match.group("quote") or "").strip()
        else:
            rule_id = "unknown"
            reason = text
            quote = ""
        if rule_id in FACTUAL_ESCALATE_RULES:
            kind: Literal["expression", "factual", "structural", "preference"] = "factual"
        elif rule_id in AUTO_ELIGIBLE_RULES:
            kind = "expression"
        else:
            kind = "preference"
        pids = locate_quote_paragraphs(chapter, quote, paragraphs=paragraphs)
        status = "detected"
        if kind == "factual":
            status = "escalated"
        elif kind != "expression" or rule_id not in AUTO_ELIGIBLE_RULES:
            status = "retained"
        elif not quote or not pids:
            status = "invalid"
        else:
            status = "eligible"
        out.append(
            EditorialIssue(
                issue_id=issue_id_for(
                    rule_id=rule_id, base_hash=base_hash, quote=quote, index=index
                ),
                rule_id=rule_id,
                quote=quote,
                reason=reason,
                repair_goal=f"消除 {rule_id}：{reason[:120]}",
                kind=kind,
                paragraph_ids=pids,
                status=status,
            )
        )
    return out


def locate_quote_paragraphs(
    chapter: str,
    quote: str,
    *,
    paragraphs: list[Any] | None = None,
) -> list[str]:
    q = (quote or "").strip()
    if not q:
        return []
    paras = paragraphs if paragraphs is not None else split_paragraphs(chapter)
    needle = q[:80]
    hits = [p.paragraph_id for p in paras if needle in p.text or q in p.text]
    if len(hits) == 1:
        return hits
    if len(hits) > 1:
        # 歧义：不授权自动改第一处
        return []
    # 跨段：找覆盖 quote 起点的段落
    at = chapter.find(needle) if len(needle) >= 4 else chapter.find(q)
    if at < 0:
        return []
    for p in paras:
        if p.start <= at < p.end:
            return [p.paragraph_id]
    return []


def _touch_budget_ok(base: str, candidate: str) -> bool:
    if base == candidate:
        return False
    changed = abs(len(candidate) - len(base))
    prefix = 0
    limit = min(len(base), len(candidate))
    while prefix < limit and base[prefix] == candidate[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and base[len(base) - 1 - suffix] == candidate[len(candidate) - 1 - suffix]
    ):
        suffix += 1
    touched = max(len(base) - prefix - suffix, changed)
    if touched > MAX_TOUCH_CHARS:
        return False
    # 短章 15% 过紧；不足 500 字时只看绝对上限。
    if len(base) >= 500 and touched / len(base) > MAX_TOUCH_RATIO:
        return False
    return True


def patch_system_prompt() -> str:
    return (
        "你是网文校稿编辑，只做局部表达修订，不是导演。\n"
        "必须返回 ChapterTextPatch：带上给定 base_content_hash，"
        "只用 paragraph_ids 替换授权段落；未列出的段落由系统原样保留。\n"
        "禁止新增剧情、人物、能力规则、交易结果或改变人物核心选择。\n"
        "禁止空替换；删重复时须把「重复段+紧邻保留段」合并为非空替换。\n"
        "resolved_issue_ids 只列本补丁试图解决的 issue_id。\n"
        "只能使用 payload 中的已批准设定澄清术语，不得发明新设定。"
    )


def verify_system_prompt() -> str:
    return (
        "你是独立复核编辑。对照原稿与候选局部改动，判断目标问题是否解决，"
        "以及是否引入新的事实/因果/人称硬错误。\n"
        "accept=true 仅当：至少一个目标 issue 解决，且 new_hard_problems 为空。\n"
        "不得因文风偏好拒绝或接受。"
    )


async def maybe_editorial_repair(
    *,
    call: ModelCall | None = None,
    mode: EditorialMode | None = None,
    content: str,
    soft_issues: Sequence[str] | None = None,
    structured_issues: Sequence[Any] | None = None,
    input_version: int = 1,
    run_id: str = "",
    work_id: str = "",
    approved_facts: Sequence[str] | None = None,
    power_system: str = "",
    remaining_calls: int | None = None,
) -> EditorialRepairOutcome:
    """生成并验证局部候选。shadow 不写回；auto 仅在复核通过时建议采用。"""
    resolved_mode = mode or resolve_editorial_mode_for_work(work_id or None)
    base = str(content or "")
    base_hash = content_hash(base)
    if resolved_mode == "off" or not base.strip():
        return EditorialRepairOutcome(mode=resolved_mode, base_content_hash=base_hash)

    issues = parse_editor_soft_strings(
        soft_issues or [], chapter=base, base_hash=base_hash
    )
    # 结构化 EditorIssue（若上游传入）优先补充
    if structured_issues:
        for index, item in enumerate(structured_issues):
            rule_id = str(getattr(item, "rule_id", "") or "")
            quote = str(getattr(item, "quote", "") or "")
            reason = str(getattr(item, "reason", "") or "")
            if not rule_id:
                continue
            if any(i.rule_id == rule_id and i.quote == quote for i in issues):
                continue
            pids = locate_quote_paragraphs(base, quote)
            kind: Literal["expression", "factual", "structural", "preference"] = (
                "expression" if rule_id in AUTO_ELIGIBLE_RULES else "preference"
            )
            if rule_id in FACTUAL_ESCALATE_RULES:
                kind = "factual"
            status = (
                "eligible"
                if kind == "expression" and rule_id in AUTO_ELIGIBLE_RULES and pids
                else ("escalated" if kind == "factual" else "retained")
            )
            issues.append(
                EditorialIssue(
                    issue_id=issue_id_for(
                        rule_id=rule_id, base_hash=base_hash, quote=quote, index=100 + index
                    ),
                    rule_id=rule_id,
                    quote=quote,
                    reason=reason,
                    repair_goal=f"消除 {rule_id}",
                    kind=kind,
                    paragraph_ids=pids,
                    status=status,
                )
            )

    eligible = [i for i in issues if i.status == "eligible"][:MAX_ISSUES_PER_ROUND]
    if not eligible:
        return EditorialRepairOutcome(
            mode=resolved_mode,
            base_content_hash=base_hash,
            issues_targeted=[i.issue_id for i in issues],
            notes=["no_eligible_expression_issues"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "SKIPPED"},
        )

    # 预留：补丁 + 复核至少 2 次调用
    if remaining_calls is not None and remaining_calls < 2:
        return EditorialRepairOutcome(
            mode=resolved_mode,
            base_content_hash=base_hash,
            issues_targeted=[i.issue_id for i in eligible],
            notes=["skipped_insufficient_call_budget"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "SKIPPED"},
        )

    if call is None:
        return EditorialRepairOutcome(
            mode=resolved_mode,
            base_content_hash=base_hash,
            issues_targeted=[i.issue_id for i in eligible],
            notes=["no_call_fn"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "SKIPPED"},
        )

    paragraphs = split_paragraphs(base)
    allowed = sorted({pid for issue in eligible for pid in issue.paragraph_ids})
    if not allowed:
        return EditorialRepairOutcome(
            mode=resolved_mode,
            base_content_hash=base_hash,
            notes=["no_paragraph_anchors"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "SKIPPED"},
        )

    patch_payload = {
        "base_content_hash": base_hash,
        "paragraphs": paragraphs_payload(paragraphs),
        "allowed_paragraph_ids": allowed,
        "issues": [i.model_dump() for i in eligible],
        "approved_facts": list(approved_facts or [])[:20],
        "power_system": (power_system or "")[:800],
        "policy_version": EDITORIAL_POLICY_VERSION,
    }
    call_key = (
        f"v{input_version}:editorial_patch:{EDITORIAL_POLICY_VERSION}:"
        f"{base_hash[:12]}:{run_id[:8] if run_id else 'run'}"
    )
    try:
        patch = await call(
            ChapterTextPatch,
            patch_system_prompt(),
            patch_payload,
            "chapter_editorial_patch",
            call_key,
        )
    except Exception as exc:  # noqa: BLE001
        return EditorialRepairOutcome(
            mode=resolved_mode,
            attempted=True,
            base_content_hash=base_hash,
            issues_targeted=[i.issue_id for i in eligible],
            notes=[f"patch_call_failed:{type(exc).__name__}"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "PATCH_PENDING"},
        )

    try:
        candidate = apply_patch(
            base_text=base,
            base_hash=base_hash,
            paragraphs=paragraphs,
            patch_hash=str(patch.base_content_hash),
            replacements=[r.model_dump(mode="json") for r in patch.replacements],
            allowed_paragraph_ids=allowed,
        ).strip()
    except PatchError as exc:
        return EditorialRepairOutcome(
            mode=resolved_mode,
            attempted=True,
            base_content_hash=base_hash,
            issues_targeted=[i.issue_id for i in eligible],
            notes=[f"patch_rejected:{exc}"],
            editorial={
                "issues": [i.model_dump() for i in issues],
                "stage": "RETAINED",
                "patch_error": str(exc),
            },
        )

    if not _touch_budget_ok(base, candidate):
        return EditorialRepairOutcome(
            mode=resolved_mode,
            attempted=True,
            base_content_hash=base_hash,
            candidate_content_hash=content_hash(candidate),
            notes=["patch_exceeds_touch_budget"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "RETAINED"},
        )

    cand_hash = content_hash(candidate)
    verify_key = (
        f"v{input_version}:editorial_verify:{EDITORIAL_POLICY_VERSION}:"
        f"{base_hash[:12]}:{cand_hash[:12]}"
    )
    try:
        verify = await call(
            EditorialVerifyResult,
            verify_system_prompt(),
            {
                "issues": [i.model_dump() for i in eligible],
                "base_excerpt": base[:4000],
                "candidate_excerpt": candidate[:4000],
                "base_content_hash": base_hash,
                "candidate_content_hash": cand_hash,
                "resolved_issue_ids": list(patch.resolved_issue_ids or []),
            },
            "chapter_editorial_verify",
            verify_key,
        )
    except Exception as exc:  # noqa: BLE001
        return EditorialRepairOutcome(
            mode=resolved_mode,
            attempted=True,
            base_content_hash=base_hash,
            candidate_content_hash=cand_hash,
            candidate_content=candidate if resolved_mode == "shadow" else None,
            notes=[f"verify_call_failed:{type(exc).__name__}"],
            editorial={"issues": [i.model_dump() for i in issues], "stage": "VERIFY_PENDING"},
        )

    accept = bool(verify.accept) and not list(verify.new_hard_problems or [])
    editorial_blob = {
        "issues": [i.model_dump() for i in issues],
        "eligible_ids": [i.issue_id for i in eligible],
        "patch": patch.model_dump(mode="json"),
        "verify": verify.model_dump(mode="json"),
        "stage": "VERIFIED" if accept else "RETAINED",
        "candidate_content_hash": cand_hash,
    }

    if resolved_mode == "shadow":
        return EditorialRepairOutcome(
            mode="shadow",
            attempted=True,
            applied=False,
            kept_original=True,
            issues_targeted=[i.issue_id for i in eligible],
            notes=["shadow_candidate_kept_aside"]
            + (["verify_accept"] if accept else ["verify_reject"]),
            base_content_hash=base_hash,
            candidate_content_hash=cand_hash,
            candidate_content=candidate,
            editorial=editorial_blob,
        )

    # auto
    if not accept:
        return EditorialRepairOutcome(
            mode="auto",
            attempted=True,
            applied=False,
            kept_original=True,
            issues_targeted=[i.issue_id for i in eligible],
            notes=["auto_retained_original"],
            base_content_hash=base_hash,
            candidate_content_hash=cand_hash,
            editorial=editorial_blob,
        )

    editorial_blob["stage"] = "APPLIED"
    return EditorialRepairOutcome(
        mode="auto",
        attempted=True,
        applied=True,
        kept_original=False,
        issues_targeted=[i.issue_id for i in eligible],
        notes=["auto_applied"],
        base_content_hash=base_hash,
        candidate_content_hash=cand_hash,
        candidate_content=candidate,
        editorial=editorial_blob,
    )

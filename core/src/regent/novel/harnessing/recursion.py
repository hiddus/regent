"""章内递归：PROPOSE → PROBE → COMPOSE。

CHECK 失败（或追读自评报了问题而这一稿已过硬门）时，改进器提出通用、本书两个
候选配置修改，在同一章纲上各写一稿探针，再由组合器按探针证据决定采纳什么、
交付哪一稿。每一次模型调用都走 ``_call``：经 CallBroker 预留、结算、记账，
一 tick 至多一次。

模型只提议；采纳与交付由程序按规则落定：
- 采纳某个候选，覆盖层就是那个候选的原样修改，不收组合器另写的版本；
- 合并只能取两个候选修改的子集；
- 另一个探针过了硬门时，不许采纳没过的那个；
- 只交付过了硬门的稿子。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application import directing_chapter as chapter
from regent.novel.application.directing_budget import H2_MAX_ROUNDS
from regent.novel.application.directing_calls import _input_version, _save
from regent.novel.application.directing_types import H2_ARCHITECTURE
from regent.novel.harnessing.surface import (
    DEFAULT_KNOB_RANGES,
    HOOK_CONDITIONS,
    SKILL_CONDITIONS,
    SOFT_CHECK_KINDS,
    flatten,
    load_improver_prompt,
    merge_overrides,
    validate_overrides,
)

KINDS = ("generic", "book")
DECISIONS = ("adopt_generic", "adopt_book", "merge", "keep")
DRAFT_CHARS = 2500


class ConfigEdit(BaseModel):
    what: str = Field(description="改了什么（旧值 → 新值）")
    where: str = Field(default="", description="指向参照稿的哪个位置")
    why: str = Field(default="", description="预期解决什么问题")
    evidence: str = Field(default="", description="改进库中支撑这条修改的条目 id；没有留空")


class HarnessCandidate(BaseModel):
    kind: str = Field(description="generic 或 book")
    edits: list[ConfigEdit] = Field(default_factory=list, max_length=3)
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="配置覆盖层：prompts / knobs / skills / soft_checks / hooks / memory",
    )


class HarnessProposal(BaseModel):
    generic: HarnessCandidate = Field(description="针对跨书反复出现的问题，不带本书人名与情节")
    book: HarnessCandidate = Field(description="针对本书类型与读者契约")


class ComposeDecision(BaseModel):
    decision: str = Field(description="adopt_generic / adopt_book / merge / keep")
    deliver: str = Field(
        default="", description="交付哪一稿：generic / book / reference；都不能交付留空"
    )
    observation: str = Field(default="", description="绑定的一条探针观察")
    adopted_overrides: dict[str, Any] = Field(
        default_factory=dict, description="只在 merge 时填：从两个候选修改里取出的子集"
    )
    experience: str = Field(default="", description="一条经验：解决了什么、何时不适用")


def is_h2_arm(production: dict[str, Any]) -> bool:
    return str(production.get("executor") or "") == H2_ARCHITECTURE


def failure_code(fail: str) -> str:
    """硬门文字去掉数字后当失败码，跨章可比。"""
    return re.sub(r"\d+", "", str(fail or "")).strip()[:64]


def arc_issues(production: dict[str, Any]) -> list[str]:
    """这一章动笔前追读自评报的问题。"""
    review = production.get("arc_review")
    if not isinstance(review, dict) or not review:
        return []
    issues = [
        str(item.get("problem") or "").strip()
        for item in review.get("problems") or []
        if isinstance(item, dict) and str(item.get("problem") or "").strip()
    ]
    if review.get("keeps_reading") is False:
        issues.insert(0, "追读自评：读完这几章，读者不会往下追")
    return issues


def round_trigger(production: dict[str, Any], *, fails: list[str]) -> str:
    """该不该开一轮递归：硬门没过开 check 轮；过了但追读自评报问题开一次 arc 轮。"""
    if production.get("h2_stop"):
        return ""
    rounds = int(production.get("h2_round") or 0)
    if rounds >= H2_MAX_ROUNDS:
        return ""
    if fails:
        return "check"
    if rounds == 0 and not production.get("h2_arc_done") and arc_issues(production):
        return "arc"
    return ""


def start_round(
    run: Any, production: dict[str, Any], *, trigger: str, fails: list[str], soft: list[str]
) -> None:
    production["h2_round"] = int(production.get("h2_round") or 0) + 1
    if trigger == "arc":
        production["h2_arc_done"] = True
    production["h2_reference"] = {
        "trigger": trigger,
        "draft": str(run.content or ""),
        "hard_fails": list(fails),
        "soft_issues": list(soft),
        "arc_issues": arc_issues(production) if trigger == "arc" else [],
        "passed": not fails,
    }
    for key in ("h2_candidates", "h2_probes", "h2_probe_index", "h2_library_hits"):
        production.pop(key, None)
    production["phase"] = "PROPOSE"


def _round(production: dict[str, Any]) -> int:
    return int(production.get("h2_round") or 1)


def _chapter_role(ctx: dict[str, Any], production: dict[str, Any]) -> str:
    plan = production.get("chapter_plan") or ctx.get("target_node") or {}
    return "payoff" if isinstance(plan, dict) and plan.get("shows_rule") else "chapter"


async def library_hits(
    session: AsyncSession, *, work: Any, ctx: dict[str, Any], production: dict[str, Any]
) -> list[dict[str, Any]]:
    from regent.novel.harnessing import library

    fails = (production.get("h2_reference") or {}).get("hard_fails") or []
    return await library.search(
        session,
        work_id=getattr(work, "id", None),
        genre_tag=str(getattr(work, "genre", "") or ""),
        chapter_role=_chapter_role(ctx, production),
        failure_code=failure_code(fails[0]) if fails else "",
    )


def _config_view(config: Any) -> dict[str, Any]:
    return {
        "prompts": {key: config.get_prompt(key) for key in ("write", "repair")},
        "knobs": {
            key: {"value": config.get_knob(key), "min": spec["min"], "max": spec["max"]}
            for key, spec in DEFAULT_KNOB_RANGES.items()
        },
        "skills": config.tree.get("skills") or {},
        "soft_checks": config.tree.get("soft_checks") or {},
        "hooks": config.tree.get("hooks") or {},
        "memory": config.get_memory(),
    }


def _surface_rules() -> dict[str, Any]:
    return {
        "editable": [
            "prompts.write",
            "prompts.repair",
            "knobs",
            "skills",
            "soft_checks",
            "hooks",
            "memory",
        ],
        "skill_when_conditions": sorted(SKILL_CONDITIONS),
        "hook_conditions": {key: list(value) for key, value in HOOK_CONDITIONS.items()},
        "soft_check_kinds": sorted(SOFT_CHECK_KINDS),
    }


def _plan_view(ctx: dict[str, Any], production: dict[str, Any]) -> dict[str, Any]:
    plan = production.get("chapter_plan") or ctx.get("target_node") or {}
    plan = plan if isinstance(plan, dict) else {}
    return {
        "chapter_no": int(ctx.get("chapter_no") or 1),
        "pressure": str(plan.get("pressure") or ""),
        "choice": str(plan.get("choice") or ""),
        "result": str(plan.get("result") or ""),
    }


async def propose_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: Any,
    run: Any,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> None:
    ctx = run.generation_context or {}
    config = chapter.h2_config(ctx, production)
    hits = await library_hits(session, work=work, ctx=ctx, production=production)
    reference = production.get("h2_reference") or {}
    request = {
        "trigger": reference.get("trigger") or "check",
        "reference_draft": str(reference.get("draft") or "")[:DRAFT_CHARS],
        "check_fails": list(reference.get("hard_fails") or []),
        "soft_issues": list(reference.get("soft_issues") or []),
        "arc_issues": list(reference.get("arc_issues") or []),
        "chapter": _plan_view(ctx, production),
        "current_config": _config_view(config),
        "surface_rules": _surface_rules(),
        "earlier_rounds": list(production.get("h2_history") or []),
        "library_hits": hits,
        "cast_names": list(cast),
    }
    round_no = _round(production)
    proposal = await chapter._call(
        session,
        provider,
        work,
        run,
        production,
        HarnessProposal,
        load_improver_prompt("propose"),
        request,
        "h2_propose",
        f"v{_input_version(run)}:h2:propose:r{round_no}",
        temperature=0.3,
    )
    production = run.generation_context["production"]
    candidates: list[dict[str, Any]] = []
    for kind in KINDS:
        candidate: HarnessCandidate = getattr(proposal, kind)
        overrides, rejected = validate_overrides(
            candidate.overrides, config, cast_names=list(cast), generic=kind == "generic"
        )
        candidates.append(
            {
                "kind": kind,
                "edits": [edit.model_dump() for edit in candidate.edits],
                "overrides": overrides,
                "rejected": rejected,
            }
        )
    production["h2_candidates"] = candidates
    production["h2_library_hits"] = [hit["id"] for hit in hits]
    production["h2_probes"] = []
    production["h2_probe_index"] = 0
    production["phase"] = "PROBE"
    _save(run, production)


async def probe_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: Any,
    run: Any,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> None:
    ctx = run.generation_context or {}
    candidates = list(production.get("h2_candidates") or [])
    index = int(production.get("h2_probe_index") or 0)
    if index >= len(candidates):
        production["phase"] = "COMPOSE"
        _save(run, production)
        return
    candidate = candidates[index]
    kind = str(candidate.get("kind") or KINDS[index])
    if not candidate.get("overrides"):
        # 配置没变，探针只是同配置重抽一次：不花这次调用。
        result = {
            "kind": kind,
            "skipped": True,
            "passed": False,
            "hard_fails": [],
            "soft_issues": [],
        }
    else:
        config = chapter.h2_config(ctx, production, extra=candidate["overrides"])
        written = await chapter._call(
            session,
            provider,
            work,
            run,
            production,
            chapter.ChapterProse,
            chapter.h2_writer_system(ctx, cast, config, production),
            chapter.h2_write_payload(ctx, cast, config),
            "h2_probe",
            f"v{_input_version(run)}:h2:probe:r{_round(production)}:{kind}",
        )
        production = run.generation_context["production"]
        content = chapter.settle_chapter_text(written.content, chapter.previous_chapter_text(ctx))
        fails = chapter.check_chapter(
            content, ctx=ctx, cast=cast, title=str(getattr(run, "title", "") or "")
        )
        soft = chapter.name_warnings(content, cast)
        soft += [item for item in chapter.h2_soft_issues(content, ctx, config) if item not in soft]
        result = {
            "kind": kind,
            "skipped": False,
            "content": content,
            "hard_fails": fails,
            "soft_issues": soft,
            "passed": not fails,
            "config_hash": config.content_hash,
        }
    production.setdefault("h2_probes", []).append(result)
    production["h2_probe_index"] = index + 1
    if index + 1 >= len(candidates):
        production["phase"] = "COMPOSE"
    _save(run, production)


def _best(passed: list[str], probes: dict[str, dict[str, Any]]) -> str:
    return min(
        passed, key=lambda kind: (len(probes[kind].get("soft_issues") or []), KINDS.index(kind))
    )


def _leaves(overrides: dict[str, Any]) -> set[tuple[tuple[str, ...], str]]:
    return {
        (path, json.dumps(value, ensure_ascii=False, sort_keys=True))
        for path, value in flatten(overrides).items()
    }


def settle_compose(
    decision: ComposeDecision,
    candidates: list[dict[str, Any]],
    probes: list[dict[str, Any]],
    *,
    reference_passed: bool,
) -> dict[str, Any]:
    """把组合器的决定落成程序认可的采纳与交付。"""
    by_kind = {str(c.get("kind")): c for c in candidates}
    probe_of = {str(p.get("kind")): p for p in probes}
    passed = [kind for kind in KINDS if (probe_of.get(kind) or {}).get("passed")]
    corrections: list[str] = []
    choice = decision.decision if decision.decision in DECISIONS else "keep"
    if choice != decision.decision:
        corrections.append(f"未知决定 {decision.decision!r}，按 keep 处理")
    adopted: dict[str, Any] = {}
    if choice.startswith("adopt_"):
        kind = choice.removeprefix("adopt_")
        if kind not in passed and passed:
            other = _best(passed, probe_of)
            corrections.append(f"{kind} 探针没过硬门而 {other} 过了，改为采纳 {other}")
            kind = other
            choice = f"adopt_{kind}"
        adopted = deepcopy((by_kind.get(kind) or {}).get("overrides") or {})
    elif choice == "merge":
        union: set[tuple[tuple[str, ...], str]] = set()
        for candidate in candidates:
            union |= _leaves(candidate.get("overrides") or {})
        merged = decision.adopted_overrides or {}
        if merged and _leaves(merged) <= union:
            adopted = deepcopy(merged)
        else:
            corrections.append("合并结果不是两个候选修改的子集")
            if passed:
                kind = _best(passed, probe_of)
                choice = f"adopt_{kind}"
                adopted = deepcopy((by_kind.get(kind) or {}).get("overrides") or {})
            else:
                choice = "keep"
    options = passed + (["reference"] if reference_passed else [])
    deliver = decision.deliver if decision.deliver in options else ""
    if not deliver:
        if choice.startswith("adopt_") and choice.removeprefix("adopt_") in passed:
            deliver = choice.removeprefix("adopt_")
        elif passed:
            deliver = _best(passed, probe_of)
        elif reference_passed:
            deliver = "reference"
        else:
            deliver = "none"
        if decision.deliver and decision.deliver != deliver:
            corrections.append(f"不能交付 {decision.deliver}，改为 {deliver}")
    return {
        "decision": choice,
        "adopted_overrides": adopted,
        "deliver": deliver,
        "corrections": corrections,
    }


def _cited(candidate: dict[str, Any], hits: list[str]) -> list[str]:
    evidence = " ".join(str(edit.get("evidence") or "") for edit in candidate.get("edits") or [])
    return [hit for hit in hits if hit and hit in evidence]


async def compose_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: Any,
    run: Any,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> None:
    ctx = run.generation_context or {}
    reference = production.get("h2_reference") or {}
    candidates = list(production.get("h2_candidates") or [])
    probes = list(production.get("h2_probes") or [])
    probe_of = {str(p.get("kind")): p for p in probes}
    request = {
        "trigger": reference.get("trigger") or "check",
        "reference": {
            "draft": str(reference.get("draft") or "")[:DRAFT_CHARS],
            "hard_fails": list(reference.get("hard_fails") or []),
            "soft_issues": list(reference.get("soft_issues") or []),
            "arc_issues": list(reference.get("arc_issues") or []),
            "passed": bool(reference.get("passed")),
        },
        "candidates": [
            {
                "kind": c.get("kind"),
                "edits": c.get("edits") or [],
                "overrides": c.get("overrides") or {},
                "rejected": c.get("rejected") or [],
                "probe": {
                    key: (probe_of.get(str(c.get("kind"))) or {}).get(key)
                    for key in ("skipped", "hard_fails", "soft_issues", "passed")
                }
                | {
                    "content": str((probe_of.get(str(c.get("kind"))) or {}).get("content") or "")[
                        :DRAFT_CHARS
                    ]
                },
            }
            for c in candidates
        ],
        "deliver_options": [k for k in KINDS if (probe_of.get(k) or {}).get("passed")]
        + (["reference"] if reference.get("passed") else []),
        "library_hits": list(production.get("h2_library_hits") or []),
    }
    round_no = _round(production)
    decision = await chapter._call(
        session,
        provider,
        work,
        run,
        production,
        ComposeDecision,
        load_improver_prompt("compose"),
        request,
        "h2_compose",
        f"v{_input_version(run)}:h2:compose:r{round_no}",
        temperature=0.3,
    )
    production = run.generation_context["production"]
    settled = settle_compose(
        decision, candidates, probes, reference_passed=bool(reference.get("passed"))
    )
    deliver = settled["deliver"]
    by_kind = {str(c.get("kind")): c for c in candidates}
    hard_fails = list(reference.get("hard_fails") or [])
    lesson = {
        "round": round_no,
        "trigger": reference.get("trigger") or "check",
        "observation": decision.observation,
        "experience": decision.experience,
        "failure_codes": [failure_code(item) for item in hard_fails],
    }
    if deliver in KINDS:
        production["h2_overrides"] = merge_overrides(
            production.get("h2_overrides"), by_kind[deliver]["overrides"]
        )
        chapter._store_prose(run, production, str(probe_of[deliver].get("content") or ""))
        production["h2_delivered_from"] = "probe"
        production["h2_lesson"] = lesson | {
            "kind": deliver,
            "overrides": by_kind[deliver]["overrides"],
            "cited": _cited(by_kind[deliver], list(production.get("h2_library_hits") or [])),
        }
        production["phase"] = "CHECK"
    elif deliver == "reference":
        production["phase"] = "CHECK"
    elif settled["decision"] == "keep" or not settled["adopted_overrides"]:
        production["h2_stop"] = True
        production["phase"] = "CHECK"
    else:
        production["h2_overrides"] = merge_overrides(
            production.get("h2_overrides"), settled["adopted_overrides"]
        )
        production["h2_delivered_from"] = "successor"
        kind = settled["decision"].removeprefix("adopt_")
        production["h2_lesson"] = lesson | {
            "kind": kind,
            "overrides": settled["adopted_overrides"],
            "cited": _cited(by_kind.get(kind) or {}, list(production.get("h2_library_hits") or [])),
        }
        production["phase"] = "WRITE"
    production["h2_effective_hash"] = chapter.h2_config(ctx, production).content_hash
    production.setdefault("h2_history", []).append(
        {
            "round": round_no,
            "trigger": lesson["trigger"],
            "decision": settled["decision"],
            "deliver": deliver,
            "corrections": settled["corrections"],
            "observation": decision.observation,
            "probes": {k: bool((probe_of.get(k) or {}).get("passed")) for k in KINDS},
            "edits": {
                str(c.get("kind")): [e.get("what") for e in c.get("edits") or []]
                for c in candidates
            },
        }
    )
    _save(run, production)

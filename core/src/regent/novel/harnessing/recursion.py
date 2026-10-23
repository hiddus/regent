"""章内递归：PROPOSE → PROBE → COMPOSE →（采纳通用条目时）SCREEN。

CHECK 失败（或追读自评报了问题而这一稿已过硬门）时，改进器提出通用、本书两个
候选配置修改，在同一章纲上各写一稿探针，再由组合器按探针证据决定采纳什么、
交付哪一稿。每一次模型调用都走 ``_call``：经 CallBroker 预留、结算、记账，
一 tick 至多一次。

模型只提议；采纳与交付由程序按规则落定：
- 采纳某个候选，覆盖层就是那个候选的原样修改，不收组合器另写的版本；
- 合并只能取两个候选修改的子集；
- 另一个探针过了硬门时，不许采纳没过的那个；
- 只交付过了硬门的稿子；
- 每条配置修改都得有一条 edits 认账并指向参照稿的位置，没人认的丢掉；
- 通用条目入改进库前过一次专有性筛查，判不定就只留在本书。
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
    LEGACY_PROMPTS,
    SKILL_CONDITIONS,
    SOFT_CHECK_KINDS,
    book_terms,
    flatten,
    load_improver_prompt,
    merge_overrides,
    upgrade_prompts,
    validate_overrides,
)

KINDS = ("generic", "book")
DECISIONS = ("adopt_generic", "adopt_book", "merge", "keep")
DRAFT_CHARS = 3000


def draft_view(text: Any, *, limit: int = DRAFT_CHARS, tail: int = 600) -> str:
    """给改进器看的稿子：超长时保头也保尾。

    章末结果是判断这一稿好不好的关键，只截前面一段等于让提案器和组合器看不到
    结局，提出的约束也就无从对准真正被砍掉的那一段。
    """
    body = str(text or "")
    if len(body) <= limit:
        return body
    return f"{body[: max(0, limit - tail)]}\n…（中略）…\n{body[-tail:]}"


class ConfigEdit(BaseModel):
    what: str = Field(description="改了什么（旧值 → 新值）")
    path: str = Field(
        default="",
        description="这处修改对应 overrides 里的哪个键，例如 knobs.previous_chapter_tail",
    )
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
    # 模型常只给一个候选；缺的那个当作“配置没变”，探针跳过，不让整次提案作废。
    generic: HarnessCandidate = Field(
        default_factory=lambda: HarnessCandidate(kind="generic"),
        description="针对跨书反复出现的问题，不带本书人名与情节",
    )
    book: HarnessCandidate = Field(
        default_factory=lambda: HarnessCandidate(kind="book"),
        description="针对本书类型与读者契约",
    )


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
    """该不该开一轮递归：硬门没过开 check 轮；过了但追读自评判「不追」开一次 arc 轮。

    自评判「会追」时也会列问题；按「有问题就开」会让每个自评章都多花一轮、
    交付重写稿，而追读自评分不出重写稿是否更好。
    """
    if production.get("h2_stop"):
        return ""
    rounds = int(production.get("h2_round") or 0)
    if rounds >= H2_MAX_ROUNDS:
        return ""
    if fails:
        return "check"
    review = production.get("arc_review")
    lost = isinstance(review, dict) and review.get("keeps_reading") is False
    if rounds == 0 and not production.get("h2_arc_done") and lost:
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
        "prompts": {
            key: config.get_prompt(key) for key in ("write", "repair", "write_extra", "repair_extra")
        },
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
            "prompts.write_extra",
            "prompts.repair_extra",
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


def _assign(tree: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = tree
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


_QUOTE_PAIRS = (("「", "」"), ("“", "”"), ("『", "』"), ("‘", "’"), ('"', '"'))


def _quoted(text: str) -> list[str]:
    spans: list[str] = []
    for open_, close in _QUOTE_PAIRS:
        rest = text
        while open_ in rest:
            _, _, tail = rest.partition(open_)
            span, _, rest = tail.partition(close)
            if not span:
                break
            spans.append(span)
    return [span.strip() for span in spans if len(span.strip()) >= 4]


def _squash(text: str) -> str:
    return "".join((text or "").split())


def _claims(path: tuple[str, ...], edits: list[dict[str, Any]], reference: str = "") -> bool:
    """这条修改有没有一条 edits 认账：说清改了什么，并指向参照稿的位置。

    认账优先看 ``path`` 是不是这一叶的前缀（``skills.chapter_opening`` 认下它底下
    的 instruction 与 mode），其次才退回到 ``what`` 里出现了键名。没写 ``where``
    的修改不算认账：指不出参照稿的位置，就是没说清这处改动针对什么。

    ``where`` 里引了原文的，引文必须真的在参照稿里——「指向参照稿具体位置」原先
    只要求这一栏非空，随便写几个字就算数。没引原文的维持原样：无从核对就不加拒收。
    """
    dotted = ".".join(path)
    leaf = path[-1]
    haystack = _squash(reference)
    for edit in edits:
        where = str(edit.get("where") or "").strip()
        if not where:
            continue
        quotes = _quoted(where)
        if quotes and haystack and not any(_squash(q) in haystack for q in quotes):
            continue
        claimed = str(edit.get("path") or "").strip()
        claimed = _UPGRADED_PATHS.get(claimed, claimed)
        if claimed and (dotted == claimed or dotted.startswith(f"{claimed}.")):
            return True
        what = str(edit.get("what") or "")
        if dotted in what or leaf in what:
            return True
    return False


# 模型整段改写的 prompts.write 已被 upgrade_prompts 转成 write_extra，认账跟着转。
_UPGRADED_PATHS = {f"prompts.{old}": f"prompts.{new}" for old, new in LEGACY_PROMPTS.items()}


def back_edits(
    edits: list[dict[str, Any]], overrides: dict[str, Any], reference: str = ""
) -> tuple[dict[str, Any], list[str]]:
    """只留下有 edits 认账的修改，返回 (留下的覆盖层, 丢弃理由)。

    第 4 章的 generic 候选把 ``soft_check_repetition_ngram`` 改成 60，三条 edits 没
    一条提到它、``evidence`` 也全是空的。这样的改动一旦进了改进库，就等于把一次
    没解释过的调参当成经验传给别的书，所以这里按叶子逐项对账，没人认的丢掉。

    ``reference`` 是参照稿全文（不是给模型看的那份截断视图），用来核 ``where`` 里
    引的原文是不是真在那一稿里。
    """
    if not overrides:
        return {}, []
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for path, value in flatten(overrides).items():
        if _claims(path, edits, reference):
            _assign(kept, path, value)
        else:
            dropped.append(".".join(path))
    if not dropped:
        return kept, []
    return kept, [f"没有 edits 认账（或 edits 没指向参照稿位置）的修改已丢弃：{'、'.join(dropped)}"]


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
    terms = book_terms(ctx, cast)
    request = {
        "trigger": reference.get("trigger") or "check",
        "reference_draft": draft_view(reference.get("draft")),
        "check_fails": list(reference.get("hard_fails") or []),
        "soft_issues": list(reference.get("soft_issues") or []),
        "arc_issues": list(reference.get("arc_issues") or []),
        "chapter": _plan_view(ctx, production),
        "current_config": _config_view(config),
        "surface_rules": _surface_rules(),
        "earlier_rounds": list(production.get("h2_history") or []),
        "library_hits": hits,
        "book_terms": terms,
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
        edits = [edit.model_dump() for edit in candidate.edits]
        overrides, rejected = validate_overrides(
            upgrade_prompts(candidate.overrides, current=config),
            config,
            book_terms=terms,
            generic=kind == "generic",
        )
        overrides, unclaimed = back_edits(edits, overrides, str(reference.get("draft") or ""))
        candidates.append(
            {
                "kind": kind,
                "edits": edits,
                "overrides": overrides,
                "rejected": rejected + unclaimed,
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
        # 探针不砍尾：砍完再比会让组合器看不到章末结果——第 4 章它比较的正是两份
        # 被砍掉结局的稿子。不删段的运行按超长判失败，超长的探针不能交付；
        # 删段的老运行不按超长判，交给交付前那次删段。
        content = chapter.dedupe_chapter_text(
            written.content, chapter.previous_chapter_text(ctx)
        )
        fails = chapter.check_chapter(
            content,
            ctx=ctx,
            cast=cast,
            title=str(getattr(run, "title", "") or ""),
            ignore_max_length=chapter.cuts_overlong(ctx, production),
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


class GenericScreen(BaseModel):
    book_specific: bool = Field(
        description="这条通用经验是否其实依赖本书专有的物件、设定或情节"
    )
    reason: str = Field(default="", description="依赖了本书的什么；不依赖就留空")
    genre_only: bool = Field(
        default=False, description="不依赖本书，但只对 genre 这一类型的书成立，换了类型就不适用"
    )


def finish_round(run: Any, production: dict[str, Any]) -> None:
    """把这一轮的组合结果落定：叠覆盖层、交付稿子、记经验、写历史。

    组合器说完就落定，和「先过一次专有性筛查再落定」共用这一段，两处的采纳与
    交付规则必须是同一份，否则筛查会变成第二条采纳路径。
    """
    pending = dict(production.pop("h2_pending") or {})
    ctx = run.generation_context or {}
    deliver = str(pending.get("deliver") or "")
    round_no = int(pending.get("round") or _round(production))
    overrides = pending.get("overrides") or {}
    kind = str(pending.get("kind") or "")
    lesson = {
        key: pending.get(key)
        for key in ("round", "trigger", "observation", "experience", "failure_codes")
    }
    if kind:
        lesson |= {
            "kind": kind,
            "overrides": overrides,
            "cited": list(pending.get("cited") or []),
            # 采纳的是哪几处修改、指向参照稿哪里、凭什么：库里只留 config_diff 与
            # 经验一句话，事后看不出这次采纳的理由，也就没法审。
            "edits": pending.get("edits") or {},
        }
        screen = production.get("h2_screen")
        if isinstance(screen, dict) and screen:
            # 降级为本书条目时把理由留在经验里：库里只有 scope 一列，看不出为什么。
            lesson["screen"] = screen
    if overrides and pending.get("delivered_from"):
        production["h2_overrides"] = merge_overrides(production.get("h2_overrides"), overrides)
    if deliver in KINDS:
        # 交付的探针稿同样经 accept_draft：删段的运行超长先删段，砍尾就会砍掉章末结果。
        chapter.accept_draft(
            run,
            production,
            ctx=ctx,
            draft=str(pending.get("draft") or ""),
            tag=f"deliver:r{round_no}:{deliver}",
        )
        production["h2_delivered_from"] = "probe"
        production["h2_lesson"] = lesson
    elif deliver == "reference":
        production["phase"] = "CHECK"
    elif kind:
        production["h2_delivered_from"] = "successor"
        production["h2_lesson"] = lesson
        production["phase"] = "WRITE"
    else:
        production["h2_stop"] = True
        production["phase"] = "CHECK"
    production["h2_effective_hash"] = chapter.h2_config(ctx, production).content_hash
    probe_of = {str(p.get("kind")): p for p in production.get("h2_probes") or []}
    production.setdefault("h2_history", []).append(
        {
            "round": round_no,
            "trigger": pending.get("trigger") or "check",
            "decision": pending.get("decision") or "keep",
            "deliver": deliver,
            "corrections": list(pending.get("corrections") or []),
            "observation": pending.get("observation") or "",
            "probes": {k: bool((probe_of.get(k) or {}).get("passed")) for k in KINDS},
            "edits": {
                str(c.get("kind")): [e.get("what") for e in c.get("edits") or []]
                for c in production.get("h2_candidates") or []
            },
        }
    )


async def screen_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: Any,
    run: Any,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> None:
    """通用条目入库前判它到底通不通用。

    专有名词筛查看不见改写过的情节说法：第 4 章那条 generic 条目经验写的是「同字
    不同齿距」、提示词补的是「上一章以声音形式出现的警告」，一个人名都没带，可是
    换个物件的书根本用不上，而它当时的 scope 是 global，对别的书可见。判不出来
    或这次调用没跑成，都按「是本书专有」处理——筛查是可选的，不能因为它把一章
    已经过硬门的稿子拖成失败。

    只吞「钱可能花了、结果不确定」的 ``CallUnknown``（第 4 章 PROPOSE 当时就是
    CALL_UNKNOWN）。``CallInFlight``/``StaleLease`` 说的是另一个 worker 正持有这一章，
    这时降级落定就是旧 worker 在替持有者写结论，必须上抛让持有者接着跑。幂等冲突、
    货币上限、程序缺陷同样上抛：兜住等于把缺陷藏进一条 scope=work 的条目里。
    """
    from regent.novel.application.production import CallUnknown
    from regent.novel.domain.errors import BudgetExhausted

    ctx = run.generation_context or {}
    pending = production.get("h2_pending") or {}
    reference = production.get("h2_reference") or {}
    request = {
        "experience": str(pending.get("experience") or ""),
        "observation": str(pending.get("observation") or ""),
        "overrides": pending.get("overrides") or {},
        "book_terms": book_terms(ctx, cast),
        "genre": str(getattr(work, "genre", "") or ""),
        "chapter": _plan_view(ctx, production),
        "reference_draft": draft_view(reference.get("draft")),
    }
    verdict: GenericScreen | None = None
    missed = ""
    try:
        verdict = await chapter._call(
            session,
            provider,
            work,
            run,
            production,
            GenericScreen,
            load_improver_prompt("screen"),
            request,
            "h2_screen",
            f"v{_input_version(run)}:h2:screen:r{_round(production)}",
            temperature=0.0,
        )
    except BudgetExhausted:
        missed = "预算耗尽，筛查没跑"
    except CallUnknown as exc:
        missed = f"筛查调用没跑成（{type(exc).__name__}），按本书专有条目处理"
    production = run.generation_context["production"]
    if verdict is None:
        production["h2_screen"] = {
            "book_specific": True,
            "reason": missed or "筛查没跑成，按本书专有条目处理",
        }
    else:
        production["h2_screen"] = {
            "book_specific": bool(verdict.book_specific),
            "genre_only": bool(verdict.genre_only),
            "reason": str(verdict.reason or "")[:200],
        }
    finish_round(run, production)
    _save(run, production)


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
            "draft": draft_view(reference.get("draft")),
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
                | {"content": draft_view((probe_of.get(str(c.get("kind"))) or {}).get("content"))},
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
    hits = list(production.get("h2_library_hits") or [])
    lesson = {
        "round": round_no,
        "trigger": reference.get("trigger") or "check",
        "observation": decision.observation,
        "experience": decision.experience,
        "failure_codes": [failure_code(item) for item in reference.get("hard_fails") or []],
    }
    if deliver in KINDS:
        kind = deliver
        overrides = by_kind[deliver]["overrides"]
        cited = _cited(by_kind[deliver], hits)
        draft = str((probe_of.get(deliver) or {}).get("content") or "")
        delivered_from = "probe"
    elif deliver == "reference" or settled["decision"] == "keep" or not settled["adopted_overrides"]:
        # 交付参照稿时不采纳任何修改：这一稿没证明那些修改有用。
        kind, overrides, cited, draft, delivered_from = "", {}, [], "", ""
    else:
        kind = settled["decision"].removeprefix("adopt_")
        overrides = settled["adopted_overrides"]
        cited = _cited(by_kind.get(kind) or {}, hits)
        draft, delivered_from = "", "successor"
    kept_kinds = list(KINDS) if kind == "merge" else [kind] if kind in KINDS else []
    production["h2_pending"] = lesson | {
        "decision": settled["decision"],
        "deliver": deliver,
        "corrections": settled["corrections"],
        "kind": kind,
        "overrides": overrides,
        "cited": cited,
        "draft": draft,
        "delivered_from": delivered_from,
        "edits": {k: (by_kind.get(k) or {}).get("edits") or [] for k in kept_kinds},
    }
    production.pop("h2_screen", None)
    if kind == "generic":
        production["phase"] = "SCREEN"
    else:
        finish_round(run, production)
    _save(run, production)

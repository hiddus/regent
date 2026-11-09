"""最终稿统一复验（R2）：在提交前用一次集中调用把硬错误找出来。

与其它审校的分工（刻意不重叠）：

| 现有 | 看什么 | 能不能拦 |
|---|---|---|
| check_chapter | 字数、抄上一章、同章复读 | 能，确定性 |
| VALIDATE | 正文 ↔ 结算事件 | 能，确定性 |
| editor_audit | 通顺、术语、读者可读 | 不能（全部降为 soft） |
| 本模块 | 错人、错物、重复消耗、事件重演、地点跳变、断句、时间冲突 | 能，但必须有正文原文 |

三条边界：

1. **引文必须落在正文里。** 模型给的每条错都要在正文里指出原句；指不出来的丢掉
   并留痕。否则「我觉得这里人名不对」就能否掉一整章，一批跑下来全是不可解释的拦截。
2. **一次审一份稿。** 删段、探针换稿、重写都会换 prose_hash；旧审查不覆盖新稿，
   入正史前会按 hash 核对，对不上就阻断而不是放行。
3. **不可用要留档，不要冒充通过。** 调用失败重试一次；仍失败记 ``unavailable``，
   这一章照常入正史但在验收证据里明确写着「未复验」，不写成「已通过」。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_calls import _call, _input_version
from regent.novel.application.production import CallError
from regent.novel.domain import final_audit as audit
from regent.novel.domain import prose_layout
from regent.novel.domain.errors import BudgetExhausted
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel

FINAL_AUDIT_SYSTEM = (
    "你是这本书的责任编辑，刚拿到本章定稿。读一遍，记下那些会让读者认为作者记错了的地方："
    "谁是谁、东西在谁手上、已经用掉的还能不能用、什么时候、在哪儿、前后因果对不对得上。\n"
    "不评价文风、节奏、详略、好不好看，也不改稿。\n"
    "每条 issue：rule_id 从 identity_switch / possession_error / consumption_repeat / "
    "event_repeat / place_jump / seam_break / timeline_conflict 里选最接近的一个；"
    "quote 从本章正文原样连续复制一句，不要改写、不要拼接；reason 写你读到这句时觉得哪里对不上。\n"
    "如果你依据的是本章之外、或本章别处的另一句原文，把它逐字复制进 fact；没有就留空。\n"
    "拿不准的不要报。没有这类问题就返回空列表。"
)


class FinalIssueOut(BaseModel):
    rule_id: str = Field(description="七类规则之一")
    quote: str = Field(min_length=4, description="本章正文里的连续原文，原样复制")
    reason: str = Field(min_length=6, description="为什么这是硬错误")
    fact: str = Field(
        default="",
        description="与它冲突的那一句原文（事实/上一章/本章另一处），逐字复制；四类状态错必填",
    )


class FinalAuditResult(BaseModel):
    issues: list[FinalIssueOut] = Field(default_factory=list, max_length=5)


CONFIRM_SYSTEM = (
    "你在复核一条终审意见。先不要判对错。\n"
    "第一步：读给你的这段正文，用自己的话写清楚这里发生了什么——谁在场、东西在谁手上、"
    "什么时候、前后接不接得上。\n"
    "第二步：再看被指出的那一处，回答：按这段正文读下来，读者会不会认为作者记错了。\n"
    "verdict 只能是 consistent（读下来没问题）、conflict（确实对不上）、unclear（这段还不够判断）。\n"
    "这段正文不足以判断时，填 need_chars 说明还要看多少字前后文，不要靠猜下结论。\n"
    "notes_so_far 是你前几轮读到的结论，在它上面继续，不要推翻已经确认的事。"
)

# 复核是迭代的：先复述、再判断；看不清就要更多上下文再来一轮。轮次上限只是防死循环，
# 不是判据——判据只有一条「按这段正文读下来，读者会不会认为作者记错了」，对版式断裂、
# 跨章重演、状态错都适用，不需要按规则分派。
VERIFY_ROUNDS = 3
VERIFY_WINDOW = 700
VERIFY_WINDOW_MAX = 6000


class VerdictOut(BaseModel):
    reading: str = Field(min_length=8, description="用自己的话复述这段正文里发生了什么")
    verdict: Literal["consistent", "conflict", "unclear"] = Field(
        description="consistent=读下来没问题；conflict=确实对不上；unclear=这段还不够判断"
    )
    why: str = Field(min_length=4, description="一句话说明依据")
    need_chars: int = Field(
        default=0, ge=0, le=8000, description="还想多看多少字前后文；0 表示这段够了"
    )


def _digest(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _fact_lines(ctx: dict[str, Any], limit: int = 30) -> list[str]:
    out: list[str] = []
    for fact in ctx.get("canon") or []:
        if not isinstance(fact, dict):
            continue
        statement = str(fact.get("statement") or fact.get("content") or "").strip()
        if statement:
            out.append(statement[:160])
        if len(out) >= limit:
            break
    return out


def _cast_lines(cast: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name, spec in (cast or {}).items():
        identity = spec.get("identity") if isinstance(spec, dict) else None
        role = ""
        if isinstance(identity, dict):
            role = str(identity.get("role") or identity.get("title") or "")
        out.append({"name": str(name), "role": role[:60]})
    return out


def build_payload(
    *,
    ctx: dict[str, Any],
    production: dict[str, Any],
    cast: dict[str, Any],
    content: str,
) -> dict[str, Any]:
    """一次审一稿的入参：只放判断硬错误必需的东西，不放全书。"""
    plan = production.get("chapter_plan") or ctx.get("target_node") or {}
    plan = plan if isinstance(plan, dict) else {}
    previous = ""
    chapters = ctx.get("recent_chapters") or []
    if chapters and isinstance(chapters[0], dict):
        previous = str(chapters[0].get("content") or "")[-700:]
    return {
        "chapter_no": int(ctx.get("chapter_no") or 1),
        "plan": {
            "choice": str(plan.get("choice") or ""),
            "result": str(plan.get("result") or ""),
            "place": str(plan.get("place") or ""),
        },
        "cast": _cast_lines(cast),
        "known_facts": _fact_lines(ctx),
        "previous_chapter_tail": previous,
        "chapter": content,
    }


def _store(
    production: dict[str, Any],
    *,
    digest: str,
    status: str,
    issues: list[audit.AuditIssue],
    dropped: list[str],
) -> None:
    production["final_audit"] = {
        "hash": digest,
        "status": status,
        # 放行口径：record=只留档（默认），block=阻断入正史。见 domain.canon_blockers。
        "mode": str(production.get("final_audit_mode") or "record"),
        "issues": [item.as_payload() for item in issues],
        "dropped": dropped,
    }


def _layout_payload(content: str) -> dict[str, Any]:
    """版式检查的留档。纯函数、零成本，所以每次终审都重算，不吃缓存。

    和模型结论分开放的第二个理由：它的 `hash` 一旦对不上当前正文，说明这处
    记录描述的是另一份稿——那时它也连参考价值都没有，不能拿来给新稿背书。
    """
    defects = prose_layout.check_layout(content)
    return {
        "hash": _digest(content),
        "defects": [item.as_payload() for item in defects],
        "blocking": len(prose_layout.blocking_defects(defects)),
    }


def _cached(production: dict[str, Any], digest: str) -> list[str] | None:
    """同一 prose_hash 的现成结论；没有结论（含待重试）返回 None。"""
    stored = production.get("final_audit") or {}
    if str(stored.get("hash") or "") != digest:
        return None
    if "items" not in stored:
        return None
    return [str(item) for item in stored.get("items") or []]


Ask = Callable[[dict[str, Any], int], Awaitable[VerdictOut]]


async def verify_loop(
    ask: Ask,
    *,
    issue: audit.AuditIssue,
    content: str,
) -> tuple[bool, str]:
    """迭代复核：复述一遍、给出判断，看不清就要更多上下文再来一轮。

    判据只有一句「按这段正文读下来，读者会不会认为作者记错了」。它不按规则分派，
    所以版式断裂（引号没闭合、两句黏连）、跨章重演（同一场又发生一次）与状态错
    走的是同一条路——上一版问的是「这两句是否不可能同时为真」，那对前两类是
    范畴错误，等于把这两类规则关掉。

    轮次上限只防死循环。每轮把上一轮的复述带回去（累积，不覆盖），模型不必重读
    全部、也不会在两轮之间自相矛盾。
    """
    notes = ""
    window = VERIFY_WINDOW
    last = ""
    spot = content.find(issue.quote)
    for round_no in range(VERIFY_ROUNDS):
        if spot >= 0:
            excerpt = content[
                max(0, spot - window): spot + len(issue.quote) + window
            ]
        else:
            excerpt = content[: window * 2]
        payload = {
            "quote": issue.quote,
            "claim": issue.reason,
            "conflict": issue.fact,
            "chapter_excerpt": excerpt,
            "notes_so_far": notes,
        }
        out = await ask(payload, round_no)
        last = str(out.why or "")
        if out.reading:
            notes = (notes + "\n" + str(out.reading)).strip()
        if out.verdict == "conflict":
            return True, f"第{round_no + 1}轮：{last}"
        if out.need_chars <= 0:
            return False, f"第{round_no + 1}轮判为{out.verdict}：{last}"
        window = min(max(window * 2, int(out.need_chars)), VERIFY_WINDOW_MAX)
    return False, f"{VERIFY_ROUNDS} 轮后仍未看清，按不成立处理：{last}"


async def _confirm(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    issue: audit.AuditIssue,
    content: str,
) -> tuple[bool, str]:
    """把迭代复核对接到调用账本。复核不可用时按「不成立」处理并留档。

    宁可放过一条说不清的错，也不能让一次过度推断把整章判死：前者进正史后还能
    在下一章被事实纠正，后者是确定性的产能损失，且会让人以为「这本书写不下去了」。
    """
    key = hashlib.sha256(
        f"{issue.rule_id}|{issue.quote}|{issue.fact}".encode("utf-8")
    ).hexdigest()[:16]

    async def ask(payload: dict[str, Any], round_no: int) -> VerdictOut:
        return await _call(
            session,
            provider,
            work,
            run,
            production,
            VerdictOut,
            CONFIRM_SYSTEM,
            payload,
            "final_audit_confirm",
            f"v{_input_version(run)}:final_audit_confirm:{key}:r{round_no}",
            temperature=0,
        )

    try:
        return await verify_loop(ask, issue=issue, content=content)
    except (CallError, BudgetExhausted) as exc:
        return False, f"复核未完成：{type(exc).__name__}"



async def run_final_audit(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    cast: dict[str, Any],
) -> list[str]:
    """对当前正文做一次终审；返回必须挡住本章的硬错误（可直接进改稿提示）。

    同一 prose_hash 只审一次：改稿之后 hash 变了会重审，没变就直接复用结论。
    """
    content = str(run.content or "")
    digest = _digest(content)

    # 版式先于模型：确定性、零成本，判的是「这份正文自己有没有坏」，
    # 与模型判的「叙事是否自洽」是两件事。放在 cache 之前，是因为它不吃缓存
    # ——同一份稿改过之后 hash 变了，版式结论必须跟着变。
    production["layout"] = _layout_payload(content)

    cached = _cached(production, digest)
    if cached is not None:
        return cached

    ctx = run.generation_context or {}
    payload = build_payload(ctx=ctx, production=production, cast=cast, content=content)
    # 放行口径可以随运行下发（便于灰度「只留档」与「会拦」两种行为对比），
    # 缺省是 record：没有明确要求就不许自己判死一章。
    production.setdefault(
        "final_audit_mode", str(ctx.get("final_audit_mode") or "record")
    )
    misses = int(production.get("final_audit_unknown") or 0)
    try:
        result = await _call(
            session,
            provider,
            work,
            run,
            production,
            FinalAuditResult,
            FINAL_AUDIT_SYSTEM,
            payload,
            "final_prose_audit",
            f"v{_input_version(run)}:final_audit:{digest[:12]}"
            + (f":m{misses}" if misses else ""),
            temperature=0,
        )
    except (CallError, BudgetExhausted) as exc:  # 结果不可知或额度不足：重试一次，然后留档
        misses += 1
        production["final_audit_unknown"] = misses
        if misses < 2:
            _store(
                production,
                digest=digest,
                status="unavailable",
                issues=[],
                dropped=[f"调用未完成，稍后重试：{type(exc).__name__}"],
            )
            raise
        _store(
            production,
            digest=digest,
            status="unavailable",
            issues=[],
            dropped=[f"调用连续未完成：{type(exc).__name__}: {str(exc)[:120]}"],
        )
        production["final_audit"]["items"] = []
        return []

    # ``_call`` 已把调用账目写进这份 production，并在 run 上存了一份副本；
    # 这里必须继续改同一份字典——回读 run 上的副本会把结论写到一个稍后就被
    # 调用方覆盖掉的对象上（本章随后还会再 _save 一次）。
    issues, dropped = audit.ground_issues(
        [
            audit.AuditIssue(
                rule_id=str(item.rule_id),
                quote=str(item.quote),
                reason=str(item.reason),
                fact=str(item.fact or ""),
            )
            for item in result.issues
        ],
        content,
        facts=[
            *(payload.get("known_facts") or []),
            str(payload.get("previous_chapter_tail") or ""),
        ],
    )
    # R2 复核：引文对得上还不够，还要有人按这段正文重读一遍，说出「读者会不会认为
    # 作者记错了」。一次过度推断（把「记得」读成「持有」）足以把正常章节判死，而
    # temperature=0 下原样重试不会改变结论——所以复核要能要更多上下文（见 verify_loop）。
    confirmed: list[audit.AuditIssue] = []
    for item in issues:
        holds, why = await _confirm(
            session,
            provider=provider,
            work=work,
            run=run,
            production=production,
            issue=item,
            content=content,
        )
        if holds:
            confirmed.append(item)
        else:
            dropped.append(f"{item.rule_id}：复核判为不成立（{why[:80]}）")
    issues = confirmed
    fails = [audit.format_issue(item) for item in issues]
    _store(
        production,
        digest=digest,
        status="failed" if issues else "passed",
        issues=issues,
        dropped=dropped,
    )
    production["final_audit"]["items"] = list(fails)
    if production["final_audit"]["mode"] != "block":
        # 默认只留档：标定显示拦截精确率不足以支撑判死一章，口径由产品决定后再开。
        return []
    return fails


__all__ = [
    "FINAL_AUDIT_SYSTEM",
    "FinalAuditResult",
    "VerdictOut",
    "build_payload",
    "run_final_audit",
    "verify_loop",
]

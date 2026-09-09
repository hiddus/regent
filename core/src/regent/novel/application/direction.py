"""Persistent scene production, driven by a director rather than a chapter editor.

Each tick makes at most one model call. The caller commits the checkpoint with
the call ledger. A crash before that commit can still leave an UNKNOWN external
call; this module does not claim provider-side exactly-once execution.
"""

# Chinese creative instructions deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.runtime import (
    NO_SCENE,
    CommandRuntime,
    RuntimeLimits,
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.context import (
    compile_actor_context,
    compile_director_performance_context,
    compile_writer_context,
    public_performances,
)
from regent.novel.domain.errors import ProductionStopped, QuotaExceeded
from regent.novel.domain.hive import route_beat
from regent.novel.domain.memory import project_payloads as _memory_view
from regent.novel.domain.models import DecisionOption
from regent.novel.domain.states import SceneArtifact, SceneRunState
from regent.novel.infrastructure.models import ChapterRunModel, PersonaSpecModel, StoryWorkModel

ARCHITECTURE = "director_v2"
# 「所有人都知道」的既有记号：它不是人物名，核验知情范围时必须跳过。
EVERYONE_KNOWS = "ALL"
MAX_CALLS = 80
MAX_TAKES = 3
MAX_TURNS = 4
MAX_REVISIONS = 2
MAX_CHAPTER_REPAIRS = 1
# 货币预算上限（分），与调用次数上限互不替代：次数管行为，金额管钱。
# pilot 值，R4 盲评后按成本曲线版本化冻结（Tech-Spec §4.3）。
MAX_COST_MINOR = 20_000
CURRENCY = "CNY"

# 导演发起的裁决必须有到期时间，否则章节会永久停在等待态——这正是「阻塞交互
# 无超时」那类缺陷。到期无人选择就走默认项（G-13），所以窗口只决定等多久。
# 影响越大等得越久：高影响裁决的默认项往往不可逆，不能因为作者两天没上线就
# 替他决定（D-05 的「高影响默认等待」据此落地为「窗口更长」而非无限等待）。
DECISION_DEADLINE_BY_IMPACT = {
    "LOW": timedelta(hours=6),
    "MEDIUM": timedelta(hours=24),
    "HIGH": timedelta(hours=72),
}
DECISION_DEADLINE_DEFAULT = timedelta(hours=24)

# 命令 Runtime：模型只提出命令，校验通过后才允许改变状态（Tech-Spec §3.4）。
_RUNTIME = CommandRuntime(
    RuntimeLimits(
        max_calls=MAX_CALLS,
        max_takes=MAX_TAKES,
        max_turns=MAX_TURNS,
        max_revisions=MAX_REVISIONS,
        max_cost_minor=MAX_COST_MINOR,
    )
)

# 模型只能在这两张表里选择命令；其余一切由 Runtime 拒绝。
_TAKE_COMMANDS = {
    "CONTINUE": CommandKind.CONTINUE_SCENE,
    "RETAKE": CommandKind.RETAKE_SCENE,
    "RENDER": CommandKind.RENDER_SCENE,
}
_PROSE_COMMANDS = {
    "RETAKE": CommandKind.RETAKE_SCENE,
    "REWRITE": CommandKind.REWRITE_PROSE,
    "ACCEPT": CommandKind.ACCEPT_SCENE,
}


class ActorDirection(BaseModel):
    persona: str
    objective: str = Field(min_length=1)
    instruction: str = Field(min_length=1, description="表演指导，不含本人未知的秘密或未来结果")


class NarrativeSpec(BaseModel):
    viewpoint: str
    distance: str
    style: str
    reader_effect: str = Field(min_length=1)
    disclosure_rule: str = Field(min_length=1)


class SceneBrief(BaseModel):
    purpose: str = Field(min_length=1)
    setting: str = Field(min_length=1)
    conflict: str = Field(min_length=1)
    exit_condition: str = Field(min_length=1)
    actors: list[ActorDirection] = Field(min_length=1, max_length=4)
    narrative: NarrativeSpec


class ChapterDirection(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reader_intent: str = Field(min_length=1)
    ending_reason: str = Field(min_length=1)
    scenes: list[SceneBrief] = Field(min_length=1, max_length=4)


class ActorTurn(BaseModel):
    intention: str
    actions: list[str] = Field(min_length=1, max_length=5)
    dialogue: list[str] = Field(default_factory=list, max_length=5)
    private_reasoning: str = ""


class SceneEvent(BaseModel):
    statement: str = Field(min_length=1)
    known_by: list[str] = Field(default_factory=list)
    reader_visible: bool
    state_changes: dict[str, str] = Field(default_factory=dict)
    dialogue_by_character: dict[str, list[str]] = Field(default_factory=dict)


class SceneResolution(BaseModel):
    events: list[SceneEvent] = Field(min_length=1, max_length=8)
    rule_issues: list[str] = Field(default_factory=list)


class DecisionRequestSpec(BaseModel):
    """导演请求用户裁决：只在影响后续走向、且导演无权代用户决定时使用。

    选项必须给出近期后果与可逆性，默认项必须真实存在——到期无人选择时
    走的是默认项，不能让它落空（G-13）。
    """

    trigger_summary: str = Field(min_length=1)
    why_human: str = Field(min_length=1)
    options: list[DecisionOption] = Field(min_length=2, max_length=4)
    default_option_id: str = Field(min_length=1)
    impact_level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    impact_horizon_chapters: int = Field(default=1, ge=1, le=50)
    node_id: str = ""

    @model_validator(mode="after")
    def _default_exists(self) -> DecisionRequestSpec:
        ids = {option.option_id for option in self.options}
        if self.default_option_id not in ids:
            raise ValueError("default_option_id 必须出现在 options 中")
        if len(ids) != len(self.options):
            raise ValueError("option_id 不得重复")
        return self


class TakeDirection(BaseModel):
    action: Literal["CONTINUE", "RETAKE", "RENDER"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None
    request_decision: DecisionRequestSpec | None = None


class SceneText(BaseModel):
    content: str = Field(min_length=200, max_length=12000)


class ProseDirection(BaseModel):
    action: Literal["ACCEPT", "REWRITE", "RETAKE"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None
    request_decision: DecisionRequestSpec | None = None


class VerifiedFact(BaseModel):
    statement: str = Field(min_length=1)
    quote: str = Field(min_length=1, description="最终正文中逐字存在的证据")
    known_by: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    # 长期记忆分类：只标**稳定**的世界规则/设定，其余留空。人物弧线与关系由
    # 事实里出现的人物名结构性地判定，不需要（也不应该）由核验者猜。
    memory_kind: str = Field(
        default="", description="rule / character_arc / promise / relation；不填即按事实结构判定"
    )
    subject: str = Field(default="", description="记忆主题；不填即按在册人物推导")
    # 本条事实**兑现了哪一条具体承诺**（C-03）。写承诺本身的内容，不要只写人物名：
    # 按人物名兑现会把同一个人名下所有未兑现承诺一起关掉，等于把还没还的伏笔
    # 当成还完了。留空表示这条事实不兑现任何承诺。
    resolves: str = Field(
        default="",
        description="被本条事实兑现的具体承诺（写承诺内容，不写人物名）；不填即不兑现",
    )


class VerifiedStateChange(BaseModel):
    key: str = Field(min_length=1)
    value: str
    quote: str = Field(min_length=1)


class SceneValidation(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    facts: list[VerifiedFact] = Field(default_factory=list)
    state_changes: list[VerifiedStateChange] = Field(default_factory=list)


class ChapterValidation(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    node_completed: bool = False
    completion_quote: str = ""
    failed_scene_index: int | None = Field(default=None, ge=0)


class EndingVerdict(BaseModel):
    """导演对「用户认可的终局是否已达成」的判断（B-05）。

    只有两个字段是刻意的：`story_complete` 必须是布尔，不能是「倾向」。给
    `confidence` 之类的中间值，调用方迟早会写 `if confidence > 0.5: 完结`，
    于是模型的一个模糊判断就变成不可逆的结束。判不出来就不调用这里——由调用方
    按 undecided 处理，保留可恢复状态。
    """

    story_complete: bool
    reason: str = Field(min_length=1, description="引用已写内容说明为什么算/不算讲完")


def is_directed(run: Any) -> bool:
    return (run.generation_context or {}).get("architecture_version") == ARCHITECTURE


def _save(run: Any, production: dict[str, Any]) -> None:
    run.generation_context = {**run.generation_context, "production": deepcopy(production)}


def _quote_check(quotes: list[str], text: str) -> None:
    if not quotes or any(not quote.strip() or quote not in text for quote in quotes):
        raise ProductionStopped("导演判断缺少可核对的原文证据")


def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:
    names = [actor.persona for actor in brief.actors]
    if len(names) != len(set(names)) or any(name not in cast for name in names):
        raise ProductionStopped("场景包含重复或未定义的角色")


def _runtime_state(production: dict[str, Any], run: Any, take: dict[str, Any]) -> RuntimeState:
    """从生产状态构造只读快照，供 Runtime 校验命令。"""
    return RuntimeState(
        scene_state=take.get("scene_state") or SceneRunState.BRIEFED.value,
        artifact=take.get("artifact", ""),
        input_version=_input_version(run),
        turn=take["turn"],
        takes_used=sum(
            t["scene_index"] == production["scene_index"] for t in production["takes"]
        ),
        revisions=take["revisions"],
        calls_used=production.get("call_count", 0),
        reserved_minor=production.get("reserved_minor", 0),
        cast=frozenset(production["cast"]),
        has_rule_issues=bool(take.get("rule_issues")),
        has_hard_failure=take.get("validation", {}).get("passed") is False,
        has_visible_events=any(e.get("reader_visible") for e in take["events"]),
        scene_index=int(production.get("scene_index", 0) or 0),
        take_no=int(take.get("take_no", 1) or 1),
        has_prose=bool(take.get("content")),
    )


def _apply_state(runtime: CommandRuntime, take: dict[str, Any], state: RuntimeState,
                 target: tuple[str, str]) -> None:
    """把 Runtime 校验通过的目标阶段写回 take。"""
    moved = runtime.next_state(state, target)
    take["scene_state"] = moved.scene_state
    take["artifact"] = moved.artifact


def _record_manifest(take: dict[str, Any], compiled: Any) -> None:
    """上下文留痕：manifest 不进入模型输入，只用于证明可复现与定位泄露。"""
    take.setdefault("manifests", []).append(
        {
            "audience": compiled.audience,
            "persona": compiled.persona,
            "manifest_hash": compiled.manifest_hash,
            "manifest_version": compiled.manifest.version,
            # 完整绑定：只存 hash 无法回答"这次装配读的是哪一场的哪些材料"
            "binding": dict(compiled.manifest.binding),
            "projection_hash": compiled.manifest.projection_hash,
            "fingerprint": compiled.manifest.fingerprint(),
            "sources": [item.model_dump(mode="json") for item in compiled.manifest.sources],
        }
    )


def _input_version(run: Any) -> int:
    """输入版本：用户改意/引导后递增，参与逻辑调用标识（Tech-Spec §5）。"""
    return int(getattr(run, "input_version", 1) or 1)


def _command_id(production: dict[str, Any], run: ChapterRunModel, phase: str) -> str:
    """稳定命令标识：同一命令在崩溃恢复后得到同一个 logical_call_id。

    输入版本（用户改意/引导）参与标识，因此新的输入是一次新的逻辑调用，
    不会与旧调用撞成幂等冲突（Tech-Spec §5）。
    """
    take = production["takes"][-1]
    return (
        f"v{_input_version(run)}:s{production['scene_index']}t{take['take_no']}"
        f":{phase}:turn{take['turn']}:act{len(take['round_actions'])}"
        f":rev{take['revisions']}:dec{len(production.get('decisions', []))}"
    )


async def _call[T: BaseModel](
    session: AsyncSession,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    schema: type[T],
    system: str,
    payload: dict[str, Any],
    purpose: str,
    command_id: str,
) -> T:
    """发起一次导演逻辑调用：调用前预留、事务外调用、调用后结算、恢复可复用。"""
    from regent.novel.application.production import CallBroker

    count = production.get("call_count", 0)
    if count >= MAX_CALLS:
        raise ProductionStopped("本章导演调用预算已耗尽，草稿已保留")
    # 预算按「已结算金额 + 本次估价」校验，不能用累计历史预留当可用额度：
    # 预留会被释放，累计值会虚高到把上限算穿（Plan v6.4 §10 P0-3）。
    committed = int(production.get("committed_minor", 0) or 0)
    remaining = MAX_COST_MINOR - committed
    if remaining <= 0:
        raise ProductionStopped("本章货币预算已耗尽，草稿已保留")

    broker = CallBroker(lease_owner=f"run:{run.id}", budget_limit_minor=remaining)
    user_prompt = json.dumps(payload, ensure_ascii=False)
    try:
        result = await broker.run(
        session,
        provider=provider,
        schema=schema,
        work_id=work.id,
        run_id=run.id,
        chapter_no=run.chapter_no,
        step=run.current_step or "PRODUCE",
        purpose=purpose,
        command_id=command_id,
        system_prompt=system,
        user_prompt=user_prompt,
            temperature=0.8 if schema in (ActorTurn, SceneText) else 0,
            model_hint=getattr(provider, "model_name", "") or "",
        )
    except QuotaExceeded as exc:
        # 预留阶段就超限：调用尚未发出，草稿保留（P0-3）。
        raise ProductionStopped("本章货币预算已耗尽，草稿已保留") from exc
    # 结果落库后再推进检查点：崩溃恢复会命中已成功的 logical call 并复用。
    production["call_count"] = count + 1
    production["committed_minor"] = committed + int(result.actual_minor or 0)
    production["reserved_minor"] = production.get("reserved_minor", 0) + result.reserved_minor
    production["actual_minor"] = production.get("actual_minor", 0) + result.actual_minor
    if result.reused:
        production["reused_calls"] = production.get("reused_calls", 0) + 1
        production["avoided_minor"] = production.get("avoided_minor", 0) + result.avoided_minor
    # Keep raw output, including rejected decisions, for diagnosis and replay.
    production.setdefault("calls", []).append(
        {
            "number": count,
            "purpose": purpose,
            "schema": schema.__name__,
            "reused": result.reused,
            "reserved_minor": result.reserved_minor,
            "actual_minor": result.actual_minor,
            "input_hash": hashlib.sha256(user_prompt.encode()).hexdigest(),
            "output": result.output.model_dump(mode="json"),
        }
    )
    _save(run, production)
    return result.output


async def _call_batch[T: BaseModel](
    session: AsyncSession,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    schema: type[T],
    system: str,
    payloads: list[tuple[str, dict[str, Any]]],
    purpose: str,
    command_id: str,
) -> list[T]:
    """同节拍多角色表演：预留与结算顺序执行，只有模型调用并发（Hive）。

    会话不支持并发使用，所以并发只覆盖 HTTP 往返；账本语义与逐条调用一致。
    ``payloads`` 是 ``(candidate_id, payload)``，candidate_id 让同一节拍的不同
    角色得到不同的逻辑调用键，不会互相撞成幂等复用。
    """
    from regent.novel.application.production import BatchCall, CallBroker

    count = production.get("call_count", 0)
    if count + len(payloads) > MAX_CALLS:
        raise ProductionStopped("本章导演调用预算已耗尽，草稿已保留")
    committed = int(production.get("committed_minor", 0) or 0)
    remaining = MAX_COST_MINOR - committed
    if remaining <= 0:
        raise ProductionStopped("本章货币预算已耗尽，草稿已保留")

    broker = CallBroker(lease_owner=f"run:{run.id}", budget_limit_minor=remaining)
    specs = [
        BatchCall(
            purpose=purpose,
            command_id=command_id,
            candidate_id=candidate_id,
            system_prompt=system,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            temperature=0.8 if schema in (ActorTurn, SceneText) else 0,
            model_hint=getattr(provider, "model_name", "") or "",
        )
        for candidate_id, payload in payloads
    ]
    try:
        results = await broker.run_batch(
            session,
            provider=provider,
            schema=schema,
            work_id=work.id,
            run_id=run.id,
            chapter_no=run.chapter_no,
            step=run.current_step or "PRODUCE",
            calls=specs,
        )
    except QuotaExceeded as exc:
        raise ProductionStopped("本章货币预算已耗尽，草稿已保留") from exc

    for (candidate_id, payload), result in zip(payloads, results):
        production.setdefault("calls", []).append(
            {
                "number": count,
                "purpose": purpose,
                "candidate_id": candidate_id,
                "schema": schema.__name__,
                "reused": result.reused,
                "reserved_minor": result.reserved_minor,
                "actual_minor": result.actual_minor,
                "input_hash": hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False).encode()
                ).hexdigest(),
                "output": result.output.model_dump(mode="json"),
            }
        )
        count += 1
        committed += int(result.actual_minor or 0)
        production["reserved_minor"] = (
            production.get("reserved_minor", 0) + result.reserved_minor
        )
        production["actual_minor"] = production.get("actual_minor", 0) + result.actual_minor
        if result.reused:
            production["reused_calls"] = production.get("reused_calls", 0) + 1
            production["avoided_minor"] = (
                production.get("avoided_minor", 0) + result.avoided_minor
            )
    production["call_count"] = count
    production["committed_minor"] = committed
    _save(run, production)
    return [result.output for result in results]


def _pending_user_decision(run: ChapterRunModel) -> dict[str, Any] | None:
    """取回尚未被导演消费的用户裁决（A-02）。

    选择只在上下文里躺着不算「改变了创作」：导演必须真的读到它、据此行动，
    然后把它标记为已消费。返回完整语义，不是 option_id。
    """
    resolutions = (run.generation_context or {}).get("decision_resolutions") or []
    for item in reversed(resolutions):
        if not item.get("consumed"):
            return dict(item)
    return None


def _consume_user_decision(run: ChapterRunModel, production: dict[str, Any]) -> None:
    """标记裁决已被导演执行，并清除 pending 挂起。

    消费后同一个裁决不会第二次进入导演上下文——否则导演会反复「执行」同一个
    决定，或就同一件事再问用户一次。
    """
    context = dict(run.generation_context or {})
    resolutions = list(context.get("decision_resolutions") or [])
    for item in reversed(resolutions):
        if not item.get("consumed"):
            item["consumed"] = True
            break
    context["decision_resolutions"] = resolutions
    run.generation_context = context
    production.pop("pending_decision", None)


async def _request_user_decision(
    session: AsyncSession,
    *,
    work: StoryWorkModel,
    run: ChapterRunModel,
    production: dict[str, Any],
    spec: DecisionRequestSpec,
    phase: str,
) -> None:
    """导演请求用户裁决：命令校验通过后落持久化请求并让章节等待（P1-2）。"""
    from regent.novel.application import works

    take = production["takes"][-1]
    _RUNTIME.validate(
        director_command(
            CommandKind.REQUEST_USER_DECISION,
            command_id=_command_id(production, run, phase),
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=take["take_no"],
            evidence=[spec.trigger_summary],
            payload={
                "why_human": spec.why_human,
                "options": [o.option_id for o in spec.options],
                "default_option_id": spec.default_option_id,
            },
        ),
        _runtime_state(production, run, take),
    )
    node_id = spec.node_id or str(
        (run.generation_context.get("target_node") or {}).get("node_id", "")
    )
    await works.create_decision(
        session,
        owner_id=work.owner_id,
        work_id=work.id,
        chapter_no=run.chapter_no,
        run_id=run.id,
        node_id=node_id,
        trigger_summary=spec.trigger_summary,
        why_human=spec.why_human,
        options=[option.model_dump(mode="json") for option in spec.options],
        default_option_id=spec.default_option_id,
        impact_level=spec.impact_level,
        impact_horizon_chapters=spec.impact_horizon_chapters,
        deadline=datetime.now(UTC)
        + DECISION_DEADLINE_BY_IMPACT.get(
            spec.impact_level.upper(), DECISION_DEADLINE_DEFAULT
        ),
    )
    production["pending_decision"] = {
        "phase": phase,
        "scene_index": production["scene_index"],
        "take_no": take["take_no"],
        "trigger_summary": spec.trigger_summary,
    }
    _save(run, production)


async def plan_chapter(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
) -> None:
    production = deepcopy(run.generation_context.get("production", {}))
    if production.get("plan"):
        return
    personas = (
        await session.scalars(
            select(PersonaSpecModel)
            .where(
                PersonaSpecModel.work_id == work.id,
            )
            .order_by(PersonaSpecModel.name)
        )
    ).all()
    cast = {
        p.name: {"identity": p.identity, "drives": p.drives, "voice": p.voice} for p in personas
    }
    if not cast:
        raise ProductionStopped("没有可供导演调度的角色")
    # 规划也是一条命令：没有场景时只允许 PLAN_SCENE（P1-1）
    _RUNTIME.validate(
        director_command(
            CommandKind.PLAN_SCENE,
            command_id=f"v{_input_version(run)}:plan",
            input_version=_input_version(run),
            scene_index=int(production.get("scene_index", 0) or 0),
            take_no=1,
            payload={"cast": sorted(cast)},
        ),
        RuntimeState(scene_state=NO_SCENE, input_version=_input_version(run)),
    )
    result = await _call(
        session,
        provider,
        work,
        run,
        production,
        ChapterDirection,
        "你是持续负责小说创作的导演。先设计本章阅读体验，再安排1至4个必要场景。"
        "每场明确人物欲望冲突、表演指导、视角、信息差和结束理由。允许舒缓和关系戏，"
        "不要机械升级冲突或套用三章结构。人物合理的选择可以改变未锁定计划。"
        "只使用给定角色；角色指令不泄露本人未知秘密，不预写结果和台词。"
        "不擅自决定用户锁定的重大节点。正文总目标1800至2500字。",
        {
            # D-03：导演计划请求拿**导演视图**的长期记忆（含未兑现承诺与导演
            # 笔记），不再把召回的全量 memory 原样塞进请求——那是 C-03 投影
            # 纪律，六步旧流程早已如此，director_v2 不得成为例外。
            "context": {
                k: v
                for k, v in run.generation_context.items()
                if k not in ("production", "memory")
            },
            "cast": cast,
            "user_guidance": run.user_guidance or {},
            "director_memory": _memory_view(
                run.generation_context.get("memory", []), "director"
            ),
        },
        "plan",
        f"v{_input_version(run)}:plan",
    )
    for brief in result.scenes:
        _check_brief(brief, cast)
    production.update(
        {
            "schema_version": 1,
            "plan": result.model_dump(mode="json"),
            "cast": cast,
            "scene_index": 0,
            "phase": "ACT",
            "takes": [],
            "accepted": [],
            "decisions": [],
            "working_state": run.generation_context.get("actual_state", {}),
        }
    )
    _new_take(production, result.scenes[0].model_dump(mode="json"))
    run.title = result.title
    _save(run, production)


def _new_take(
    production: dict[str, Any],
    brief: dict[str, Any],
    scene_state: str = SceneRunState.BRIEFED.value,
    artifact: str = "",
) -> None:
    scene = production["scene_index"]
    count = sum(t["scene_index"] == scene for t in production["takes"])
    if count >= MAX_TAKES:
        raise ProductionStopped("场景重演次数已达上限，未放行失败场景")
    production["takes"].append(
        {
            "scene_index": scene,
            "take_no": count + 1,
            "brief": brief,
            "state_before": deepcopy(production["working_state"]),
            "turn": 0,
            "performances": [],
            "round_actions": [],
            "events": [],
            "revisions": 0,
            "prose_versions": [],
            "status": "DRAFT",
            "scene_state": scene_state,
            "artifact": artifact,
        }
    )
    production["phase"] = "ACT"


def _retake(production: dict[str, Any], take: dict[str, Any], decision: Any) -> None:
    if decision.revised_brief is None:
        raise ProductionStopped("重演必须提供修改后的场景指令")
    _check_brief(decision.revised_brief, production["cast"])
    brief = decision.revised_brief.model_dump(mode="json")
    if brief == take["brief"]:
        raise ProductionStopped("重演未改变场景指令")
    # Rejected events never modify working_state or subsequent actor knowledge.
    _new_take(production, brief)
    take["status"] = "REJECTED"


def _all_events(production: dict[str, Any], take: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e for index in production["accepted"] for e in production["takes"][index]["events"]
    ] + take["events"]


async def produce_tick(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
) -> bool:
    """Advance one command; False asks the worker to commit and yield."""
    production = deepcopy(run.generation_context["production"])
    if production["phase"] == "DONE":
        return True
    take = production["takes"][-1]
    brief = take["brief"]
    phase = production["phase"]
    prefix = f"scene{production['scene_index']}:take{take['take_no']}:{phase}"
    # D-03：六视角记忆投影只在此处按受众裁剪一次，装配器只负责留痕。
    memory_payloads = list(run.generation_context.get("memory", []) or [])

    async def call[T: BaseModel](schema: type[T], system: str, payload: dict[str, Any]) -> T:
        return await _call(
            session,
            provider,
            work,
            run,
            production,
            schema,
            system,
            payload,
            prefix,
            _command_id(production, run, phase),
        )

    if phase == "ACT":
        remaining_actors = brief["actors"][len(take["round_actions"]):]
        # 上下文先全部编译：路由决策必须基于真实上下文，不能基于"应该是隔离的"
        compiled_by_persona = {
            actor["persona"]: compile_actor_context(
                work_id=work.id,
                branch_id=run.branch_id,
                chapter_no=run.chapter_no,
                scene_index=production["scene_index"],
                take_no=take["take_no"],
                beat=take["turn"],
                persona=actor["persona"],
                cast=production["cast"],
                direction=actor,
                setting=brief["setting"],
                canon=run.generation_context.get("canon", []),
                observations=_all_events(production, take),
                turn=take["turn"],
                # D-03：角色只拿**他自己**的长期记忆投影（他知道的/误信的/
                # 承诺过的）；读者认知与导演笔记不进人物上下文。逐人物裁剪
                # 同时保住了 Hive 隔离：每个 compiled payload 仍只含本人视角。
                memory=_memory_view(memory_payloads, "character", actor["persona"]),
            )
            for actor in remaining_actors
        }
        route = route_beat(compiled_by_persona)
        take.setdefault("hive", []).append(
            {
                **route.as_dict(),
                "beat": take["turn"],
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
            }
        )
        # 隔离不成立时退回逐角色：宁可慢，也不能让角色看到彼此的私有信息。
        batch = (
            [compiled_by_persona[a["persona"]] for a in remaining_actors]
            if route.enabled
            else [compiled_by_persona[remaining_actors[0]["persona"]]]
        )
        state = _runtime_state(production, run, take)
        for compiled in batch:
            _RUNTIME.validate(
                director_command(
                    CommandKind.REQUEST_PERFORMANCE,
                    command_id=_command_id(production, run, phase),
                    input_version=_input_version(run),
                    scene_index=production["scene_index"],
                    take_no=take["take_no"],
                    persona=compiled.persona,
                ),
                state,
            )
        system = (
            "扮演给定人物。只根据自身动机、已知事实和可观察事件提出行动及台词。"
            "行动是尝试，不能自行宣布成功。不要猜测别人的秘密或未来安排。"
            "表演指导若超出已知信息，不得将其当作新事实。"
        )
        if len(batch) == 1:
            outputs = [
                await call(ActorTurn, system, batch[0].payload)
            ]
        else:
            outputs = await _call_batch(
                session,
                provider,
                work,
                run,
                production,
                ActorTurn,
                system,
                [(c.persona, c.payload) for c in batch],
                prefix,
                _command_id(production, run, phase),
            )
        for compiled, result in zip(batch, outputs):
            _record_manifest(take, compiled)
            action = {"persona": compiled.persona, **result.model_dump(mode="json")}
            take["round_actions"].append(action)
            take["performances"].append(action)
        if len(take["round_actions"]) == len(brief["actors"]):
            # 本节拍全员表演完毕：进入结算由 Runtime 推进，不受模型影响。
            _apply_state(
                _RUNTIME,
                take,
                _runtime_state(production, run, take),
                (SceneRunState.RESOLVING.value, ""),
            )
            production["phase"] = "RESOLVE"
        else:
            _apply_state(
                _RUNTIME, take, state, (SceneRunState.PERFORMING.value, "")
            )
    elif phase == "RESOLVE":
        result = await call(
            SceneResolution,
            "你负责场景行动结算。依据客观事实和规则判定尝试产生的结果。"
            "不可为戏剧目标强行成功。列出实际事件、状态差异、谁获得信息和读者可见性。"
            "known_by只列实际获知的给定人物；缺席人物不能自动获知。"
            "读者可见性必须符合叙事视角与披露规则。"
            "将实际说出的关键台词保存在事件dialogue_by_character，保留人物声纹。"
            "规则冲突写rule_issues。",
            {
                "brief": brief,
                "canon": run.generation_context.get("canon", []),
                "state_before": take["state_before"],
                "events": take["events"],
                "actions": public_performances(take["round_actions"]),
            },
        )
        if any(
            name not in production["cast"] for event in result.events for name in event.known_by
        ):
            raise ProductionStopped("结算事件含未知的知情人物")
        if any(
            name not in production["cast"]
            for event in result.events
            for name in event.dialogue_by_character
        ):
            raise ProductionStopped("台词归属含未知人物")
        take["events"].extend(e.model_dump(mode="json") for e in result.events)
        take["rule_issues"] = result.rule_issues
        _apply_state(
            _RUNTIME,
            take,
            _runtime_state(production, run, take),
            (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PERFORMANCE.value),
        )
        production["phase"] = "WATCH_TAKE"
    elif phase == "WATCH_TAKE":
        watch = compile_director_performance_context(
            work_id=work.id,
            branch_id=run.branch_id,
            chapter_no=run.chapter_no,
            scene_index=production["scene_index"],
            take_no=take["take_no"],
            brief=brief,
            events=take["events"],
            performances=take["performances"],
            rule_issues=take.get("rule_issues", []),
            remaining_turns=MAX_TURNS - take["turn"] - 1,
            user_guidance=run.user_guidance or {},
            user_decision=_pending_user_decision(run),
            # D-03：导演观看表演时带导演视图记忆（未兑现承诺是排场的硬约束）。
            memory=_memory_view(memory_payloads, "director"),
        )
        result = await call(TakeDirection, "你是导演，观看实际演绎，判断人物选择和场景效果。"
            "决定CONTINUE推进下一节拍、RETAKE改变调度重演、RENDER结束表演进入小说呈现。"
            "evidence必须逐字引用事件或可见行动。重演必须给出不同的revised_brief。"
            "有rule_issues必须重演；不要把自己变成打分编辑。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "推进，不得执行其他选项的走向，也不得就同一件事再次请求裁决。", watch.payload)
        _record_manifest(take, watch)
        evidence_text = "\n".join(
            [e["statement"] for e in take["events"]]
            + [line for a in take["performances"] for line in a["actions"] + a["dialogue"]]
        )
        _quote_check(result.evidence, evidence_text)
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **result.model_dump(mode="json"),
            }
        )
        if result.request_decision is not None:
            await _request_user_decision(
                session,
                work=work,
                run=run,
                production=production,
                spec=result.request_decision,
                phase=phase,
            )
            return False
        # 导演已带着用户裁决作出下一步决定：这次选择被消费掉，不再重复生效。
        _consume_user_decision(run, production)
        state = _runtime_state(production, run, take)
        target = _RUNTIME.validate(
            director_command(
                _TAKE_COMMANDS[result.action],
                command_id=_command_id(production, run, phase),
                input_version=_input_version(run),
                scene_index=production["scene_index"],
                take_no=take["take_no"],
                evidence=result.evidence,
                actors=brief["actors"],
                revised_brief=(
                    result.revised_brief.model_dump(mode="json")
                    if result.revised_brief is not None
                    else None
                ),
            ),
            state,
        )
        if result.action == "RETAKE":
            _retake(production, take, result)
        elif result.action == "CONTINUE":
            # Only persona-specific instructions go to actors, never the global diagnosis.
            if result.revised_brief is not None:
                _check_brief(result.revised_brief, production["cast"])
                take["brief"] = result.revised_brief.model_dump(mode="json")
            take["turn"] += 1
            take["round_actions"] = []
            _apply_state(_RUNTIME, take, state, target)
            production["phase"] = "ACT"
        else:
            _apply_state(_RUNTIME, take, state, target)
            production["phase"] = "RENDER"
            take["render_instruction"] = result.instruction
    elif phase == "RENDER":
        # Writer never receives raw canon, actor thoughts, hidden events or future scenes.
        prose = compile_writer_context(
            work_id=work.id,
            branch_id=run.branch_id,
            chapter_no=run.chapter_no,
            scene_index=production["scene_index"],
            take_no=take["take_no"],
            narrative=brief["narrative"],
            events=take["events"],
            voices={
                a["persona"]: production["cast"][a["persona"]]["voice"] for a in brief["actors"]
            },
            previous_ending=(
                production["takes"][production["accepted"][-1]]["content"][-500:]
                if production["accepted"]
                else ""
            ),
            target_characters=max(500, 2200 // len(production["plan"]["scenes"])),
            director_instruction=take.get("render_instruction", ""),
            previous_draft=take.get("content", ""),
            revision_instruction=take.get("revision_instruction", ""),
            # D-03：正文只拿叙述者视图记忆——读者认知可以出现，导演笔记
            # 永不进正文材料（与旧流程 weave 的 narrator_memory 同一纪律）。
            memory=_memory_view(memory_payloads, "narrator"),
        )
        result = await call(
            SceneText,
            "你是小说执笔者。按叙事指令将已发生的可见事件呈现为小说。"
            "控制视角、内心、详略和语言，不写剧本或导演说明。"
            "禁止新增重大事件、能力、人物或知识。只能使用给定材料。"
            "衔接前场结尾，避免重复复述；按目标字数形成完整场景。",
            prose.payload,
        )
        _record_manifest(take, prose)
        take["content"] = result.content.strip()
        take["prose_versions"].append(take["content"])
        take.pop("validation", None)
        _apply_state(
            _RUNTIME,
            take,
            _runtime_state(production, run, take),
            (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
        )
        production["phase"] = "WATCH_PROSE"
    elif phase == "WATCH_PROSE":
        result = await call(
            ProseDirection,
            "你是导演，观看小说呈现是否实现本场阅读体验、潜台词和人物情绪。"
            "给出具体观察和逐字正文evidence，决定ACCEPT、REWRITE表达或RETAKE表演。"
            "REWRITE必须给出具体不同的呈现指令；RETAKE必须给出不同的revised_brief。"
            "独立审校指出硬问题时不得ACCEPT。不要以文风评分代替创作决策。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "取舍，不得执行其他选项的走向，也不得就同一件事再次请求裁决。",
            {
                "brief": brief,
                "draft": take["content"],
                "events": take["events"],
                "validation": take.get("validation"),
                "remaining_revisions": MAX_REVISIONS - take["revisions"],
                "user_decision": _pending_user_decision(run),
                # D-03：导演审阅正文同样只拿导演视图记忆。
                "director_memory": _memory_view(memory_payloads, "director"),
            },
        )
        _quote_check(result.evidence, take["content"])
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **result.model_dump(mode="json"),
            }
        )
        if result.request_decision is not None:
            await _request_user_decision(
                session,
                work=work,
                run=run,
                production=production,
                spec=result.request_decision,
                phase=phase,
            )
            return False
        # 与 WATCH_TAKE 同理：裁决在此被导演执行一次后消费。
        _consume_user_decision(run, production)
        state = _runtime_state(production, run, take)
        target = _RUNTIME.validate(
            director_command(
                _PROSE_COMMANDS[result.action],
                command_id=_command_id(production, run, phase),
                input_version=_input_version(run),
                scene_index=production["scene_index"],
                take_no=take["take_no"],
                evidence=result.evidence,
                actors=brief["actors"],
                revised_brief=(
                    result.revised_brief.model_dump(mode="json")
                    if result.revised_brief is not None
                    else None
                ),
            ),
            state,
        )
        if result.action == "RETAKE":
            _retake(production, take, result)
        elif result.action == "REWRITE":
            if result.instruction == take.get("revision_instruction"):
                raise ProductionStopped("呈现修订重复了无效指令")
            take["revisions"] += 1
            take["revision_instruction"] = result.instruction
            _apply_state(_RUNTIME, take, state, target)
            production["phase"] = "RENDER"
        else:
            _apply_state(_RUNTIME, take, state, target)
            production["phase"] = "VALIDATE"
    elif phase == "VALIDATE":
        result = await call(
            SceneValidation,
            "独立核验正文与已结算事件、Canon、人物知情范围及叙事披露一致。"
            "检查新增/缺失重大事件、信息泄露、时间位置冲突。issues非空则passed=false。"
            "抽取正文明确发生且有逐字quote证据的事实，known_by仅列实际知情人物。"
            "state_changes逐项核验结算后的最终状态，每项提供正文quote；不能只复述计划。"
            "无法确认则不通过。你不负责戏剧决策。",
            {
                "draft": take["content"],
                "events": take["events"],
                "brief": brief,
                "prior_events": _all_events(production, take),
                "canon": run.generation_context.get("canon", []),
            },
        )
        issues = list(result.issues)
        for fact in result.facts:
            if fact.quote not in take["content"]:
                issues.append("事实证据不在正文中")
            # "ALL" 是「所有人都知道」的既有记号（context/hive/canon 同此口径），
            # 不是人物名：把它当未定义人物会让公开事实永远过不了核验。
            if any(
                name != EVERYONE_KNOWS and name not in production["cast"]
                for name in fact.known_by
            ):
                issues.append("事实知情范围含未定义人物")
        expected_changes = {}
        for event in take["events"]:
            expected_changes.update(event["state_changes"])
        verified_changes = {change.key: change.value for change in result.state_changes}
        if verified_changes != expected_changes or len(verified_changes) != len(
            result.state_changes
        ):
            issues.append("正文实际状态与场景结算不一致")
        if any(change.quote not in take["content"] for change in result.state_changes):
            issues.append("状态变化缺少正文证据")
        passed = result.passed and not issues and bool(result.facts)
        take["validation"] = {**result.model_dump(mode="json"), "passed": passed, "issues": issues}
        if not passed:
            # 未通过：回到导演重看正文。硬失败不得被跳过（G-04）。
            _apply_state(
                _RUNTIME,
                take,
                _runtime_state(production, run, take),
                (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
            )
            production["phase"] = "WATCH_PROSE"
        else:
            _apply_state(
                _RUNTIME,
                take,
                _runtime_state(production, run, take),
                (SceneRunState.ACCEPTED.value, ""),
            )
            take["status"] = "ACCEPTED"
            production["accepted"].append(len(production["takes"]) - 1)
            production["working_state"].update(verified_changes)
            production["scene_index"] += 1
            if production["scene_index"] == len(production["plan"]["scenes"]):
                # 组章是一条命令：必须在最后一场已接受、且每场都有稿件时才允许
                _RUNTIME.validate(
                    director_command(
                        CommandKind.ASSEMBLE_CHAPTER,
                        command_id=_command_id(production, run, "assemble"),
                        input_version=_input_version(run),
                        scene_index=production["scene_index"],
                        take_no=take["take_no"],
                        evidence=[
                    t["content"][:200] for t in production["takes"] if t.get("content")
                ],
                    ),
                    _runtime_state(production, run, take),
                )
                take["assembled"] = True
                run.content = "\n\n".join(
                    production["takes"][i]["content"] for i in production["accepted"]
                )
                run.word_count = len(run.content)
                production["phase"] = "DONE"
            else:
                _new_take(production, production["plan"]["scenes"][production["scene_index"]])
    else:
        raise ProductionStopped(f"未知导演阶段: {phase}")
    _save(run, production)
    return production["phase"] == "DONE"


async def validate_chapter(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    work: StoryWorkModel,
    run: ChapterRunModel,
) -> bool:
    production = deepcopy(run.generation_context["production"])
    if production["phase"] != "DONE" or not production["accepted"]:
        raise ProductionStopped("未完成场景不能提交整章")
    result = await _call(
        session,
        provider,
        work,
        run,
        production,
        ChapterValidation,
        "核验组章后的连续性、因果、重复事件及角色知识。确认所有场景衔接成立。"
        "只要存在事实冲突就不通过。不要按两个状态字段或每章冲突升级来判断。"
        "失败时指出最早受影响的场景下标failed_scene_index（从0开始）。"
        "判断目标节点是否真正完成；完成必须给出正文逐字completion_quote。",
        {
            "chapter": run.content,
            "recent_chapters": run.generation_context.get("recent_chapters", []),
            "target_node": run.generation_context.get("target_node", {}),
            "canon": run.generation_context.get("canon", []),
        },
        "chapter_validation",
        # 正文变了就是一次新的核验；正文未变时复用上次结论，不重复付费（G-09）
        f"v{_input_version(run)}:chapter_validation:"
        f"{hashlib.sha256(run.content.encode()).hexdigest()[:12]}",
    )
    issues = list(result.issues)
    if len(run.content.strip()) < 800:
        issues.append("整章正文不足800字符")
    if result.node_completed and (
        not result.completion_quote.strip() or result.completion_quote not in run.content
    ):
        issues.append("节点完成缺少正文证据")
    run.review = {
        "passed": result.passed and not issues,
        "continuity_issues": issues,
        "prose_issues": [],
        "leakage_issues": [],
        "revised": bool(production.get("chapter_repairs"))
        or any(len(take["prose_versions"]) > 1 for take in production["takes"]),
    }
    if not run.review["passed"]:
        content_hash = hashlib.sha256(run.content.encode()).hexdigest()
        if production.get("last_failed_hash") == content_hash:
            # 同样的正文只会得到同样的结论：继续重演只是花钱。
            raise ProductionStopped("修复没有改变正文，停止重试")
        production["last_failed_hash"] = content_hash
        repairs = production.get("chapter_repairs", 0)
        if repairs >= MAX_CHAPTER_REPAIRS:
            raise ProductionStopped("章节修复次数已达上限，保留场景及证据")
        index = result.failed_scene_index if result.failed_scene_index is not None else 0
        if index >= len(production["accepted"]):
            raise ProductionStopped("审校返回无效的场景下标")
        old = production["takes"][production["accepted"][index]]
        # Until a fine-grained dependency graph exists, invalidate the suffix.
        for i in production["accepted"][index:]:
            production["takes"][i]["status"] = "SUPERSEDED"
        production["accepted"] = production["accepted"][:index]
        production["scene_index"] = index
        production["working_state"] = deepcopy(old["state_before"])
        production["chapter_repairs"] = repairs + 1
        _new_take(
            production,
            old["brief"],
            SceneRunState.DIRECTOR_VIEW.value,
            SceneArtifact.PROSE.value,
        )
        current = production["takes"][-1]
        current.update(
            {
                "events": deepcopy(old["events"]),
                "content": old["content"],
                "prose_versions": [old["content"]],
                "validation": {"passed": False, "issues": issues or ["整章未通过独立审校"]},
            }
        )
        production["phase"] = "WATCH_PROSE"
        _save(run, production)
        return False
    # 完成也是一条命令：只在整章通过独立审校后才允许（P1-1）
    _RUNTIME.validate(
        director_command(
            CommandKind.FINISH_CHAPTER,
            command_id=f"v{_input_version(run)}:finish",
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=production["takes"][-1]["take_no"],
            evidence=[run.content[:200]],
        ),
        RuntimeState(
            scene_state=SceneRunState.ACCEPTED.value,
            input_version=_input_version(run),
            scene_index=production["scene_index"],
            take_no=production["takes"][-1]["take_no"],
            has_prose=bool(run.content),
        ),
    )
    facts = [
        fact
        for i in production["accepted"]
        for fact in production["takes"][i]["validation"]["facts"]
    ]
    run.generation_context = {
        **run.generation_context,
        "verified_facts": facts,
        "actual_state": production["working_state"],
        "node_completed": result.node_completed,
        "validated_content_hash": hashlib.sha256(run.content.encode()).hexdigest(),
    }
    return True

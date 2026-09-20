"""Director CallBroker adapter."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_budget import effective_call_cap as _effective_call_cap
from regent.novel.application.directing_budget import effective_cost_cap as _effective_cost_cap
from regent.novel.application.directing_contracts import ActorTurn, SceneText
from regent.novel.domain.errors import BudgetExhausted, QuotaExceeded
from regent.novel.domain.script_protocol import ChapterScript
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel


def _save(run: Any, production: dict[str, Any]) -> None:
    run.generation_context = {**run.generation_context, "production": deepcopy(production)}


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


def resolve_call_key_version(production: dict[str, Any], run: Any = None) -> int:
    """调用键版本：显式字段优先；旧检查点缺省为 1（无 vrep0），新运行写 2。"""
    if isinstance(production, dict) and "call_key_version" in production:
        return int(production.get("call_key_version") or 1)
    ctx = getattr(run, "generation_context", None) or {}
    if isinstance(ctx, dict) and "call_key_version" in ctx:
        return int(ctx.get("call_key_version") or 1)
    return 1


def hydrate_call_key_version(production: dict[str, Any], run: Any) -> None:
    """把根 context / 缺省旧格式写入 production，避免后续误默认成 2。"""
    if "call_key_version" not in production:
        production["call_key_version"] = resolve_call_key_version(production, run)


def _script_scene_command_id(
    run: Any,
    production: dict[str, Any],
    sp: dict[str, Any],
    phase: str,
) -> str:
    """分场/整章剧本臂在尚无 take 时的逻辑调用标识。

    必须区分：场次下标/scene_id、本场修订号、章级创作返工、决策数。
    否则不同场次会撞同一 CallBroker 幂等键，真实持久化链路上第二场无法开拍。

    章核验失败回退 WRITE_SCENE 时会带 previous_draft/revision_instruction，
    必须纳入 ``chapter_validate_repairs``，否则与首稿同键不同入参 → CallConflict。
    """
    scene_idx = int(sp.get("scene_index") or 0)
    plan = sp.get("scene_plan") or {}
    cards = plan.get("cards") or []
    scene_tag = ""
    if phase in ("WRITE_SCENE", "AUDIT_SCENE"):
        scene_id = ""
        if scene_idx < len(cards):
            scene_id = str((cards[scene_idx] or {}).get("scene_id") or f"i{scene_idx}")
        else:
            scene_id = f"i{scene_idx}"
        srep = int(sp.get(f"scene_repairs:{scene_id}") or 0)
        scene_tag = f":sc{scene_idx}:{scene_id}:srep{srep}"
    vrep = int(sp.get("chapter_validate_repairs") or 0)
    # F6：缺省为 1（旧格式）；新运行须显式写入 2
    ckv = resolve_call_key_version(production, run)
    vrep_tag = f":vrep{vrep}" if ckv != 1 or vrep > 0 else ""
    return (
        f"v{_input_version(run)}:script:{phase}{scene_tag}"
        f":rep{int(sp.get('creative_repairs') or 0)}"
        f"{vrep_tag}"
        f":dec{len(production.get('decisions', []))}"
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
    call_cap = _effective_call_cap(production)
    if count >= call_cap:
        raise BudgetExhausted("本章导演调用预算已耗尽，草稿已保留", kind="calls")
    # 预算按「已结算金额 + 本次估价」校验，不能用累计历史预留当可用额度：
    # 预留会被释放，累计值会虚高到把上限算穿（Plan v6.4 §10 P0-3）。
    committed = int(production.get("committed_minor", 0) or 0)
    remaining = _effective_cost_cap(production) - committed
    if remaining <= 0:
        raise BudgetExhausted("本章货币预算已耗尽，草稿已保留", kind="cost")

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
            temperature=0.8 if schema in (ActorTurn, SceneText, ChapterScript) else 0,
            model_hint=getattr(provider, "model_name", "") or "",
        )
    except QuotaExceeded as exc:
        # 预留阶段就超限：调用尚未发出，草稿保留（P0-3）。
        raise BudgetExhausted("本章货币预算已耗尽，草稿已保留", kind="cost") from exc
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
    call_cap = _effective_call_cap(production)
    if count + len(payloads) > call_cap:
        raise BudgetExhausted("本章导演调用预算已耗尽，草稿已保留", kind="calls")
    committed = int(production.get("committed_minor", 0) or 0)
    remaining = _effective_cost_cap(production) - committed
    if remaining <= 0:
        raise BudgetExhausted("本章货币预算已耗尽，草稿已保留", kind="cost")

    broker = CallBroker(lease_owner=f"run:{run.id}", budget_limit_minor=remaining)
    specs = [
        BatchCall(
            purpose=purpose,
            command_id=command_id,
            candidate_id=candidate_id,
            system_prompt=system,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            temperature=0.8 if schema in (ActorTurn, SceneText, ChapterScript) else 0,
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
        raise BudgetExhausted("本章货币预算已耗尽，草稿已保留", kind="cost") from exc

    for (candidate_id, payload), result in zip(payloads, results, strict=False):
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
        production["reserved_minor"] = production.get("reserved_minor", 0) + result.reserved_minor
        production["actual_minor"] = production.get("actual_minor", 0) + result.actual_minor
        if result.reused:
            production["reused_calls"] = production.get("reused_calls", 0) + 1
            production["avoided_minor"] = production.get("avoided_minor", 0) + result.avoided_minor
    production["call_count"] = count
    production["committed_minor"] = committed
    _save(run, production)
    return [result.output for result in results]

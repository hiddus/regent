"""一次性补丁：ACT 阶段接入 Hive 路由与并发表演。"""
from __future__ import annotations

import pathlib

path = pathlib.Path("core/src/regent/novel/application/direction.py")
text = path.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# 1) 导入 Hive
sub(
    "from regent.novel.domain.errors import ProductionStopped",
    "from regent.novel.domain.errors import ProductionStopped\nfrom regent.novel.domain.hive import route_beat",
)

# 2) 批量表演入口
sub(
    """
async def plan_chapter(""",
    '''
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


async def plan_chapter(''',
)

# 3) ACT 阶段改为按节拍路由
OLD_ACT = '''    if phase == "ACT":
        actor = brief["actors"][len(take["round_actions"])]
        name = actor["persona"]
        state = _runtime_state(production, run, take)
        target = _RUNTIME.validate(
            director_command(
                CommandKind.REQUEST_PERFORMANCE,
                command_id=_command_id(production, run, phase),
                input_version=_input_version(run),
                scene_index=production["scene_index"],
                take_no=take["take_no"],
                persona=name,
            ),
            state,
        )
        compiled = compile_actor_context(
            work_id=work.id,
            branch_id=run.branch_id,
            chapter_no=run.chapter_no,
            scene_index=production["scene_index"],
            take_no=take["take_no"],
            beat=take["turn"],
            persona=name,
            cast=production["cast"],
            direction=actor,
            setting=brief["setting"],
            canon=run.generation_context.get("canon", []),
            observations=_all_events(production, take),
            turn=take["turn"],
        )
        result = await call(
            ActorTurn,
            "扮演给定人物。只根据自身动机、已知事实和可观察事件提出行动及台词。"
            "行动是尝试，不能自行宣布成功。不要猜测别人的秘密或未来安排。"
            "表演指导若超出已知信息，不得将其当作新事实。",
            compiled.payload,
        )
        _record_manifest(take, compiled)
        action = {"persona": name, **result.model_dump(mode="json")}
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
            _apply_state(_RUNTIME, take, state, target)
'''

NEW_ACT = '''    if phase == "ACT":
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
'''

sub(OLD_ACT, NEW_ACT)

path.write_text(text, encoding="utf-8")
print("patched", path)

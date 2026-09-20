"""Scene and beat production loop."""

# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_calls import (
    _call,
    _call_batch,
    _command_id,
    _input_version,
    _save,
)
from regent.novel.application.directing_cast import (
    _admit_ambient_dialogue_speakers,
    _canonical_persona,
    _scrub_unknown_known_by,
)
from regent.novel.application.directing_contracts import (
    ActorTurn,
    ProseDirection,
    SceneResolution,
    SceneText,
    SceneTextPatch,
    SceneValidation,
    TakeDirection,
)
from regent.novel.application.directing_decisions import (
    _consume_user_decision,
    _pending_user_decision,
    _request_user_decision,
)
from regent.novel.application.directing_evidence import (
    _grounded_judgment,
    _stage_text,
)
from regent.novel.application.directing_issue_ledger import (
    _record_issue_ledger,
)
from regent.novel.application.directing_protocol import (
    PROTOCOL_BEAT,
    production_protocol,
)
from regent.novel.application.directing_requirements import (
    _catastrophic_prose_loss,
    _ensure_requirements,
    _substantive_validation_issues,
    _validation_report_defects,
    _working_state_from_requirements,
)
from regent.novel.application.directing_runtime import (
    _PROSE_COMMANDS,
    _RUNTIME,
    _TAKE_COMMANDS,
    MAX_REVISIONS,
    MAX_TURNS,
    MAX_VALIDATION_REPAIRS,
    _apply_state,
    _check_brief,
    _coerce_illegal_action,
    _extend_events,
    _legal_action_report,
    _new_take,
    _record_manifest,
    _runtime_state,
    _validate_action,
)
from regent.novel.application.directing_script_loop import (
    _produce_script_tick,
)
from regent.novel.application.directing_types import (
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.context import (
    compile_actor_context,
    compile_director_performance_context,
    compile_writer_context,
    public_performances,
)
from regent.novel.domain.errors import (
    ProductionStopped,
)
from regent.novel.domain.hive import route_beat
from regent.novel.domain.memory import project_payloads as _memory_view
from regent.novel.domain.prose_front_gates import (
    front_gate_writer_hint,
)
from regent.novel.domain.prose_patch import (
    PatchError,
    apply_patch,
    paragraphs_payload,
    split_paragraphs,
)
from regent.novel.domain.prose_patch import (
    content_hash as prose_content_hash,
)
from regent.novel.domain.scene_requirements import (
    must_preserve_for_writer,
    revision_conflicts_settled,
)
from regent.novel.domain.states import SceneArtifact, SceneRunState
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel


def _retake(production: dict[str, Any], take: dict[str, Any], decision: Any) -> None:
    if decision.revised_brief is None:
        raise ProductionStopped("重演必须提供修改后的场景指令")
    _check_brief(decision.revised_brief, production["cast"])
    # brief 是否与现行 brief 完全相同，已在 _validate_action 走命令校验通道。
    # 走到这里说明至少有字段被改过：直接落新 brief。
    brief = decision.revised_brief.model_dump(mode="json")
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
    if production_protocol(production) in {PROTOCOL_SCRIPT, PROTOCOL_SCRIPT_SCENE}:
        return await _produce_script_tick(
            session, provider=provider, work=work, run=run, production=production
        )
    if production["phase"] == "DONE":
        return True
    take = production["takes"][-1]
    brief = take["brief"]
    phase = production["phase"]
    prefix = f"scene{production['scene_index']}:take{take['take_no']}:{phase}"
    # D-03：六视角记忆投影只在此处按受众裁剪一次，装配器只负责留痕。
    memory_payloads = list(run.generation_context.get("memory", []) or [])

    async def call[T: BaseModel](
        schema: type[T], system: str, payload: dict[str, Any], *, repair_no: int = 0
    ) -> T:
        command_id = _command_id(production, run, phase)
        if repair_no:
            command_id = f"{command_id}:r{repair_no}"
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
            command_id,
        )

    if phase == "SCENE":
        # director_v2@2：导演 brief 已含人物行动约束，一次结算生成客观事件，
        # 再进入执笔；不跑 Hive / 逐节拍 WATCH_TAKE。
        result = await call(
            SceneResolution,
            "你负责按导演场景指令结算本场客观结果。"
            "actors 中的 intention/constraints 是导演给出的行动约束，不是已发生事实。"
            "依据客观事实和规则判定尝试产生的结果；不可为戏剧目标强行成功。"
            "列出实际事件、状态差异、谁获得信息和读者可见性。"
            "known_by只列 cast 中实际获知的人物；禁止写路人/职能名；缺席人物不能自动获知。"
            "读者可见性必须符合叙事视角与披露规则。"
            "将实际说出的关键台词保存在事件dialogue_by_character，保留人物声纹。"
            "规则冲突写rule_issues。",
            {
                "brief": brief,
                "cast": list(production["cast"].keys()),
                "canon": run.generation_context.get("canon", []),
                "state_before": take["state_before"],
                "events": _all_events(production, take),
                "actor_constraints": brief.get("actors", []),
                "memory": _memory_view(memory_payloads, "director"),
            },
        )
        cast = production["cast"]
        for event in result.events:
            event.known_by = [_canonical_persona(name, cast) or name for name in event.known_by]
            event.dialogue_by_character = {
                (_canonical_persona(name, cast) or name): lines
                for name, lines in event.dialogue_by_character.items()
            }
        dropped_known = _scrub_unknown_known_by(result.events, cast)
        if dropped_known:
            take.setdefault("scrubbed_known_by", [])
            take["scrubbed_known_by"] = list(
                dict.fromkeys(list(take.get("scrubbed_known_by") or []) + dropped_known)
            )
        admitted = _admit_ambient_dialogue_speakers(result.events, cast)
        if admitted:
            take.setdefault("admitted_extras", [])
            take["admitted_extras"] = list(
                dict.fromkeys(list(take.get("admitted_extras") or []) + admitted)
            )
        unknown_dialogue = sorted(
            {
                name
                for event in result.events
                for name in event.dialogue_by_character
                if name not in cast
            }
        )
        if unknown_dialogue:
            raise ProductionStopped("台词归属含未知人物：" + "、".join(unknown_dialogue))
        _extend_events(take, result.events)
        take["rule_issues"] = result.rule_issues
        take["render_instruction"] = (
            f"目的：{brief.get('purpose', '')}；"
            f"冲突：{brief.get('conflict', '')}；"
            f"退出：{brief.get('exit_condition', '')}"
        )
        if result.rule_issues:
            # 场景协议不逐拍重演；规则冲突留给正文审阅阶段显式处理。
            take.setdefault("deferred_rule_issues", [])
            take["deferred_rule_issues"] = list(
                dict.fromkeys(
                    list(take.get("deferred_rule_issues") or []) + list(result.rule_issues)
                )
            )
        _apply_state(
            _RUNTIME,
            take,
            _runtime_state(production, run, take),
            (SceneRunState.RENDERING.value, ""),
        )
        production["phase"] = "RENDER"
    elif phase == "ACT":
        if production_protocol(production) != PROTOCOL_BEAT:
            raise ProductionStopped("场景协议不得进入逐节拍 ACT；请使用 SCENE 阶段")
        remaining_actors = brief["actors"][len(take["round_actions"]) :]
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
            outputs = [await call(ActorTurn, system, batch[0].payload)]
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
        for compiled, result in zip(batch, outputs, strict=False):
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
            _apply_state(_RUNTIME, take, state, (SceneRunState.PERFORMING.value, ""))
    elif phase == "RESOLVE":
        if production_protocol(production) != PROTOCOL_BEAT:
            raise ProductionStopped("场景协议不得进入 RESOLVE；请使用 SCENE 阶段")
        result = await call(
            SceneResolution,
            "你负责场景行动结算。依据客观事实和规则判定尝试产生的结果。"
            "不可为戏剧目标强行成功。列出实际事件、状态差异、谁获得信息和读者可见性。"
            "known_by只列 cast 中实际获知的人物；禁止写路人/职能名；缺席人物不能自动获知。"
            "读者可见性必须符合叙事视角与披露规则。"
            "将实际说出的关键台词保存在事件dialogue_by_character，保留人物声纹。"
            "规则冲突写rule_issues。",
            {
                "brief": brief,
                "cast": list(production["cast"].keys()),
                "canon": run.generation_context.get("canon", []),
                "state_before": take["state_before"],
                "events": take["events"],
                "actions": public_performances(take["round_actions"]),
            },
        )
        # 归一：模型可能写"陈父"而 cast 键是"陈父（陈远舟）"——去掉末尾括号
        # 就能确定性匹配。不归一直接判死会把可修的问题变成不可逆的整章消失。
        cast = production["cast"]
        for event in result.events:
            event.known_by = [_canonical_persona(name, cast) or name for name in event.known_by]
            event.dialogue_by_character = {
                (_canonical_persona(name, cast) or name): lines
                for name, lines in event.dialogue_by_character.items()
            }
        dropped_known = _scrub_unknown_known_by(result.events, cast)
        if dropped_known:
            take.setdefault("scrubbed_known_by", [])
            take["scrubbed_known_by"] = list(
                dict.fromkeys(list(take.get("scrubbed_known_by") or []) + dropped_known)
            )
        admitted = _admit_ambient_dialogue_speakers(result.events, cast)
        if admitted:
            take.setdefault("admitted_extras", [])
            take["admitted_extras"] = list(
                dict.fromkeys(list(take.get("admitted_extras") or []) + admitted)
            )
        unknown_dialogue = sorted(
            {
                name
                for event in result.events
                for name in event.dialogue_by_character
                if name not in cast
            }
        )
        if unknown_dialogue:
            raise ProductionStopped("台词归属含未知人物：" + "、".join(unknown_dialogue))
        _extend_events(take, result.events)
        take["rule_issues"] = result.rule_issues
        _apply_state(
            _RUNTIME,
            take,
            _runtime_state(production, run, take),
            (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PERFORMANCE.value),
        )
        production["phase"] = "WATCH_TAKE"
    elif phase == "WATCH_TAKE":
        if production_protocol(production) != PROTOCOL_BEAT:
            raise ProductionStopped("场景协议不逐拍观看表演；请在 WATCH_PROSE 取舍")
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
        checked: dict[str, Any] = {}
        coerced: list[str] = []

        def _coerce_take(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take, production))

        def _check_take(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )

        def _report_take(result: Any) -> list[str]:
            return _legal_action_report(
                production, run, take, phase, result, _TAKE_COMMANDS, brief["actors"]
            )

        result = await _grounded_judgment(
            call,
            TakeDirection,
            "你是导演，观看实际演绎，判断人物选择和场景效果。"
            "决定CONTINUE推进下一节拍、RETAKE改变调度重演、RENDER结束表演进入小说呈现。"
            "evidence须给出可定位的场景依据（关键短语即可），禁止凭空捏造。"
            "重演必须给出不同的revised_brief。"
            "有rule_issues必须重演；不要把自己变成打分编辑。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "推进，不得执行其他选项的走向，也不得就同一件事再次请求裁决。",
            watch.payload,
            _stage_text(take),
            "观看表演",
            coerce=_coerce_take,
            command_check=_check_take,
            command_report=_report_take,
        )
        _record_manifest(take, watch)
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **({"coerced": coerced} if coerced else {}),
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
        state, target = checked["state"], checked["target"]
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
        requirements = _ensure_requirements(take)
        preserve = must_preserve_for_writer(requirements)
        revision_mode = take.get("revision_mode") or (
            "patch" if take.get("content") and take.get("revision_instruction") else "full"
        )
        # 有旧稿的修订一律补丁路径，忽略误选的 SceneText 整场覆盖。
        if take.get("content") and take.get("revision_instruction"):
            revision_mode = take.get("revision_mode") or "patch"
            if revision_mode not in {"patch", "full"}:
                revision_mode = "patch"
            # full/patch 都走 SceneTextPatch；区别只在提示是否鼓励改全段
            use_patch = True
        else:
            use_patch = False
        paragraphs = take.get("prose_paragraphs") or []
        base_hash = take.get("prose_base_hash") or ""
        if use_patch and take.get("content") and (not paragraphs or not base_hash):
            paras = split_paragraphs(take["content"])
            paragraphs = paragraphs_payload(paras)
            base_hash = prose_content_hash(take["content"])
            take["prose_paragraphs"] = paragraphs
            take["prose_base_hash"] = base_hash
        editable = [p["paragraph_id"] for p in paragraphs] if paragraphs else []
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
            revision_mode="full" if revision_mode == "full" else "patch",
            base_content_hash=base_hash,
            paragraphs=paragraphs,
            editable_paragraph_ids=editable,
            must_preserve=preserve,
        )
        render_payload = dict(prose.payload)
        commons_rails = list(run.generation_context.get("commons_rails") or [])
        if commons_rails:
            render_payload["commons_rails"] = [
                {"rule_id": r.get("rule_id"), "statement": r.get("statement")}
                for r in commons_rails
            ]
        prose_style = run.generation_context.get("prose_style") or {}
        if prose_style:
            render_payload["prose_style"] = prose_style
        bible_block = run.generation_context.get("story_bible_block") or ""
        style_hint = front_gate_writer_hint(
            prose_style=prose_style if isinstance(prose_style, dict) else {},
            chapter_no=int(run.chapter_no),
            cast=(production.get("cast") if isinstance(production, dict) else None) or {},
            world_bible=(
                run.generation_context.get("world_bible")
                if isinstance(run.generation_context.get("world_bible"), dict)
                else None
            ),
            reader_contract=(
                run.generation_context.get("reader_contract")
                if isinstance(run.generation_context.get("reader_contract"), dict)
                else None
            ),
        )
        if bible_block:
            style_hint = (style_hint + " " + bible_block).strip()
        conflicts = revision_conflicts_settled(take.get("revision_instruction", ""), requirements)
        if conflicts:
            take.setdefault("revision_conflict_notices", [])
            take["revision_conflict_notices"] = list(
                dict.fromkeys(take.get("revision_conflict_notices", []) + conflicts)
            )

        if use_patch and take.get("content"):
            scope = (
                "允许替换全部段落（完整重写），但仍必须返回 SceneTextPatch；"
                if revision_mode == "full"
                else "只替换修订指令涉及的段落，未列出的段落由系统原样保留；"
            )
            patch_system = (
                "你是小说执笔者，正在对已有场景做修订。"
                "必须返回 SceneTextPatch：带上给定的 base_content_hash，"
                "并用 paragraph_ids 指出要替换的段落与替换文本。"
                + scope
                + "禁止只输出后半段当作整场正文。"
                "必须覆盖 must_preserve 中的可见最终状态（可压缩表达，不得删除成立依据）。"
                "禁止新增重大事件、能力、人物或知识。只能使用给定材料。"
                "若修订涉及外挂/系统/鱼塘等术语：补上读者向白话释义（能做/不能做或代价），"
                "不要只改文风而留下黑话。"
                + (
                    "若 payload 含 commons_rails，呈现须符合器物规则与本作公约。"
                    if commons_rails
                    else ""
                )
                + (style_hint if style_hint else "")
            )
            patch_result = await call(
                SceneTextPatch,
                patch_system,
                render_payload,
            )
            _record_manifest(take, prose)
            try:
                merged = apply_patch(
                    base_text=take["content"],
                    base_hash=base_hash,
                    paragraphs=paragraphs,
                    patch_hash=patch_result.base_content_hash,
                    replacements=[r.model_dump(mode="json") for r in patch_result.replacements],
                    # full：全部可改；patch：仅 editable 列表（越界拒绝）。
                    allowed_paragraph_ids=(None if revision_mode == "full" else editable),
                )
                loss = _catastrophic_prose_loss(take["content"], merged, paragraphs)
                if loss:
                    raise PatchError(loss)
            except PatchError as exc:
                # 补丁无效：不覆盖旧稿；记入诊断后回到导演（计一次修订已发生）
                take["patch_error"] = str(exc)
                take.pop("validation", None)
                _apply_state(
                    _RUNTIME,
                    take,
                    _runtime_state(production, run, take),
                    (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
                )
                production["phase"] = "WATCH_PROSE"
            else:
                take["last_patch"] = patch_result.model_dump(mode="json")
                take["content"] = merged.strip()
                take["prose_versions"].append(take["content"])
                take.pop("validation", None)
                take.pop("patch_error", None)
                # 合并后刷新段落表，供下一轮局部修订
                take["prose_paragraphs"] = paragraphs_payload(split_paragraphs(take["content"]))
                take["prose_base_hash"] = prose_content_hash(take["content"])
                take["revision_mode"] = ""
                _apply_state(
                    _RUNTIME,
                    take,
                    _runtime_state(production, run, take),
                    (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
                )
                production["phase"] = "WATCH_PROSE"
        else:
            result = await call(
                SceneText,
                "你是小说执笔者。按叙事指令将已发生的可见事件呈现为小说。"
                "控制视角、内心、详略和语言，不写剧本或导演说明。"
                "禁止新增重大事件、能力、人物或知识。只能使用给定材料。"
                "衔接前场结尾，避免重复复述；按目标字数形成完整场景。"
                "必须覆盖 must_preserve 中的可见最终状态（可压缩表达，不得删除成立依据）。"
                "输出必须是完整场景正文，不能只交局部片段。"
                "若本场首次出现外挂/系统/资源池/鱼塘类术语：必须用一句读者向白话释义"
                "（能做什么、不能做什么或代价来源），禁止只甩面板名与数值当读者已懂；"
                "反说明书≠不解释，要嵌入角色感知，不要条款列表。"
                + (
                    "若 payload 含 commons_rails，呈现须符合器物规则与本作公约。"
                    if commons_rails
                    else ""
                )
                + (style_hint if style_hint else ""),
                render_payload,
            )
            _record_manifest(take, prose)
            take["content"] = result.content.strip()
            take["prose_versions"].append(take["content"])
            take.pop("validation", None)
            take["prose_paragraphs"] = paragraphs_payload(split_paragraphs(take["content"]))
            take["prose_base_hash"] = prose_content_hash(take["content"])
            take["revision_mode"] = ""
            _apply_state(
                _RUNTIME,
                take,
                _runtime_state(production, run, take),
                (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
            )
            production["phase"] = "WATCH_PROSE"
    elif phase == "WATCH_PROSE":
        checked: dict[str, Any] = {}
        coerced: list[str] = []

        def _coerce_prose(result: Any) -> None:
            coerced.clear()
            coerced.extend(_coerce_illegal_action(phase, result, take, production))

        def _check_prose(result: Any) -> None:
            checked["state"], checked["target"] = _validate_action(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )

        def _report_prose(result: Any) -> list[str]:
            return _legal_action_report(
                production, run, take, phase, result, _PROSE_COMMANDS, brief["actors"]
            )

        result = await _grounded_judgment(
            call,
            ProseDirection,
            "你是导演，观看小说呈现是否实现本场阅读体验、潜台词和人物情绪。"
            "给出具体观察和可定位的正文依据（关键短语即可），决定ACCEPT、REWRITE表达或RETAKE。"
            "指出缺失可用「全文无…」（所缺须确实未出现）；肯定性依据仍须摘自原文。"
            "REWRITE必须给出具体不同的呈现指令；有已有正文时系统用段落补丁合并，"
            "不得指望只交后半段覆盖整场。revision_mode=full 表示可改全部段落，仍走补丁。"
            "RETAKE必须给出不同的revised_brief。"
            "独立审校指出硬问题时不得ACCEPT。不要以文风评分代替创作决策。"
            "若存在 deferred_rule_issues，必须先处理（REWRITE 或 RETAKE），不得直接 ACCEPT。"
            "若 validation.issues 含 [editor:…] 或责编/整章审校问题：必须先 REWRITE/RETAKE 消掉，"
            "不得以『界面已出现/抽取已完成』为由 ACCEPT；读者须听懂术语与机制代价。"
            "若 user_decision 非空，用户已就这件事作出裁决：必须按其 near_term_consequence "
            "取舍，不得执行其他选项的走向，也不得就同一件事再次请求裁决。",
            {
                "brief": brief,
                "draft": take["content"],
                "events": take["events"],
                "validation": take.get("validation"),
                "issue_ledger": take.get("issue_ledger") or [],
                "patch_error": take.get("patch_error"),
                "deferred_rule_issues": take.get("deferred_rule_issues")
                or take.get("rule_issues")
                or [],
                "remaining_revisions": MAX_REVISIONS - take["revisions"],
                "user_decision": _pending_user_decision(run),
                # D-03：导演审阅正文同样只拿导演视图记忆。
                "director_memory": _memory_view(memory_payloads, "director"),
            },
            take["content"],
            "审阅正文",
            coerce=_coerce_prose,
            command_check=_check_prose,
            command_report=_report_prose,
        )
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **({"coerced": coerced} if coerced else {}),
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
        state, target = checked["state"], checked["target"]
        if result.action == "RETAKE":
            _retake(production, take, result)
        elif result.action == "REWRITE":
            if result.instruction == take.get("revision_instruction"):
                raise ProductionStopped("呈现修订重复了无效指令")
            take["revisions"] += 1
            take["revision_instruction"] = result.instruction
            # 有已有正文时强制走补丁合并；full 只表示「可改全部段落」，不打开 SceneText 覆盖。
            if take.get("content"):
                take["revision_mode"] = "full" if result.revision_mode == "full" else "patch"
                paras = split_paragraphs(take["content"])
                take["prose_paragraphs"] = paragraphs_payload(paras)
                take["prose_base_hash"] = prose_content_hash(take["content"])
            else:
                take["revision_mode"] = "full"
            _ensure_requirements(take)
            _apply_state(_RUNTIME, take, state, target)
            production["phase"] = "RENDER"
        else:
            _apply_state(_RUNTIME, take, state, target)
            production["phase"] = "VALIDATE"
    elif phase == "VALIDATE":
        requirements = _ensure_requirements(take)
        draft = take["content"]
        content_sha = prose_content_hash(draft)
        base_payload = {
            "draft": draft,
            "content_hash": content_sha,
            "requirements": requirements,
            "events": take["events"],
            "brief": brief,
            "prior_events": _all_events(production, take),
            "canon": run.generation_context.get("canon", []),
            "revision_conflict_notices": take.get("revision_conflict_notices") or [],
        }
        repair: list[str] = []
        result: SceneValidation | None = None
        report_defects: list[str] = []
        for _attempt in range(1 + MAX_VALIDATION_REPAIRS):
            payload = dict(base_payload)
            if repair:
                payload["repair_instructions"] = list(repair)
            result = await call(
                SceneValidation,
                "独立核验正文与已结算事件、Canon、人物知情范围及叙事披露一致。"
                "对每个 requirement_id 给出 supported/contradicted/missing，并提供当前正文 quote。"
                "自然语言 explanation 只作解释，不承担状态身份比较。"
                "禁止引用事件计划、旧稿或不在当前 draft 中的句子；quote 必须能在当前正文中定位。"
                "隐藏/不要求直接证据的 requirement 不要强迫正文披露。"
                "同义动作可算覆盖：结算写『眉头动了一下』而正文是『转笔停了一拍/视线抬起又落下』"
                "这类可观察停顿/反应，应判 supported，不得因字面用词不同标 missing。"
                "issues 只列真实硬问题；禁止把『符合要求』『已覆盖』等通过说明写入 issues——"
                "那些内容写在 explanation。全部 supported 且无真实问题时 issues 必须为空、passed=true。"
                "抽取有逐字quote的事实。你不负责戏剧决策。",
                payload,
                # 自修必须换 command_id，否则不同 repair_instructions 撞同一幂等键。
                repair_no=_attempt,
            )
            assert result is not None
            report_defects = _validation_report_defects(
                result,
                draft,
                requirements,
                protocol_version=str(take.get("requirements_version") or ""),
            )
            if not report_defects:
                break
            repair = [
                *repair,
                "上一版核验报告无效，不得据此认定正文缺失：",
                *[f"- {item}" for item in report_defects],
                f"当前正文 content_hash={content_sha}；请只引用该稿中的句子，并覆盖全部 requirement_id。",
            ]
        assert result is not None
        if report_defects:
            issues = ["核验报告无效（自修已用尽）：" + "；".join(report_defects[:8])]
            passed = False
            take["validation"] = {
                **result.model_dump(mode="json"),
                "passed": False,
                "issues": issues,
                "report_invalid": True,
                "content_hash": content_sha,
            }
            _record_issue_ledger(take, content_hash=content_sha, validation=take["validation"])
            _apply_state(
                _RUNTIME,
                take,
                _runtime_state(production, run, take),
                (SceneRunState.DIRECTOR_VIEW.value, SceneArtifact.PROSE.value),
            )
            production["phase"] = "WATCH_PROSE"
        else:
            issues = _substantive_validation_issues(result, draft, requirements, production["cast"])
            if take.get("revision_conflict_notices"):
                # 冲突通知给导演看，本身不自动改写；若对应 requirement 为 missing 已在 issues 中
                pass
            # 独立常识审校：确定性硬门 + 触发式 LLM（无综合分）
            from regent.novel.application import commons_audit as ca
            from regent.novel.domain.genre_packs import active_rule_ids

            commons_rails = list(run.generation_context.get("commons_rails") or [])
            det = ca.deterministic_commons_issues(
                draft,
                commons_rails=commons_rails,
                chapter_no=int(run.chapter_no),
            )
            issues = ca.merge_commons_into_issues(issues, det)
            if ca.should_run_llm_commons_audit(
                draft, commons_rails=commons_rails, deterministic_hits=det
            ):
                audit = await call(
                    ca.CommonsAuditResult,
                    ca.commons_audit_system_prompt(),
                    {
                        "draft": draft,
                        "commons_rails": commons_rails,
                        "principle_lenses": run.generation_context.get("principle_lenses") or [],
                        "chapter_no": int(run.chapter_no),
                        "brief": brief,
                    },
                    repair_no=90,
                )
                issues = ca.merge_commons_into_issues(
                    issues,
                    ca.issues_from_llm_result(
                        audit,
                        allowed_rule_ids=active_rule_ids(commons_rails),
                        advisory_rule_ids=ca.advisory_rule_ids(commons_rails),
                        deterministic_hits=det,
                    ),
                )
            passed = result.passed and not issues and bool(result.facts)
            take["validation"] = {
                **result.model_dump(mode="json"),
                "passed": passed,
                "issues": issues,
                "commons_issues": [x for x in issues if x.startswith("[commons:")],
                "content_hash": content_sha,
                "requirements_version": take.get("requirements_version"),
            }
            _record_issue_ledger(take, content_hash=content_sha, validation=take["validation"])
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
                verified_changes = _working_state_from_requirements(requirements, result)
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

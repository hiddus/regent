"""Chapter review and bounded repair."""

# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from regent.model import ModelProvider
from regent.novel.application.directing_budget import (
    effective_call_cap as _budget_call_cap,
)
from regent.novel.application.directing_protocol import (
    production_protocol,
)
from regent.novel.application.directing_types import (
    MAX_CHAPTER_REPAIRS,
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
)
from regent.novel.application.runtime import (
    RuntimeState,
)
from regent.novel.domain.commands import CommandKind
from regent.novel.domain.commands import command as director_command
from regent.novel.domain.errors import (
    ProductionStopped,
)
from regent.novel.domain.prose_front_gates import (
    REVIEW_COERCE_PREFIXES,
    front_gate_hard_fails,
    review_issues_may_coerce,
)
from regent.novel.domain.script_protocol import (
    empty_script_state,
)
from regent.novel.domain.states import SceneArtifact, SceneRunState
from regent.novel.domain.world_bible import (
    resolve_prose_style,
)
from regent.novel.infrastructure.models import ChapterRunModel, StoryWorkModel
from regent.novel.application.directing_calls import (
    _call,
    _input_version,
    _save,
)
from regent.novel.application.directing_contracts import (
    ChapterValidation,
)
from regent.novel.application.directing_runtime import (
    _RUNTIME,
)
from regent.novel.application.directing_scene_loop import (
    _new_take,
)

_effective_call_cap = _budget_call_cap

# 创作已通过后，整章审校最多回退补拍次数；再遇可 soft 意见直接 coerce。
_MAX_POST_CREATIVE_REVIEW_REWINDS = 1


def _review_issues_soft_after_creative(issues: list[Any] | None) -> bool:
    """创作验收已通过后：仅结构化顾问标签可 coerce。

    复读、跨段事实「连续」冲突、自然语言「跳场/入场」不得 soft。
    """
    return review_issues_may_coerce(issues)


def _top_review_fix_notes(issues: list[Any] | None, *, limit: int = 3) -> list[str]:
    """回退补拍只带少量互不雷同的事实点，避免几十条意见淹没模型。"""
    out: list[str] = []
    seen: list[str] = []
    for raw in issues or []:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text[:48]
        if any(key[:24] in s or s[:24] in key for s in seen):
            continue
        seen.append(key)
        out.append(text[:120])
        if len(out) >= limit:
            break
    return out


def _review_unchanged_may_soft(issues: list[Any] | None) -> bool:
    """创作已过且正文未变时：仅窄 soft 意见可放行。"""
    return _review_issues_soft_after_creative(issues)


def _coerce_review_soft(
    run: Any,
    production: dict[str, Any],
    issues: list[Any],
    *,
    flag: str,
) -> None:
    prior = dict(run.review or {})
    run.review["passed"] = True
    run.review[flag] = True
    run.review["soft_continuity"] = list(issues)
    run.review["verdict_original"] = {
        "passed": prior.get("passed"),
        "continuity_issues": prior.get("continuity_issues") or [],
        "coerce_flag": flag,
        "coerce_reason": "creative_accept_or_advisory_only",
    }
    run.review["accept_decision"] = "coerced_soft"
    run.review["coerce_reason"] = flag
    _save(run, production)


def _rewind_script_after_review(
    run: Any,
    production: dict[str, Any],
    *,
    issues: list[str],
    failed_scene_index: int | None,
    proto: str,
) -> bool:
    """整章审校失败后按协议回退，禁止落入不在阶段表中的 WATCH_PROSE。

    - script_scene：优先 repair_locate 定位；仅在无法定位时回退 failed_scene_index。
    - script：回到 WRITE_CHAPTER 带意见修订整章。
    """
    from regent.novel.domain.repair_locate import (
        apply_repair_target_to_script_state,
        locate_fail_messages,
        repair_target_from_scene_index,
        select_repair_target,
    )

    sp = production.setdefault("script_protocol", empty_script_state())
    top = _top_review_fix_notes(issues, limit=3)
    note = "；".join(top) if top else "整章未通过独立审校"
    if proto == PROTOCOL_SCRIPT_SCENE:
        plan = sp.get("scene_plan") or {}
        cards = list(plan.get("cards") or [])
        texts = list(sp.get("scene_texts") or [])
        located = locate_fail_messages(
            list(issues or []),
            scene_texts=texts,
            cards=cards,
            chapter_content=str(run.content or ""),
        )
        target = select_repair_target(
            located,
            cards=cards,
            scene_texts=texts,
            instruction_prefix="整章审校失败，需补拍本场",
        )
        if not target.locatable:
            # 仅当审校给出合法下标时兼容回退；缺失/越界 → 明确停机，禁止默认首场
            if failed_scene_index is None:
                raise ProductionStopped(
                    "整章审校失败且无法局部定位（缺少 failed_scene_index）：" + note
                )
            target = repair_target_from_scene_index(
                scene_index=failed_scene_index,
                cards=cards,
                issues=located,
                instruction_prefix="整章审校失败，需补拍本场：" + note,
            )
            if not target.locatable:
                raise ProductionStopped(target.instruction)
        # 纳入幂等键：与 VALIDATE 回退共用 vrep
        sp["chapter_validate_repairs"] = int(sp.get("chapter_validate_repairs") or 0) + 1
        apply_repair_target_to_script_state(sp, target)
        texts_after = list(sp.get("scene_texts") or [])
        idx = int(sp.get("scene_index") or 0)
        if texts_after and idx != len(texts_after) - 1:
            raise ProductionStopped(
                f"审校回退后 scene_index={idx} 与 scene_texts 长度="
                f"{len(texts_after)} 不一致"
            )
        sid = target.scene_id
        # 审校回退给本场一次干净补拍额度；勿累加 scene_repairs 直接吃光 AUDIT 预算。
        sp[f"scene_repairs:{sid}"] = 0
        sp["post_review_scene_id"] = sid
        sp["post_review_rewrite"] = True
        sp["located_issues"] = [i.as_dict() for i in (located or target.issues)]
        # 整章 take 失效，避免旧正文被再次验收
        for take in production.get("takes") or []:
            take["status"] = "SUPERSEDED"
        production["accepted"] = []
        production["working_state"] = deepcopy(sp.get("working_state") or {})
        production["phase"] = "WRITE_SCENE"
    else:
        take = (production.get("takes") or [None])[-1]
        if take is not None:
            take["revision_instruction"] = f"整章审校失败：{note}"[:400]
            take["status"] = "DRAFT"
        production["phase"] = "WRITE_CHAPTER"
    _save(run, production)
    return False


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
        "失败时：issues 每条尽量带可检索摘录（用「摘录「…」」格式）；"
        "failed_scene_index 填最早受影响场景下标（从0开始，相对分场顺序）。"
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
    # 章级常识审校（装备 commons_rails 时）
    from regent.novel.application import commons_audit as ca
    from regent.novel.domain.genre_packs import active_rule_ids

    commons_rails = list(run.generation_context.get("commons_rails") or [])
    det = ca.deterministic_commons_issues(
        run.content,
        commons_rails=commons_rails,
        chapter_no=int(run.chapter_no),
    )
    issues = ca.merge_commons_into_issues(issues, det)
    if ca.should_run_llm_commons_audit(
        run.content, commons_rails=commons_rails, deterministic_hits=det
    ):
        audit = await _call(
            session,
            provider,
            work,
            run,
            production,
            ca.CommonsAuditResult,
            ca.commons_audit_system_prompt(),
            {
                "chapter": run.content,
                "commons_rails": commons_rails,
                "principle_lenses": run.generation_context.get("principle_lenses") or [],
                "chapter_no": int(run.chapter_no),
            },
            "chapter_commons_audit",
            f"v{_input_version(run)}:chapter_commons:"
            f"{hashlib.sha256(run.content.encode()).hexdigest()[:12]}",
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
    commons_only = [x for x in issues if x.startswith("[commons:")]
    # 前置硬门：即便非 script 臂，也不把明显错稿交给责编硬审

    world = run.generation_context.get("world_bible") or {}
    if not isinstance(world, dict):
        world = {}
    production_cast = (production.get("cast") or {}) if isinstance(production, dict) else {}
    style = (
        (production.get("prose_style") if isinstance(production, dict) else None)
        or resolve_prose_style(run.generation_context)
        or world.get("prose_style")
        or {}
    )
    packet = {}
    if isinstance(production, dict):
        packet = (production.get("script_protocol") or {}).get("production_packet") or {}
    front_fails = front_gate_hard_fails(
        str(run.content or ""),
        prose_style=style if isinstance(style, dict) else {},
        title=str(getattr(run, "title", "") or ""),
        cast=production_cast,
        production_packet=packet if isinstance(packet, dict) else {},
        chapter_no=int(run.chapter_no),
        world_bible=world if isinstance(world, dict) else None,
        reader_contract=(
            world.get("reader_contract")
            if isinstance(world.get("reader_contract"), dict)
            else (
                run.generation_context.get("reader_contract")
                if isinstance(run.generation_context.get("reader_contract"), dict)
                else None
            )
        ),
    )
    if front_fails:
        issues = list(dict.fromkeys([*issues, *front_fails]))
    # 专职责编：默认抽样，避免每章固定付费却只有 soft 记录
    from regent.novel.application import editor_audit as ea

    editor_issues: list[str] = []
    soft_only: list[str] = []
    if ea.should_run_editor_audit(chapter_no=int(run.chapter_no), front_fails=front_fails):
        editor = await _call(
            session,
            provider,
            work,
            run,
            production,
            ea.EditorAuditResult,
            ea.editor_system_prompt(chapter_no=int(run.chapter_no)),
            ea.editor_payload(
                chapter=run.content,
                chapter_no=int(run.chapter_no),
                title=str(
                    getattr(run, "title", "") or (production.get("plan") or {}).get("title") or ""
                ),
                cast=production_cast,
                power_system=str(
                    world.get("power_system") or run.generation_context.get("power_system") or ""
                ),
                reader_contract=dict(
                    world.get("reader_contract")
                    or run.generation_context.get("reader_contract")
                    or {}
                ),
                dramatic_engine=dict(
                    world.get("dramatic_engine")
                    or run.generation_context.get("dramatic_engine")
                    or {}
                ),
                prose_style=dict(
                    world.get("prose_style") or run.generation_context.get("prose_style") or {}
                ),
            ),
            "chapter_editor_audit",
            f"v{_input_version(run)}:chapter_editor:"
            f"{hashlib.sha256(run.content.encode()).hexdigest()[:12]}",
        )
        editor_issues = ea.issues_from_editor_result(
            editor, hard_only=True, chapter=str(run.content or "")
        )
        soft_only = ea.issues_from_editor_result(
            editor, soft_only=True, chapter=str(run.content or "")
        )
    else:
        soft_only = [
            "[editor-soft:skipped] 责编抽样跳过本轮（NOVEL_EDITOR_AUDIT=sample；"
            "硬门与连续性已由 VALIDATE/ChapterValidation 覆盖）"
        ]
    # 大纲脏名：审校句里点到非卡司人名、且宿主叙述名已在正文时，降 soft（不作事实硬拦）。
    # 前置硬门 [front:]/[pov:] 不得被这条 soft 掉。
    from regent.novel.domain.dossiers import resolve_narration_roles

    roles = resolve_narration_roles(
        prose_style=style if isinstance(style, dict) else {},
        cast=production_cast,
        world_bible=world if isinstance(world, dict) else None,
    )
    host_name = str(roles.get("host_name") or "")
    cast_names = set(roles.get("cast_names") or [])
    cleaned_issues: list[str] = []
    for item in list(dict.fromkeys(issues + editor_issues)):
        text = str(item)
        if text.startswith(("[front:", "[pov:")):
            cleaned_issues.append(item)
            continue
        soft_outline = False
        if host_name and host_name in (run.content or ""):
            for ghost in re.findall(r"[\u4e00-\u9fff]{2,4}", text):
                if (
                    ghost not in cast_names
                    and ghost != host_name
                    and ghost in text
                    and ghost not in (run.content or "")
                    and ("目标" in text or "节点" in text or "大纲" in text or "角色" in text)
                ):
                    soft_outline = True
                    break
        if soft_outline:
            soft_only = [*list(soft_only), f"[soft-outline]{text}"]
            continue
        cleaned_issues.append(item)
    issues = cleaned_issues
    front_only = [x for x in issues if str(x).startswith(("[front:", "[pov:"))]
    run.review = {
        "passed": result.passed and not issues,
        "continuity_issues": issues,
        "prose_issues": [],
        "leakage_issues": [],
        "commons_issues": commons_only,
        "front_gate_issues": front_only,
        "editor_issues": editor_issues,
        "editor_soft_issues": soft_only,
        "revised": bool(production.get("chapter_repairs"))
        or any(len(take["prose_versions"]) > 1 for take in production["takes"]),
    }
    if not run.review["passed"]:
        content_hash = hashlib.sha256(run.content.encode()).hexdigest()
        sp = production.get("script_protocol") or {}
        creative_ok = bool((sp.get("creative_accept") or {}).get("accept"))
        soft_ok = _review_issues_soft_after_creative(issues) or review_issues_may_coerce(
            issues
        )
        post_rewinds = int(production.get("post_creative_review_rewinds") or 0)

        # 创作已过：可 soft 意见直接放行，禁止再进 WRITE↔AUDIT 空烧。
        if creative_ok and (run.content or "").strip() and soft_ok:
            _coerce_review_soft(
                run, production, issues, flag="coerced_after_creative"
            )
        elif production.get("last_failed_hash") == content_hash:
            # 正文未变：可 coerce 则放行，否则停机（再烧只会重复）。
            if creative_ok and (run.content or "").strip() and soft_ok:
                _coerce_review_soft(
                    run, production, issues, flag="coerced_unchanged"
                )
            else:
                raise ProductionStopped("修复没有改变正文，停止重试")
        else:
            production["last_failed_hash"] = content_hash
            repairs = production.get("chapter_repairs", 0)
            if repairs >= MAX_CHAPTER_REPAIRS or (
                creative_ok and post_rewinds >= _MAX_POST_CREATIVE_REVIEW_REWINDS
            ):
                if creative_ok and (run.content or "").strip() and soft_ok:
                    _coerce_review_soft(
                        run, production, issues, flag="coerced_after_repairs"
                    )
                else:
                    raise ProductionStopped("章节修复次数已达上限，保留场景及证据")
            if not run.review.get("passed"):
                production["chapter_repairs"] = repairs + 1
                if creative_ok:
                    production["post_creative_review_rewinds"] = post_rewinds + 1
                index = result.failed_scene_index
                # 保留 None：不得夹成 0 制造伪定位
                proto = production_protocol(production)
                if proto in {PROTOCOL_SCRIPT, PROTOCOL_SCRIPT_SCENE}:
                    # script_scene：failed_scene_index 相对分场 cards，不是整章 take 的 accepted。
                    return _rewind_script_after_review(
                        run,
                        production,
                        issues=issues or ["整章未通过独立审校"],
                        failed_scene_index=index,
                        proto=proto,
                    )
                if index is None or index < 0 or index >= len(production["accepted"]):
                    raise ProductionStopped("审校返回无效的场景下标")
                old = production["takes"][production["accepted"][index]]
                # 回退该场及之后的全部 take（含从未接受的 REJECTED），避免旧 take 占满重演预算。
                for take in production["takes"]:
                    if int(take.get("scene_index", -1)) >= index:
                        take["status"] = "SUPERSEDED"
                production["accepted"] = production["accepted"][:index]
                production["scene_index"] = index
                production["working_state"] = deepcopy(old["state_before"])
                _new_take(
                    production,
                    old["brief"],
                    SceneRunState.DIRECTOR_VIEW.value,
                    SceneArtifact.PROSE.value,
                )
                current = production["takes"][-1]
                editor_notes = [x for x in (issues or []) if "[editor:" in str(x)]
                repair_note = "；".join(
                    (issues or ["整章未通过独立审校"])[:6]
                )[:400]
                current.update(
                    {
                        "events": deepcopy(old["events"]),
                        "content": old["content"],
                        "prose_versions": [old["content"]],
                        "validation": {
                            "passed": False,
                            "issues": issues or ["整章未通过独立审校"],
                        },
                        "revision_instruction": (
                            "整章责编/审校未通过，必须改稿消掉下列问题后再提交："
                            + (
                                "；".join(editor_notes[:5])
                                if editor_notes
                                else repair_note
                            )
                        )[:500],
                        "revisions": 0,
                        "revision_mode": "full",
                    }
                )
                production["phase"] = "WATCH_PROSE"
                _save(run, production)
                return False
    # 完成也是一条命令：只在整章通过独立审校后才允许（P1-1）
    if run.review.get("passed"):
        from regent.novel.application.chapter_finalize import run_editorial_then_accept

        async def _editorial_call(schema, system, payload, purpose, command_id):
            return await _call(
                session,
                provider,
                work,
                run,
                production,
                schema,
                system,
                payload,
                purpose,
                command_id,
            )

        def _finish_chapter() -> None:
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

        call_cap = _effective_call_cap(production)
        remaining_calls = max(0, int(call_cap) - int(production.get("call_count") or 0))
        await run_editorial_then_accept(
            work=work,
            run=run,
            production=production,
            soft_only=soft_only,
            chapter_result=result,
            style=style if isinstance(style, dict) else {},
            world=world if isinstance(world, dict) else {},
            production_cast=production_cast,
            packet=packet if isinstance(packet, dict) else {},
            remaining_calls=remaining_calls,
            input_version=_input_version(run),
            call=_editorial_call,
            save=_save,
            finish_chapter=_finish_chapter,
        )
        return True
    # 防御性兜底：审校未通过绝不进入接受边界。
    return False

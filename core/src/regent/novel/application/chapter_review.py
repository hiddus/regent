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
    front_gate_hard_fails,
    power_surface_fails_review,
)
from regent.novel.domain.review_issues import (
    partition_hard_by_grounding,
    partition_issues,
    quote_in_content,
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
    EvidenceRecheck,
    RoleSelfReview,
)
from regent.novel.application.directing_runtime import (
    _RUNTIME,
)
from regent.novel.application.directing_scene_loop import (
    _new_take,
)

_effective_call_cap = _budget_call_cap

# 创作已通过后，整章审校最多回退补拍次数；再遇可 soft 意见直接 coerce。
_MAX_POST_CREATIVE_REVIEW_REWINDS = MAX_CHAPTER_REPAIRS
_MAX_REVIEW_PLAN_REWINDS = 2


def _merge_gate_debts_into_issues(
    issues: list[str], sp: dict[str, Any] | None, *,
    current_front_fails: list[str] | None = None,
) -> list[str]:
    """导演递延的债务进入责编议题；按 kind 加不同前缀便于识别。"""
    out = list(issues)
    for debt in (sp or {}).get("gate_debts") or []:
        if not isinstance(debt, dict):
            continue
        if str(debt.get("owner") or "") != "review":
            continue
        kind = str(debt.get("kind") or "")
        if kind == "continuity":
            prefix = "[gate_debt:cont]"
        elif kind == "prose_front":
            prefix = "[gate_debt:prose_front]"
        else:
            continue
        tags = list(debt.get("tags") or [])
        if kind == "prose_front" and current_front_fails is not None:
            # 正文确定性门已在本轮完整重验。旧文案/旧摘录只能留作证据。
            # 不能重新注入当前 issues。否则已消除的 redundant 永远阻断。
            checked = ("[front:redundant]", "[front:power_surface]")
            rechecked = [t for t in tags if str(t).startswith(checked)]
            debt["resolved_tags"] = [
                t for t in rechecked
                if not any(
                    str(f).split("]", 1)[0] == str(t).split("]", 1)[0]
                    for f in current_front_fails
                )
            ]
            debt["revalidated_tags"] = list(current_front_fails)
            tags = [t for t in tags if t not in rechecked]
        for tag in tags:
            text = str(tag or "").strip()
            if not text:
                continue
            marked = (
                text
                if text.startswith(prefix)
                else f"{prefix}{text}"
            )
            if marked not in out and text not in out:
                out.append(marked)
    return out


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


async def _resolve_evidence_defects(
    session: Any,
    provider: Any,
    work: Any,
    run: Any,
    production: dict[str, Any],
    *,
    defects: list[str],
    content: str,
) -> tuple[list[str], list[str], list[str]]:
    """对无法接地的审校硬问题做一次有界证据复核。

    返回 (grounded, withdrawn, unresolved)：
    - grounded：复核补出当前正文逐字证据，转回正常修复路径（硬问题）。
    - withdrawn：复核撤回，计入误报，不阻断。
    - unresolved：仍无法接地，须结构化停机，禁止入正史。

    同一正文版本最多复核一次；该调用计入失败路径预算，不占重写/修复/返工额度。
    """
    sp = production.setdefault("script_protocol", {})
    chash = hashlib.sha256(content.encode()).hexdigest()
    if sp.get("evidence_recheck_hash") == chash:
        # 本轮已复核过仍无法接地：不再开新一轮，全部视为未解决。
        return [], [], list(defects)
    ids = [f"evd{i}" for i in range(len(defects))]
    result = await _call(
        session,
        provider,
        work,
        run,
        production,
        EvidenceRecheck,
        "你刚才提出了若干审校硬问题，但程序在当前正文里找不到你引用的证据。"
        "只复核这些问题的证据，不改正文。对每条：若确是正文中的真实冲突，"
        "给出当前正文里的逐字片段 grounded_quote 并 verdict=grounded；"
        "若引用内容不在当前正文、或属于合法重读/引用/维修，verdict=withdrawn。"
        "不得臆造证据，不得提出新问题。",
        {
            "content": content,
            "defects": [
                {"issue_id": i, "issue": t}
                for i, t in zip(ids, defects, strict=True)
            ],
        },
        "chapter_evidence_recheck",
        f"v{_input_version(run)}:evidence_recheck:{chash[:12]}",
    )
    sp["evidence_recheck_hash"] = chash
    by_id = {str(item.issue_id): item for item in (result.items or [])}
    grounded: list[str] = []
    withdrawn: list[str] = []
    unresolved: list[str] = []
    for iid, text in zip(ids, defects, strict=True):
        item = by_id.get(iid)
        if item is None:
            unresolved.append(text)
        elif item.verdict == "withdrawn":
            withdrawn.append(text)
        elif (
            item.verdict == "grounded"
            and quote_in_content(item.grounded_quote, content)
        ):
            grounded.append(text)
        else:
            # 声称接地但片段对不上正文 → 视同无法接地。
            unresolved.append(text)
    return grounded, withdrawn, unresolved



def _rewind_script_to_compile(
    run: Any,
    production: dict[str, Any],
    *,
    issues: list[str],
    note: str,
) -> bool:
    """计划层或章节目标失败时回到分场，旧场正文与旧通过标记一并失效。"""
    sp = production.setdefault("script_protocol", empty_script_state())
    rewinds = int(sp.get("review_plan_rewinds") or 0) + 1
    sp["review_plan_rewinds"] = rewinds
    if rewinds > _MAX_REVIEW_PLAN_REWINDS:
        raise ProductionStopped("分场返工次数已达上限：" + note)
    sp["review_plan_repair"] = [str(item) for item in issues if str(item).strip()][:6]
    sp["scene_texts"] = []
    sp["scene_index"] = 0
    sp["scene_audits"] = {}
    sp["scene_exit_states"] = []
    sp["scene_state_trail"] = []
    sp["working_summary"] = ""
    sp["scene_entry_summaries"] = []
    sp["scene_plan"] = {}
    sp["pending_repair_ticket"] = {}
    sp["scene_revision_instruction"] = ""
    sp["post_review_rewrite"] = False
    sp["compile_self_review_done"] = False
    sp.pop("compile_self_review", None)
    # 入口只能来自 chapter_entry_state：ASSEMBLE 把失败草稿出口写进了 working_state，
    # 若不重置，重分场会从失败稿的出口起算，带污染进正史。
    entry = production.get("chapter_entry_state")
    if isinstance(entry, dict):
        sp["working_state"] = deepcopy(entry)
        production["working_state"] = deepcopy(entry)
    # 台账也回退到本章入口（前章 handoff 或空台账）。
    from regent.novel.domain.continuity_ledger import empty_ledger

    prev_ledger = None
    for prev in reversed(run.generation_context.get("recent_chapters") or []):
        if isinstance(prev, dict) and isinstance(prev.get("continuity_ledger"), dict):
            prev_ledger = prev["continuity_ledger"]
            break
    sp["continuity_ledger"] = (
        deepcopy(prev_ledger) if prev_ledger else empty_ledger()
    )
    sp["continuity_ledger_trail"] = [deepcopy(sp["continuity_ledger"])]
    sp.pop("ledger_violations", None)
    for key in list(sp):
        if key.startswith((
            "writer_self_reviewed:",
            "auditor_self_reviewed:",
            "self_repairs:",
            "auditor_self_repairs:",
        )):
            sp.pop(key, None)
    for take in production.get("takes") or []:
        if isinstance(take, dict):
            take["status"] = "SUPERSEDED"
    production["accepted"] = []
    production["phase"] = "COMPILE_SCENES"
    review = getattr(run, "review", None)
    if isinstance(review, dict):
        review["passed"] = False
        review["invalidated_reason"] = "plan_rewind"
    _save(run, production)
    return False


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
        from regent.novel.domain.chapter_handoff import (
            FAULT_CHAPTER_GOAL,
            FAULT_PRIOR_CHAPTER,
            FAULT_SCENE_PLAN,
            review_repair_layer,
        )

        layer = review_repair_layer(list(issues or []))
        if layer == FAULT_PRIOR_CHAPTER:
            raise ProductionStopped(
                "审校指向已接受的前章事实，不能靠改当前章一场解决：" + note
            )
        if layer in {FAULT_SCENE_PLAN, FAULT_CHAPTER_GOAL}:
            return _rewind_script_to_compile(
                run,
                production,
                issues=list(issues or []),
                note=note,
            )
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
    from regent.novel.domain.repair_locate import build_scene_layout

    _sp = production.get("script_protocol") or {}
    _scene_layout = build_scene_layout(
        scene_texts=list(_sp.get("scene_texts") or []),
        cards=list((_sp.get("scene_plan") or {}).get("cards") or []),
    )
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
        "failed_scene_index 填最早受影响场景下标（从0开始，相对 payload.scene_layout 分场顺序）。"
        "必须对照 scene_layout 的 scene_index/scene_id/字符区间定位，禁止自行猜分场。"
        "判断目标节点是否真正完成；完成必须给出正文逐字completion_quote。",
        {
            "chapter": run.content,
            "recent_chapters": run.generation_context.get("recent_chapters", []),
            "target_node": run.generation_context.get("target_node", {}),
            "canon": run.generation_context.get("canon", []),
            "scene_layout": _scene_layout,
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
    # 审校意见缺场次时，由同一审校再核一遍自己的结论，只补定位，不把失败改成通过。
    if (not result.passed) and result.failed_scene_index is None and list(result.issues):
        self_rev = await _call(
            session,
            provider,
            work,
            run,
            production,
            RoleSelfReview,
            "你是刚才做整章审校的人，只审核自己刚写的审校意见，不改正文。"
            "本章内的问题必须给出 failed_scene_index（从0开始，对照 scene_layout）。"
            "若冲突属于前章已成立事实、不能靠改本章某一场解决，prior_chapter=true，不要猜场次。"
            "passed=false 只表示审校意见不完整，不表示整章因此通过。"
            "不要把已成立的失败改成通过。",
            {
                "issues": list(result.issues),
                "scene_layout": _scene_layout,
                "failed_scene_index": result.failed_scene_index,
            },
            "chapter_self_review",
            f"v{_input_version(run)}:chapter_validation:"
            f"{hashlib.sha256(run.content.encode()).hexdigest()[:12]}:selfreview",
        )
        if isinstance(production, dict):
            sp_self = production.setdefault("script_protocol", {})
            if isinstance(sp_self, dict):
                sp_self["chapter_self_review"] = self_rev.model_dump()
        if self_rev.failed_scene_index is not None:
            result = result.model_copy(
                update={"failed_scene_index": self_rev.failed_scene_index}
            )
        if self_rev.prior_chapter:
            issues = [
                item
                if any(marker in item for marker in ("[prior_canon]", "旧章", "前章"))
                else f"[prior_canon] {item}"
                for item in issues
            ]
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
    # REVIEW 层：power_surface 用弱匹配（特定机制/代价短语），其余门保持严格
    _content_text = str(run.content or "")
    front_fails_no_power = [
        f for f in front_fails if not str(f).startswith("[front:power_surface")
    ]
    review_power_fails = power_surface_fails_review(
        _content_text,
        production_packet=packet if isinstance(packet, dict) else {},
        chapter_no=int(run.chapter_no),
    )
    front_fails_effective = list(dict.fromkeys([*front_fails_no_power, *review_power_fails]))
    if front_fails_effective:
        issues = list(dict.fromkeys([*issues, *front_fails_effective]))
    # 导演 AUDIT 递延的连续性债务：责编层接管
    _sp_early = production.get("script_protocol") if isinstance(production, dict) else {}
    if isinstance(_sp_early, dict):
        issues = _merge_gate_debts_into_issues(
            issues, _sp_early, current_front_fails=front_fails_effective,
        )
    # 专职责编：默认抽样，避免每章固定付费却只有 soft 记录
    from regent.novel.application import editor_audit as ea

    editor_issues: list[str] = []
    soft_only: list[str] = []
    if ea.should_run_editor_audit(chapter_no=int(run.chapter_no), front_fails=front_fails_effective):
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
    # 严重性与递延分离：软问题只记录不阻断；硬问题必须消除才能入正史（工作包1.1）。
    hard_issues, soft_issues = partition_issues(issues)
    content_text = str(run.content or "")
    # 审校硬问题须证据接地；无法接地的走一次有界复核，不直接触发重写、不自动降软。
    grounded_hard, evidence_defects = partition_hard_by_grounding(hard_issues, content_text)
    withdrawn: list[str] = []
    if evidence_defects:
        recheck_grounded, withdrawn, unresolved = await _resolve_evidence_defects(
            session,
            provider,
            work,
            run,
            production,
            defects=evidence_defects,
            content=content_text,
        )
        if unresolved:
            raise ProductionStopped(
                "审校硬问题无法接地，证据复核后仍缺证据，禁止入正史："
                + "；".join(str(i) for i in unresolved[:4])
            )
        grounded_hard = [*grounded_hard, *recheck_grounded]
    hard_final = list(grounded_hard)
    # 有未结硬问题即不通过；硬问题全部被复核撤回（误报）时，审校的 passed=False 被解释掉。
    # 审校判负却无可核对硬问题、也无撤回记录时，保守不放行。
    passed = False if hard_final else (bool(result.passed) or bool(withdrawn))
    run.review = {
        "passed": passed,
        "continuity_issues": issues,
        "hard_issues": hard_final,
        "soft_issues": [*soft_issues, *withdrawn],
        "evidence_defects": evidence_defects,
        "withdrawn_issues": withdrawn,
        "prose_issues": [],
        "leakage_issues": [],
        "commons_issues": commons_only,
        "front_gate_issues": front_only,
        "editor_issues": editor_issues,
        "editor_soft_issues": soft_only,
        "revised": bool(production.get("chapter_repairs"))
        or any(len(take["prose_versions"]) > 1 for take in production["takes"]),
    }
    # 包括最终停机轮；只记录修订轮无法解释“次数耗尽”时究竟还剩什么。
    review_snapshot = {
        "content_hash": hashlib.sha256(run.content.encode()).hexdigest(),
        "passed": run.review["passed"],
        "issues": list(issues),
        "hard_issues": list(hard_final),
        "chapter_repairs": int(production.get("chapter_repairs") or 0),
        "post_creative_review_rewinds": int(production.get("post_creative_review_rewinds") or 0),
    }
    history = production.setdefault("review_history", [])
    if not history or history[-1] != review_snapshot:
        history.append(review_snapshot)
    _save(run, production)
    if not run.review["passed"]:
        # 硬问题不再有强制放行兜底：额度内回退修复，否则结构化停机（工作包1.1）。
        content_hash = hashlib.sha256(run.content.encode()).hexdigest()
        sp = production.get("script_protocol") or {}
        creative_ok = bool((sp.get("creative_accept") or {}).get("accept"))
        post_rewinds = int(production.get("post_creative_review_rewinds") or 0)
        if production.get("last_failed_hash") == content_hash:
            raise ProductionStopped(
                "修复没有改变正文，仍有硬问题，停止重试："
                + "；".join(str(i) for i in hard_final[:4])
            )
        production["last_failed_hash"] = content_hash
        repairs = production.get("chapter_repairs", 0)
        if repairs >= MAX_CHAPTER_REPAIRS or (
            creative_ok and post_rewinds >= _MAX_POST_CREATIVE_REVIEW_REWINDS
        ):
            raise ProductionStopped(
                "章节修复次数已达上限，保留场景及证据；剩余硬问题："
                + "；".join(str(i) for i in hard_final[:4])
            )
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
                issues=hard_final or ["整章未通过独立审校"],
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
            (hard_final or issues or ["整章未通过独立审校"])[:6]
        )[:400]
        current.update(
            {
                "events": deepcopy(old["events"]),
                "content": old["content"],
                "prose_versions": [old["content"]],
                "validation": {
                    "passed": False,
                    "issues": hard_final or issues or ["整章未通过独立审校"],
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

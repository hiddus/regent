"""Persistent scene production, driven by a director rather than a chapter editor.

Each tick commits at most one **decision**; the caller commits the checkpoint
with the call ledger. A tick may issue more than one model call when a judgment is
repaired in place (evidence or command rejection, bounded by
``MAX_JUDGE_REPAIRS``/``MAX_COMMAND_REPAIRS``). Repair calls are ordinary calls for
budget purposes: ``call_count`` counts them against ``MAX_CALLS`` and money is
capped separately by ``MAX_COST_MINOR``. A crash before the commit can still leave
an UNKNOWN external call; this module does not claim provider-side exactly-once
execution.
"""

# Chinese creative instructions deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
import re
import uuid
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
from regent.novel.domain.errors import (
    BudgetExhausted,
    CommandRejected,
    ProductionStopped,
    QuotaExceeded,
)
from regent.novel.domain.hive import route_beat
from regent.novel.domain.memory import project_payloads as _memory_view
from regent.novel.domain.models import DecisionOption
from regent.novel.domain.prose_patch import (
    PatchError,
    apply_patch,
    content_hash as prose_content_hash,
    paragraphs_payload,
    split_paragraphs,
)
from regent.novel.domain.scene_requirements import (
    REQUIREMENTS_VERSION,
    freeze_scene_requirements,
    must_preserve_for_writer,
    revision_conflicts_settled,
)
from regent.novel.domain.scene_card import (
    BeatVerdict,
    ChapterScenePlan,
    SceneCard,
    SCENE_WRITE_SYSTEM,
    assemble_chapter,
    compile_scene_plan,
    evaluate_scene_audit,
    structural_plan_issues,
    validate_scene_plan,
)
from regent.novel.domain.script_protocol import (
    DIRECTOR_ASSIGN_CONTRACT,
    DIRECTOR_SELECT_CONTRACT,
    SCRIPT_CANDIDATE_SLOTS,
    SCRIPT_DIVERSITY_CONTRACT,
    SCRIPT_PHASES,
    SCRIPT_SCENE_PHASES,
    WEB_NOVEL_POWER_CONTRACT,
    ChapterCreativeAccept,
    ChapterScript,
    ScriptAssignmentBoard,
    ScriptChoice,
    assemble_production_packet,
    assert_rejected_isolated,
    empty_script_state,
    normalize_assignment_board,
    route_script_hive,
    task_for_slot,
)
from regent.novel.domain.world_bible import (
    resolve_prose_style,
)
from regent.novel.domain.prose_front_gates import (
    front_gate_hard_fails,
    front_gate_writer_hint,
    review_issues_may_coerce,
)
from regent.novel.domain.states import SceneArtifact, SceneRunState
from regent.novel.infrastructure.models import ChapterRunModel, PersonaSpecModel, StoryWorkModel
from regent.novel.application.directing_types import (
    MAX_CHAPTER_CREATIVE_REPAIRS,
    MAX_CHAPTER_REPAIRS,
    MAX_TAKES,
    PROTOCOL_SCENE,
    PROTOCOL_SCRIPT,
    PROTOCOL_SCRIPT_SCENE,
    SCRIPT_ARCHITECTURE,
    SCRIPT_SCENE_ARCHITECTURE,
)
from regent.novel.application.directing_protocol import (
    ARCHITECTURE,
    BEAT_ARCHITECTURE,
    DIRECTED_ARCHITECTURES,
    PROTOCOL_BEAT,
    is_directed,
    production_protocol,
)
from regent.novel.application.directing_budget import (
    CURRENCY,
    MAX_CALLS,
    MAX_COST_MINOR,
    effective_call_cap as _budget_call_cap,
    effective_cost_cap as _budget_cost_cap,
    remaining_calls as _budget_remaining_calls,
)

# 「所有人都知道」的既有记号：它不是人物名，核验知情范围时必须跳过。
EVERYONE_KNOWS = "ALL"
# 首稿后有限重演/修订；仍失败则暂停并保留草稿（职责删减：收缩自动回退）。
# 角色引用自修次数。之所以需要「带反馈的自修」而不是交给步骤级重试：规划用
# temperature=0，入参不变的重试会以近乎确定的方式产出同一个坏名字，重试等于
# 白烧钱。自修必须改入参——把违规原因和在册名单回传给导演。
# 末尾括号注释，模型给角色名加注的习惯写法；只用于**确定性**归一，不做模糊匹配。
_TRAILING_PAREN = re.compile(r"[（(][^（()）]*[)）]\s*$")
# 每章可新增的人物数。角色表是**起点不是牢笼**：一本小说不可能只有开局那几个
# 人，导演当然要能按叙事需要带新人进场。上限卡的不是「能不能造人」，而是
# 「一次造多少」——角色表会被带进后续每一章的上下文和每个角色的信息集，
# 无上限等于让一次随手声明永久抬高后续所有章节的成本。
# 导演观察证据：要求可定位场景依据，不要求整句逐字抄录。
# 连续重合门槛保留足够高度，以防「两截拼贴」式伪引文过关。
# 判断类调用（观看表演 / 审阅正文）的引文自修次数。
# 2：场次切换后导演偶发捏造台词引文，多给一拍改成正文里真实存在的短句。
# 命令被 Runtime 拒绝后的自修次数。真机卡点：正文漏写已结算状态 → 硬失败 →
# 导演仍选 ACCEPT → 被拒 → 整章判死。选这个而不是「悄悄换成 REWRITE」：
# 换动作等于替导演绕过独立核验（既有测试编码的安全属性），而自修是让它**自己**
# 改选一个合法动作，坚持 ACCEPT 才判死。
# 2 次：硬失败后常见「再 ACCEPT 一次 / 误选 REWRITE」，多给一拍改选 RETAKE。
# 核验报告格式/伪造引文的自修次数（计入同一调用预算，不另开通道）。
# 接受 REWRITE / 进入 RENDER 前须预留的调用次数。
# REWRITE 之后至少还要：执笔修订 → 导演复看 → 场景核验（+1 报告自修）。
# RENDER 之后至少还要：导演复看 → 场景核验（+1 报告自修）。
# 固定小数即可，不做成复杂调度（职责删减后的收敛补齐）。
# 兼容旧名：取更严的 REWRITE 预留，供常量断言与默认路径。

# 导演发起的裁决必须有到期时间，否则章节会永久停在等待态——这正是「阻塞交互
# 无超时」那类缺陷。到期无人选择就走默认项（G-13），所以窗口只决定等多久。
# 影响越大等得越久：高影响裁决的默认项往往不可逆，不能因为作者两天没上线就
# 替他决定（D-05 的「高影响默认等待」据此落地为「窗口更长」而非无限等待）。
from regent.novel.application.directing_decisions import (
    DECISION_DEADLINE_BY_IMPACT, DECISION_DEADLINE_DEFAULT,
)



def _effective_call_cap(production: dict[str, Any]) -> int:
    return _budget_call_cap(production, base_calls=MAX_CALLS)


def _effective_cost_cap(production: dict[str, Any]) -> int:
    return _budget_cost_cap(production, base_cost_minor=MAX_COST_MINOR)


def _remaining_calls(production: dict[str, Any]) -> int:
    return _budget_remaining_calls(production, base_calls=MAX_CALLS)


from regent.novel.application.directing_issue_ledger import (
    _hard_issue_specs,
    _record_issue_ledger,
    _rewrite_blocked_by_ledger,
)





from regent.novel.application.directing_runtime import (
    MAX_REVISIONS, MAX_TURNS, MAX_VALIDATION_REPAIRS,
    VALIDATE_RESERVE_CALLS, VALIDATE_RESERVE_RENDER, VALIDATE_RESERVE_REWRITE,
    _PROSE_COMMANDS, _RUNTIME, _TAKE_COMMANDS, _apply_state, _check_brief,
    _coerce_illegal_action, _ensure_validate_call_reserve, _extend_events,
    _legal_action_report, _live_takes_for_scene, _record_manifest,
    _retake_would_pass_with_brief, _runtime_state, _validate_action,
    _validate_reserve_for,
)

from regent.novel.application.directing_contracts import (
    ActorDirection,
    ActorTurn,
    ChapterDirection,
    ChapterValidation,
    DecisionRequestSpec,
    DualDossierBundle,
    EndingVerdict,
    NarrativeSpec,
    NewPersonaSpec,
    ParagraphReplacement,
    ProseDirection,
    RequirementVerdict,
    SceneBrief,
    SceneEvent,
    SceneResolution,
    SceneText,
    SceneTextPatch,
    SceneValidation,
    ScriptChapterValidation,
    TakeDirection,
    VerifiedFact,
    VerifiedStateChange,
    _SceneAudit,
    _ScenePlanDraft,
    _SceneProseWithBeats,
)
from regent.novel.application.directing_planning import (
    MAX_PLAN_REPAIRS, _ensure_dual_dossiers, _ensure_work_conventions,
    _plan_script_chapter, _plan_story_rails_issues, plan_chapter,
)

from regent.novel.application.directing_evidence import (
    COMMAND_REPAIR_RULE,
    MAX_COMMAND_REPAIRS,
    MAX_JUDGE_REPAIRS,
    QUOTE_MIN_RATIO,
    QUOTE_MIN_RUN,
    EVIDENCE_REPAIR_RULE,
    _grounded_judgment,
    _is_grounded,
    _longest_common_run,
    _quote_check,
    _stage_text,
)

from regent.novel.application.directing_cast import (
    _admit_ambient_dialogue_speakers,
    _brief_issues,
    _canonical_persona,
    _is_ambient_extra,
    _scrub_unknown_known_by,
    MAX_NEW_PERSONAS_PER_CHAPTER,
    _declared_cast,
    _register_new_personas,
)

















from regent.novel.application.directing_requirements import (
    _catastrophic_prose_loss,
    _ensure_requirements,
    _substantive_validation_issues,
    _validation_report_defects,
    _working_state_from_requirements,
)









from regent.novel.application.directing_calls import (
    _call, _call_batch, _command_id, _input_version, _save, _script_scene_command_id,
)

from regent.novel.application.directing_decisions import (
    _consume_user_decision, _pending_user_decision, _request_user_decision,
)









from regent.novel.application.directing_script_loop import (
    _script_writer_system,
    _script_system,
    _script_brief_from_packet,
    _ensure_script_take,
    _bump_creative_repair,
    _produce_script_tick
)

def _commit_run_story_ledger(
    run: Any,
    *,
    production: dict[str, Any],
    facts: list[Any],
) -> None:
    """兼容入口：最终账本写入已迁至 ``chapter_accept``。"""
    from regent.novel.application.chapter_accept import commit_run_story_ledger

    commit_run_story_ledger(run, production=production, facts=facts)




from regent.novel.application.directing_scene_loop import (
    _new_take,
    _retake,
    _all_events,
    produce_tick
)

from regent.novel.application.chapter_review import (
    _rewind_script_after_review,
    validate_chapter
)

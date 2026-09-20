"""导演模型调用协议。

这些 schema 的字段、约束与类名参与模型请求和恢复协议；迁移不得改变。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from regent.novel.domain.models import DecisionOption
from regent.novel.domain.scene_card import BeatVerdict, SceneCard


class ActorDirection(BaseModel):
    persona: str = Field(
        min_length=1,
        description="必须是给定角色表里的确切名字，一字不差；"
        "不得添加括号、头衔、别称或本场说明，也不得新造人物",
    )
    role: str = Field(default="", description="该人物在本场的作用，如「旁观者」；不要写进 persona")
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


class NewPersonaSpec(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    voice: str = Field(min_length=1, description="说话方式：句式、用词、语气；这是该人物的声纹")
    identity: str = Field(default="", description="一句话身份，如「码头搬工」")
    drives: str = Field(default="", description="他此刻想要什么")
    reason: str = Field(min_length=1, description="为什么现有角色撑不起这场戏")
    kind: str = Field(
        default="",
        description="可选：traveler（穿越者意识）或 host_body（原身躯壳）",
    )
    bio: str = Field(default="", description="小传：前史/过人之处；穿越作建议填写")


class DualDossierBundle(BaseModel):
    traveler_name: str = Field(min_length=1, description="穿越者称呼或前史名")
    traveler_bio: str = Field(
        min_length=40, description="穿越前身份、死因/契机、性格、目标（从本作 premise 发明）"
    )
    traveler_voice: str = Field(min_length=1)
    host_name: str = Field(min_length=1, description="原身对外姓名")
    host_bio: str = Field(
        min_length=40,
        description="原身外表资源、为何能独自活动、过人之处；勿写入穿越者死亡/前职",
    )
    host_voice: str = Field(min_length=1, description="原身对外声纹/人设说话方式")


class ChapterDirection(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reader_intent: str = Field(min_length=1)
    ending_reason: str = Field(min_length=1)
    scenes: list[SceneBrief] = Field(min_length=1, max_length=4)
    new_personas: list[NewPersonaSpec] = Field(default_factory=list)


class ActorTurn(BaseModel):
    intention: str
    actions: list[str] = Field(min_length=1, max_length=5)
    dialogue: list[str] = Field(default_factory=list, max_length=5)
    private_reasoning: str = ""


class SceneEvent(BaseModel):
    statement: str = Field(min_length=1)
    known_by: list[str] = Field(
        default_factory=list,
        description="实际获知者的角色名，必须取自给定角色表且一字不差；缺席人物不能自动获知",
    )
    reader_visible: bool
    state_changes: dict[str, str] = Field(default_factory=dict)
    dialogue_by_character: dict[str, list[str]] = Field(
        default_factory=dict, description="键为角色表中的确切名字，值为该人物实际说出的台词"
    )


class SceneResolution(BaseModel):
    events: list[SceneEvent] = Field(min_length=1, max_length=8)
    rule_issues: list[str] = Field(default_factory=list)


class DecisionRequestSpec(BaseModel):
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


class ParagraphReplacement(BaseModel):
    paragraph_ids: list[str] = Field(min_length=1)
    text: str = Field(min_length=1)


class SceneTextPatch(BaseModel):
    base_content_hash: str = Field(min_length=16)
    purpose: str = Field(default="")
    replacements: list[ParagraphReplacement] = Field(min_length=1)


class ProseDirection(BaseModel):
    action: Literal["ACCEPT", "REWRITE", "RETAKE"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None
    request_decision: DecisionRequestSpec | None = None
    revision_mode: Literal["full", "patch"] | None = None


class VerifiedFact(BaseModel):
    statement: str = Field(min_length=1)
    quote: str = Field(min_length=1, description="最终正文中逐字存在的证据")
    known_by: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    memory_kind: str = Field(
        default="", description="rule / character_arc / promise / relation；不填即按事实结构判定"
    )
    subject: str = Field(default="", description="记忆主题；不填即按在册人物推导")
    resolves: str = Field(
        default="",
        description="被本条事实兑现的具体承诺（写承诺内容，不写人物名）；不填即不兑现",
    )


class VerifiedStateChange(BaseModel):
    key: str = Field(min_length=1)
    value: str
    quote: str = Field(min_length=1)


class RequirementVerdict(BaseModel):
    requirement_id: str = Field(min_length=1)
    status: Literal["supported", "contradicted", "missing"]
    quote: str = Field(
        default="", description="当前正文证据；supported/contradicted 必填且须在稿中"
    )
    explanation: str = Field(default="", description="自然语言解释，不承担身份比较")


class SceneValidation(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    facts: list[VerifiedFact] = Field(default_factory=list)
    requirements: list[RequirementVerdict] = Field(default_factory=list)
    state_changes: list[VerifiedStateChange] = Field(default_factory=list)


class ChapterValidation(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    node_completed: bool = False
    completion_quote: str = ""
    failed_scene_index: int | None = Field(default=None, ge=0)


class EndingVerdict(BaseModel):
    story_complete: bool
    reason: str = Field(min_length=1, description="引用已写内容说明为什么算/不算讲完")


class LocatedIssueSpec(BaseModel):
    """整章硬失败的可追踪定位（与 domain.repair_locate.LocatedIssue 对齐）。"""

    issue_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: Literal["hard", "soft"] = "hard"
    scene_ids: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    content_hash: str = ""
    expected_action: str = "rewrite_scene"
    verify_rule: str = ""
    message: str = ""


class ScriptChapterValidation(BaseModel):
    hard_fails: list[str] = Field(default_factory=list)
    soft_notes: list[str] = Field(default_factory=list)
    facts: list[VerifiedFact] = Field(default_factory=list)
    located_issues: list[LocatedIssueSpec] = Field(default_factory=list)


class ScenePlanDraft(BaseModel):
    cards: list[SceneCard] = Field(min_length=1, max_length=6)
    chapter_goal: str = ""
    continuity_notes: str = ""


class SceneProseWithBeats(BaseModel):
    content: str = Field(min_length=50)
    beat_evidence: dict[str, str] = Field(default_factory=dict)


class SceneAudit(BaseModel):
    scene_id: str = Field(min_length=1)
    beat_verdicts: list[BeatVerdict] = Field(default_factory=list)
    continuity_ok: bool = True
    continuity_issues: list[str] = Field(default_factory=list)
    hard_fails: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    state_changes: dict[str, str] = Field(default_factory=dict)
    character_shifts: list[str] = Field(default_factory=list)
    hooks_opened: list[str] = Field(default_factory=list)
    hooks_closed: list[str] = Field(default_factory=list)


# Private compatibility names used by existing tests/checkpoints.
_ScenePlanDraft = ScenePlanDraft
_SceneProseWithBeats = SceneProseWithBeats
_SceneAudit = SceneAudit

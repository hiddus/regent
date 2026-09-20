"""一次性补丁：P1-2 导演裁决闭环（创建、竞争落定、到期默认）。"""
from __future__ import annotations

import pathlib

# ---------------------------------------------------------------------------
# direction.py：导演可请求用户裁决
# ---------------------------------------------------------------------------
dr = pathlib.Path("core/src/regent/novel/application/direction.py")
text = dr.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"direction.py count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


sub(
    """from regent.novel.domain.models import DecisionOption""",
    """from regent.novel.domain.models import DecisionOption""",
) if "from regent.novel.domain.models import DecisionOption" in text else None

# 1) 导入 DecisionOption
sub(
    "from regent.novel.domain.hive import route_beat\n",
    "from regent.novel.domain.hive import route_beat\nfrom regent.novel.domain.models import DecisionOption\n",
)

# 2) 裁决请求规格
sub(
    """class TakeDirection(BaseModel):""",
    '''class DecisionRequestSpec(BaseModel):
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


class TakeDirection(BaseModel):''',
)

# 3) 两个导演决策都带上请求裁决
sub(
    """class TakeDirection(BaseModel):
    action: Literal["CONTINUE", "RETAKE", "RENDER"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None""",
    """class TakeDirection(BaseModel):
    action: Literal["CONTINUE", "RETAKE", "RENDER"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None
    request_decision: DecisionRequestSpec | None = None""",
)

sub(
    """class ProseDirection(BaseModel):
    action: Literal["ACCEPT", "REWRITE", "RETAKE"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None""",
    """class ProseDirection(BaseModel):
    action: Literal["ACCEPT", "REWRITE", "RETAKE"]
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)
    revised_brief: SceneBrief | None = None
    request_decision: DecisionRequestSpec | None = None""",
)

# 4) 导入 model_validator 与 Literal（Literal 已导入？）
if "model_validator" not in text:
    sub(
        "from pydantic import BaseModel, Field\n",
        "from pydantic import BaseModel, Field, model_validator\n",
    )

# 5) 发起裁决的辅助函数
sub(
    """
async def plan_chapter(""",
    '''
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
    )
    production["pending_decision"] = {
        "phase": phase,
        "scene_index": production["scene_index"],
        "take_no": take["take_no"],
        "trigger_summary": spec.trigger_summary,
    }
    _save(run, production)


async def plan_chapter(''',
)

# 6) WATCH_TAKE：导演请求裁决优先于动作
sub(
    """        _quote_check(result.evidence, evidence_text)
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **result.model_dump(mode="json"),
            }
        )
        state = _runtime_state(production, run, take)""",
    """        _quote_check(result.evidence, evidence_text)
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
        state = _runtime_state(production, run, take)""",
)

# 7) WATCH_PROSE：同上
sub(
    """        _quote_check(result.evidence, take["content"])
        production["decisions"].append(
            {
                "phase": phase,
                "scene_index": production["scene_index"],
                "take_no": take["take_no"],
                **result.model_dump(mode="json"),
            }
        )
        state = _runtime_state(production, run, take)""",
    """        _quote_check(result.evidence, take["content"])
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
        state = _runtime_state(production, run, take)""",
)

dr.write_text(text, encoding="utf-8")
print("patched", dr)

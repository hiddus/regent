"""导演命令（Tech-Spec §3.4）。

模型只提出命令，不直接更新状态：命令必须带版本、命令标识、输入版本和
证据，由 Runtime 校验后才生效。命令本身不可变，fingerprint 用于留痕与
恢复对齐——同一命令在崩溃恢复后必须得到同一个 fingerprint。
"""

# Chinese docstrings deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from regent.novel.domain.context import digest

COMMAND_VERSION = 1


class CommandKind(StrEnum):
    """命令白名单。Runtime 只接受这些种类，模型不得发明新命令。"""

    PLAN_SCENE = "PLAN_SCENE"
    REQUEST_PERFORMANCE = "REQUEST_PERFORMANCE"
    CONTINUE_SCENE = "CONTINUE_SCENE"
    RETAKE_SCENE = "RETAKE_SCENE"
    RENDER_SCENE = "RENDER_SCENE"
    REWRITE_PROSE = "REWRITE_PROSE"
    ACCEPT_SCENE = "ACCEPT_SCENE"
    ASSEMBLE_CHAPTER = "ASSEMBLE_CHAPTER"
    REQUEST_USER_DECISION = "REQUEST_USER_DECISION"
    FINISH_CHAPTER = "FINISH_CHAPTER"


class DirectorCommand(BaseModel):
    """一条待校验的导演命令。

    input_version 参与 fingerprint：用户改意后的新输入是一次新命令，
    不会被旧命令的幂等结果吞掉。
    """

    version: int = COMMAND_VERSION
    kind: CommandKind
    command_id: str = Field(min_length=1)
    input_version: int = Field(default=1, ge=1)
    scene_index: int = Field(default=0, ge=0)
    take_no: int = Field(default=1, ge=1)
    evidence: list[str] = Field(default_factory=list)
    payload: dict = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return digest(
            {
                "version": self.version,
                "kind": str(self.kind),
                "command_id": self.command_id,
                "input_version": self.input_version,
                "scene_index": self.scene_index,
                "take_no": self.take_no,
                "evidence": self.evidence,
                "payload": self.payload,
            }
        )


def command(
    kind: CommandKind,
    *,
    command_id: str,
    input_version: int = 1,
    scene_index: int = 0,
    take_no: int = 1,
    evidence: list[str] | None = None,
    **payload: object,
) -> DirectorCommand:
    return DirectorCommand(
        kind=kind,
        command_id=command_id,
        input_version=input_version,
        scene_index=scene_index,
        take_no=take_no,
        evidence=list(evidence or []),
        payload=dict(payload),
    )

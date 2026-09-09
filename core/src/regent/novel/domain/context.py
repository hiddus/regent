"""确定性上下文装配（Tech-Spec §3.2、§13 G-02 / G-03）。

投影只依赖输入：同样的选定来源和版本必须产生同样的投影，字典顺序、
调用次序和模型输出都不得影响结果。manifest 记录来源与两个 hash
（来源 hash、投影 hash），用于证明可复现，并在发生信息泄露时定位路径。

本模块只做装配与裁剪，不做检索、不做创作判断，也不发起模型调用。
"""

# Chinese docstrings and prompt-facing text deliberately use full-width punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

MANIFEST_VERSION = 1

Audience = Literal["actor", "writer", "director", "validator"]


def canonical(value: Any) -> str:
    """稳定序列化：键序固定，因此同样的值总是得到同样的字节。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class SourceRef(BaseModel):
    """上下文来源。hash 为空表示来源未提供可校验摘要，不得据此宣称可复现。"""

    kind: str = Field(min_length=1)
    ref: str = ""
    version: str = ""
    hash: str = ""


class ContextManifest(BaseModel):
    """一次上下文装配的可复现凭证。"""

    version: int = MANIFEST_VERSION
    audience: Audience
    persona: str = ""
    binding: dict[str, Any] = Field(default_factory=dict)
    sources: list[SourceRef] = Field(default_factory=list)
    projection_hash: str = ""

    def fingerprint(self) -> str:
        return digest(
            {
                "version": self.version,
                "audience": self.audience,
                "persona": self.persona,
                "binding": self.binding,
                "sources": [s.model_dump(mode="json") for s in self.sources],
                "projection_hash": self.projection_hash,
            }
        )


@dataclass(frozen=True)
class CompiledContext:
    """装配结果：payload 交给模型，manifest 单独留痕，不进入模型输入。"""

    audience: Audience
    persona: str
    payload: dict[str, Any]
    manifest: ContextManifest

    @property
    def manifest_hash(self) -> str:
        return self.manifest.fingerprint()


def _binder(**binding: Any) -> dict[str, Any]:
    return {key: str(value) for key, value in binding.items() if value is not None}


def visible_facts(facts: list[dict[str, Any]], persona: str) -> list[dict[str, Any]]:
    """按人物认知裁剪事实。

    缺失可见性视为未知，绝不隐式公开（G-03）。`known_by` 为 None 或缺失
    都按“无人知情”处理，因此未标注的事实不会因数据结构差异而泄露。
    """
    return [
        fact
        for fact in facts
        if persona in (fact.get("known_by") or []) or "ALL" in (fact.get("known_by") or [])
    ]


def reader_visible_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执笔者只拿到读者可见事件；隐藏事件不得进入正文材料。"""
    return [event for event in events if event.get("reader_visible")]


def public_performances(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导演观看表演时剔除内心独白。

    角色的 private_reasoning 只对角色自己和留痕可见；导演据此判断效果，
    但不能把它当作事实写回场景（G-03）。
    """
    return [{k: v for k, v in action.items() if k != "private_reasoning"} for action in actions]


def compile_actor_context(
    *,
    work_id: Any,
    branch_id: Any,
    chapter_no: int,
    scene_index: int,
    take_no: int,
    beat: int,
    persona: str,
    cast: dict[str, Any],
    direction: dict[str, Any],
    setting: str,
    canon: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    turn: int,
    memory: Sequence[dict[str, Any]] = (),
) -> CompiledContext:
    """装配单个人物的上下文：只有他该知道的事实与可观察事件。

    ``memory`` 是**已按该人物投影过的**长期记忆（调用方负责用 character 视角
    裁剪；装配器不做检索也不做裁剪判断，只留痕）。读者认知与导演笔记不得进入。
    """
    known_facts = visible_facts(canon, persona)
    allowed_observations = visible_facts(observations, persona)
    memory_items = list(memory)
    payload = {
        "persona": persona,
        **dict(cast.get(persona) or {}),
        "direction": direction,
        "setting": setting,
        "known_facts": known_facts,
        "observations": allowed_observations,
        "character_memory": memory_items,
        "turn": turn,
    }
    manifest = ContextManifest(
        audience="actor",
        persona=persona,
        binding=_binder(
            work_id=work_id,
            branch_id=branch_id,
            chapter_no=chapter_no,
            scene_index=scene_index,
            take_no=take_no,
            beat=beat,
        ),
        sources=[
            SourceRef(kind="canon", ref="parent", hash=digest(canon)),
            SourceRef(kind="observations", ref=f"scene:{scene_index}", hash=digest(observations)),
            SourceRef(kind="persona", ref=persona, hash=digest(cast.get(persona) or {})),
            SourceRef(kind="brief", ref=f"scene:{scene_index}", hash=digest(direction)),
            SourceRef(
                kind="memory",
                ref=f"character:{persona}",
                hash=digest(memory_items),
            ),
        ],
        projection_hash=digest(payload),
    )
    return CompiledContext("actor", persona, payload, manifest)


def compile_writer_context(
    *,
    work_id: Any,
    branch_id: Any,
    chapter_no: int,
    scene_index: int,
    take_no: int,
    narrative: dict[str, Any],
    events: list[dict[str, Any]],
    voices: dict[str, str],
    previous_ending: str,
    target_characters: int,
    director_instruction: str,
    previous_draft: str = "",
    revision_instruction: str = "",
    memory: Sequence[dict[str, Any]] = (),
) -> CompiledContext:
    """装配执笔者上下文：只有已发生且读者可见的事件。

    原始 Canon、人物内心、隐藏事件和未发生的后续场景一律不进入。
    ``memory`` 是叙述者视角的长期记忆（调用方负责投影）：读者认知可以出现，
    导演笔记不得进入正文材料。
    """
    visible = reader_visible_events(events)
    memory_items = list(memory)
    payload = {
        "narrative": narrative,
        "events": visible,
        "voices": voices,
        "previous_ending": previous_ending,
        "target_characters": target_characters,
        "director_instruction": director_instruction,
        "previous_draft": previous_draft,
        "revision_instruction": revision_instruction,
        "narrator_memory": memory_items,
    }
    manifest = ContextManifest(
        audience="writer",
        binding=_binder(
            work_id=work_id,
            branch_id=branch_id,
            chapter_no=chapter_no,
            scene_index=scene_index,
            take_no=take_no,
        ),
        sources=[
            SourceRef(
                kind="events",
                ref=f"scene:{scene_index}",
                hash=digest(events),
                version=f"visible:{len(visible)}",
            ),
            SourceRef(kind="narrative", ref=f"scene:{scene_index}", hash=digest(narrative)),
            SourceRef(kind="memory", ref="narrator", hash=digest(memory_items)),
        ],
        projection_hash=digest(payload),
    )
    return CompiledContext("writer", "", payload, manifest)


def compile_director_performance_context(
    *,
    work_id: Any,
    branch_id: Any,
    chapter_no: int,
    scene_index: int,
    take_no: int,
    brief: dict[str, Any],
    events: list[dict[str, Any]],
    performances: list[dict[str, Any]],
    rule_issues: list[str],
    remaining_turns: int,
    user_guidance: dict[str, Any] | None = None,
    user_decision: dict[str, Any] | None = None,
    memory: Sequence[dict[str, Any]] = (),
) -> CompiledContext:
    """装配导演观看表演的上下文：看到已发生事件与可见行动，看不到内心。

    ``user_decision`` 是用户裁决的**完整创作语义**（不只是 option_id）：导演
    必须按所选后果继续，不得执行未选分支，也不得就同一件事再问一次（A-02）。
    ``memory`` 是导演视角的长期记忆（含未兑现承诺与导演笔记）。
    """
    memory_items = list(memory)
    payload = {
        "brief": brief,
        "events": events,
        "performances": public_performances(performances),
        "rule_issues": list(rule_issues),
        "remaining_turns": remaining_turns,
        "user_guidance": user_guidance or {},
        "user_decision": user_decision or {},
        "director_memory": memory_items,
    }
    manifest = ContextManifest(
        audience="director",
        binding=_binder(
            work_id=work_id,
            branch_id=branch_id,
            chapter_no=chapter_no,
            scene_index=scene_index,
            take_no=take_no,
            beat="performance",
        ),
        sources=[
            SourceRef(kind="events", ref=f"scene:{scene_index}", hash=digest(events)),
            SourceRef(kind="performances", ref=f"take:{take_no}", hash=digest(performances)),
            SourceRef(
                kind="user_decision",
                ref=str((user_decision or {}).get("decision_id", "")),
                hash=digest(user_decision or {}),
            ),
            SourceRef(kind="memory", ref="director", hash=digest(memory_items)),
        ],
        projection_hash=digest(payload),
    )
    return CompiledContext("director", "", payload, manifest)

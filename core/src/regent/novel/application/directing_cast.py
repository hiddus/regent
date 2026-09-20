"""Director cast and scene brief rules."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from regent.novel.application.directing_contracts import ChapterDirection, SceneBrief
from regent.novel.infrastructure.models import PersonaSpecModel, StoryWorkModel

MAX_NEW_PERSONAS_PER_CHAPTER = 2

_TRAILING_PAREN = re.compile(r"[（(][^（()）]*[)）]\s*$")


def _canonical_persona(name: str, cast: dict[str, Any]) -> str | None:
    """把角色引用归一到 ``cast`` 的键；归一不了返回 None。

    三种**确定性**情形：
    ① 原样就是键（角色表里本来就带括号的名字，如「陈父（陈远舟）」）；
    ② 模型写的名字带括号，去掉末尾括号后正好是键（如「陈默（修表匠）」→「陈默」）；
    ③ 模型写了简称，cast 里某个键去掉末尾括号后正好等于它（如模型写「陈父」，
       cast 键是「陈父（陈远舟）」→ cast 键去括号 = 「陈父」= 模型写的）。
    不做模糊匹配：猜错等于把一个人的戏记到另一个人头上，而这是不可逆的。
    """
    if name in cast:
        return name
    stripped = _TRAILING_PAREN.sub("", name).strip()
    if stripped and stripped in cast:
        return stripped
    # 反向：模型写了简称，cast 键去括号后等于它
    for key in cast:
        key_stripped = _TRAILING_PAREN.sub("", key).strip()
        if key_stripped == name:
            return key
    return None


def _scrub_unknown_known_by(events: list[Any], cast: dict[str, Any]) -> list[str]:
    """知情名单只保留 cast 内人物；路人/职能名从 known_by 剔除。

    结算模型常把「化妆师」这类背景职能写进 known_by。把他们钉进信息集会污染
    后续记忆；直接整章停机又过重。剔除未知知情者即可——台词归属仍硬门。
    """
    dropped: list[str] = []
    for event in events:
        kept: list[str] = []
        for name in list(event.known_by or []):
            if name in cast:
                kept.append(name)
            else:
                dropped.append(name)
        event.known_by = kept
    return sorted(set(dropped))


_AMBIENT_EXTRAS = frozenset(
    {
        "化妆师",
        "助理",
        "服务员",
        "店员",
        "保安",
        "司机",
        "导演",
        "编导",
        "场务",
        "灯光师",
        "摄影师",
        "主持人",
        "工作人员",
        "路人",
        "经纪人",
    }
)


def _is_ambient_extra(name: str) -> bool:
    n = str(name or "").strip()
    if not n or len(n) > 8:
        return False
    if n in _AMBIENT_EXTRAS:
        return True
    return any(n.endswith(s) or n == s for s in _AMBIENT_EXTRAS)


def _admit_ambient_dialogue_speakers(events: list[Any], cast: dict[str, Any]) -> list[str]:
    """职能路人（化妆师等）有台词时临时纳入 cast，避免整章因群演停机。"""
    admitted: list[str] = []
    for event in events:
        for name in list((event.dialogue_by_character or {}).keys()):
            if name in cast:
                continue
            if not _is_ambient_extra(name):
                continue
            cast[name] = {
                "identity": {"role": "ambient_extra", "kind": ""},
                "drives": {},
                "voice": {"style": "简短职能口吻"},
            }
            admitted.append(name)
    return sorted(set(admitted))


def _brief_issues(brief: SceneBrief, cast: dict[str, Any]) -> list[str]:
    """返回本场角色引用的问题（人类可读），无问题返回空表。

    副作用：把可归一的引用**就地**改写为 ``cast`` 的键。模型写「陈渡（记忆
    观察者）」时意图明确，为此重做整章规划毫无意义；归一即可。
    """
    issues: list[str] = []
    resolved_names: list[str] = []
    for actor in brief.actors:
        resolved = _canonical_persona(actor.persona, cast)
        if resolved is None:
            issues.append(f"「{actor.persona}」不是角色表中的名字")
            continue
        if resolved in resolved_names:
            issues.append(f"「{actor.persona}」与本场其他角色指向同一个人")
            continue
        resolved_names.append(resolved)
        actor.persona = resolved
    return issues


def _declared_cast(
    direction: ChapterDirection, cast: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """把导演声明的新人物并入候选角色表，返回（候选表，声明本身的问题）。

    声明过的新名字是合法的引用——这一点是刻意的：角色表不是封闭集合。但声明
    本身要受约束（不得顶替既有角色、不得自我重复、不得超量），否则「新增人物」
    会变成绕过角色图谱的暗门。
    """
    if len(direction.new_personas) > MAX_NEW_PERSONAS_PER_CHAPTER:
        return cast, [
            f"一章最多新增 {MAX_NEW_PERSONAS_PER_CHAPTER} 个人物，"
            f"本次声明了 {len(direction.new_personas)} 个"
        ]
    issues: list[str] = []
    merged = dict(cast)
    seen: set[str] = set()
    for spec in direction.new_personas:
        if spec.name in cast:
            issues.append(f"「{spec.name}」已是既有角色，不能重复声明")
        elif spec.name in seen:
            issues.append(f"「{spec.name}」被重复声明")
        else:
            seen.add(spec.name)
            merged[spec.name] = {
                "identity": {
                    "role": spec.identity,
                    "kind": spec.kind,
                    "bio": spec.bio or spec.identity,
                },
                "drives": {"primary": spec.drives},
                "voice": {"style": spec.voice},
            }
    return merged, issues


async def _register_new_personas(
    session: AsyncSession,
    work: StoryWorkModel,
    cast: dict[str, Any],
    direction: ChapterDirection,
) -> dict[str, Any]:
    """登记导演声明且**实际出场**的新人物，返回并入后的角色表。

    只登记出场的那几个：声明了没用上的不带入角色表——它会被带进后续每一章
    的上下文和每个角色的信息集，一次随手声明不该永久抬高后续成本。
    """
    used = {actor.persona for scene in direction.scenes for actor in scene.actors}
    merged = dict(cast)
    added: list[PersonaSpecModel] = []
    for spec in direction.new_personas:
        if spec.name not in used or spec.name in merged:
            continue
        entry = {
            "identity": {
                "role": spec.identity,
                "kind": spec.kind,
                "bio": spec.bio or spec.identity,
            },
            "drives": {"primary": spec.drives},
            "voice": {"style": spec.voice},
        }
        merged[spec.name] = entry
        added.append(
            PersonaSpecModel(
                id=uuid.uuid4(),
                work_id=work.id,
                name=spec.name,
                stable_traits=[spec.drives, spec.voice],
                **entry,
            )
        )
    if added:
        session.add_all(added)
        await session.flush()
    return merged

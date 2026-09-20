"""类型资源包：机械器物 UX 的确定性规则 + 原则透镜装配入口。

叙事/设定类问题（换身体感、双身份、外挂契约）不在此写死禁句；
见 ``principle_lenses`` → Agent 推导 ``work_conventions``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from regent.novel.domain.principle_lenses import resolve_lenses


@dataclass(frozen=True)
class GenreRule:
    rule_id: str
    kind: str  # world_default | reader_contract | premise_scale
    statement: str
    trigger_terms: tuple[str, ...] = ()
    hard: bool = True
    check: str | None = None  # 确定性检查 id；叙事类应为 None


@dataclass(frozen=True)
class GenrePack:
    pack_id: str
    triggers: tuple[str, ...]
    rules: tuple[GenreRule, ...] = field(default_factory=tuple)


# 仅保留可用 regex/启发式稳定判定的器物 UX；其余交给透镜 + 本作公约 Agent
_URBAN = GenrePack(
    pack_id="urban_entertainment",
    triggers=("都市文娱", "都市", "恋综", "职场"),
    rules=(
        GenreRule(
            rule_id="im_contact_density",
            kind="world_default",
            statement=(
                "现代 IM（微信等）不得呈现为「全部联系人/全网会话仅个位数且当作全貌」；"
                "可聚焦头部会话，但须暗示其余列表仍在。"
            ),
            trigger_terms=("微信", "置顶", "未读", "聊天列表", "通讯录", "会话"),
            check="im_contact_density",
        ),
        GenreRule(
            rule_id="im_recency_sort",
            kind="world_default",
            statement="会话列表按最近一次互动排序呈现，禁止静态点名册观感。",
            trigger_terms=("微信", "置顶", "聊天列表", "会话", "消息"),
            check="im_recency_sort",
        ),
        GenreRule(
            rule_id="urban_prop_ux",
            kind="world_default",
            statement=(
                "出现微信/手机/置顶/未读时，交互须符合当代微信默认："
                "可用未读红点、置顶、备注、语音通话、滑动列表；"
                "禁止写成私聊可见『已读』回执（微信没有已读未回）；"
                "禁止用灰头像表示闲置/不活跃联系人（会话列表无此常态）。"
            ),
            trigger_terms=("微信", "手机", "置顶", "未读", "已读", "头像"),
            check="urban_prop_ux",
        ),
        GenreRule(
            rule_id="wechat_false_ux",
            kind="world_default",
            statement=(
                "微信私聊无『已读/已读未回』回执；会话列表不以灰头像表示闲置联系人。"
                "正文若写成界面可见已读状态或灰头像常态，属器物常识错误。"
            ),
            trigger_terms=("微信", "已读", "头像", "未回", "会话"),
            check="wechat_false_ux",
        ),
    ),
)

_PACKS: tuple[GenrePack, ...] = (_URBAN,)


def resolve_packs(keywords: Iterable[str] | None) -> list[GenrePack]:
    """按方向关键词装配资源包；可叠加，保序去重。"""
    tokens = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not tokens:
        return []
    out: list[GenrePack] = []
    seen: set[str] = set()
    for pack in _PACKS:
        if pack.pack_id in seen:
            continue
        hit = False
        for t in pack.triggers:
            if t in set(tokens):
                hit = True
                break
            if any(t in kw or kw in t for kw in tokens):
                hit = True
                break
        if not hit:
            continue
        seen.add(pack.pack_id)
        out.append(pack)
    return out


def flatten_rails(packs: Sequence[GenrePack]) -> list[dict[str, Any]]:
    """写入 generation_context.commons_rails 的结构化列表。"""
    rails: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for pack in packs:
        for rule in pack.rules:
            if rule.rule_id in seen_rules:
                continue
            seen_rules.add(rule.rule_id)
            rails.append(
                {
                    "pack_id": pack.pack_id,
                    "rule_id": rule.rule_id,
                    "kind": rule.kind,
                    "statement": rule.statement,
                    "trigger_terms": list(rule.trigger_terms),
                    "hard": bool(rule.hard),
                    "check": rule.check,
                }
            )
    return rails


def commons_rails_for_keywords(keywords: Iterable[str] | None) -> list[dict[str, Any]]:
    """机械器物规则。叙事公约见 principle_lenses + work_conventions。"""
    return flatten_rails(resolve_packs(keywords))


def assemble_context_rails(
    keywords: Iterable[str] | None,
    *,
    work_conventions: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (commons_rails, principle_lenses)。

    commons_rails = 机械 UX + 已推导的本作公约；lenses 供 Agent 继续推理。
    """
    from regent.novel.domain.work_conventions import conventions_as_rails

    rails = commons_rails_for_keywords(keywords)
    rails = rails + conventions_as_rails(work_conventions)
    lenses = [
        {
            "lens_id": lens.lens_id,
            "category": lens.category,
            "when": lens.when,
            "questions": list(lens.questions),
            "anti_patterns": list(lens.anti_patterns),
        }
        for lens in resolve_lenses(keywords)
    ]
    return rails, lenses


def active_rule_ids(rails: Sequence[dict[str, Any]] | None) -> set[str]:
    return {str(r.get("rule_id") or "") for r in (rails or []) if r.get("rule_id")}

"""故事方向关键词：默认候选池 + 用户勾选/自填，不写死单一方向串。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Iterable, Sequence

# 产品默认展示的可选标签（用户可勾选子集）
DIRECTION_KEYWORD_OPTIONS: tuple[str, ...] = (
    "都市文娱",
    "文抄公",
    "恋综",
    "性转",
    "女频网文",
    "男频",
    "玄幻修仙",
    "悬疑推理",
    "末世求生",
    "甜宠",
    "爽文",
    "系统流",
    "重生",
    "穿书",
    "职场",
)

# 新建时 UI 默认勾选的子集（用户可改）
DEFAULT_DIRECTION_KEYWORD_SELECTION: tuple[str, ...] = (
    "都市文娱",
    "文抄公",
    "恋综",
    "性转",
    "女频网文",
)

ASSUMPTION_PREFIX = "story_direction_keywords:"
LOCKED_DIRECTION_PREFIX = "locked_direction:"
_KEYWORD_SPLIT = re.compile(r"[,，、+/|]+")
_MAX_KEYWORD_LEN = 48
_MAX_KEYWORDS = 24


def _clean_token(raw: str) -> str:
    t = str(raw or "").strip()
    if not t:
        return ""
    if len(t) > _MAX_KEYWORD_LEN:
        t = t[:_MAX_KEYWORD_LEN]
    return t


def parse_direction_keyword_text(text: str) -> list[str]:
    """CLI 等：逗号/顿号分隔的一串关键词。"""
    if not str(text or "").strip():
        return []
    parts = [_clean_token(p) for p in _KEYWORD_SPLIT.split(text)]
    return normalize_direction_keywords(parts, ())


def normalize_direction_keywords(
    selected: Iterable[str] | None,
    custom: Iterable[str] | None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(selected or []) + list(custom or []):
        token = _clean_token(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= _MAX_KEYWORDS:
            break
    return out


def format_direction_line(keywords: Sequence[str]) -> str:
    kw = normalize_direction_keywords(keywords, ())
    if not kw:
        return ""
    return " + ".join(kw)


def assumption_line(keywords: Sequence[str]) -> str:
    kw = normalize_direction_keywords(keywords, ())
    return f"{ASSUMPTION_PREFIX}{json.dumps(kw, ensure_ascii=False)}"


def parse_assumption_line(line: str) -> list[str]:
    text = str(line or "").strip()
    if not text.startswith(ASSUMPTION_PREFIX):
        return []
    try:
        data = json.loads(text[len(ASSUMPTION_PREFIX) :])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return normalize_direction_keywords(data, ())


def keywords_from_assumptions(assumptions: Iterable[str] | None) -> list[str]:
    for line in assumptions or []:
        kw = parse_assumption_line(line)
        if kw:
            return kw
    return []


def locked_direction_from_assumptions(
    assumptions: Iterable[str] | None,
) -> dict[str, Any]:
    for line in assumptions or []:
        text = str(line or "").strip()
        if not text.startswith(LOCKED_DIRECTION_PREFIX):
            continue
        try:
            data = json.loads(text[len(LOCKED_DIRECTION_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def clarify_rails_from_assumptions(assumptions: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for line in assumptions or []:
        text = str(line or "").strip()
        if text.startswith("澄清："):
            out.append(text)
    return out


def _power_rail_for_keywords(keywords: Sequence[str]) -> str:
    kw = normalize_direction_keywords(keywords, ())
    if "文抄公" in kw:
        return (
            "金手指类型仅限「文抄公」。具体怎么抄、边界、资源、代价类型、"
            "首次兑现方式——全部由本章剧本候选发明；选中稿写入正史，种子层不得预写。"
        )
    if kw:
        return (
            "金手指/核心外挂须与方向关键词相容，但机制细则、边界与代价"
            "全部由本章剧本候选发明；选中稿写入正史，种子层不得预写。"
        )
    return (
        "是否使用金手指、何种类型——由编剧在 raw_intent 与方向关键词语境下发明；"
        "选中稿写入正史，种子层不得预写操作细则。"
    )


def _must_not_for_keywords(keywords: Sequence[str]) -> str:
    base = (
        "禁止家常伦理逼债开局；禁止金手指只旁白不改变局面；"
        "禁止把未在正史确立的机制术语写进种子说明。"
    )
    extra: list[str] = []
    if "女频网文" in keywords or "性转" in keywords:
        extra.append("禁止男频战神口吻；禁止无性转身体错位（若含性转标签）")
    if "恋综" in keywords:
        extra.append(
            "禁止把「观众骂剧本/剧本女」当核心代价（恋综默认有剧本，无真实杀伤力）"
        )
    if not extra:
        return base
    return base + "；" + "；".join(extra)


def apply_keyword_rails(fragment: dict[str, str], keywords: Sequence[str]) -> dict[str, str]:
    """把用户关键词写入 fragment 副本（direction 行与 rails 文案）。"""
    out = deepcopy(fragment)
    kw = normalize_direction_keywords(keywords, ())
    line = format_direction_line(kw)
    out["direction"] = line
    out["direction_keywords"] = ",".join(kw)

    dir_hint = line or "（用户未选方向标签，按创作意图与 genre 自由发挥）"
    out["brief"] = (
        "这是产品方向，不是大纲。用户选定的方向标签："
        f"{dir_hint}。"
        "禁止从种子复述具体人名、节目名、关系网、打脸步骤或金手指操作细则。"
        "编剧须自行发明舞台与戏核；导演 BRIEF 派题后写候选。"
    )
    out["goal"] = (
        f"让第一章在「{dir_hint}」所界定的类型感内成立：读者愿意跟读；"
        "具体情节、人物与机制由候选与择优决定。"
    )
    out["power"] = _power_rail_for_keywords(kw)
    out["must_not"] = _must_not_for_keywords(kw)
    out["hook"] = "由编剧在方向标签与创作意图内自定开篇压力，种子不写死。"
    out["immersion"] = "单一主角有限视角；处境感由正文发明，不由种子说明书代写。"
    return out


def fragment_for_run(
    base: dict[str, str],
    *,
    direction_keywords: Sequence[str] | None = None,
    direction_custom: Sequence[str] | None = None,
    use_default_when_empty: bool = True,
) -> dict[str, str]:
    """实验/连载：解析关键词并套用到 keyword_rails 类 fragment。"""
    if base.get("keyword_rails") != "1":
        return deepcopy(base)
    kw = normalize_direction_keywords(direction_keywords, direction_custom)
    if not kw and use_default_when_empty:
        kw = list(DEFAULT_DIRECTION_KEYWORD_SELECTION)
    return apply_keyword_rails(base, kw)


def catalog_payload() -> dict[str, Any]:
    return {
        "options": list(DIRECTION_KEYWORD_OPTIONS),
        "default_selection": list(DEFAULT_DIRECTION_KEYWORD_SELECTION),
    }

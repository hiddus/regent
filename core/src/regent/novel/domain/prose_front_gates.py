"""明显错稿前置门：在执笔提示 + VALIDATE 拦下，不交给责编硬审。

身份与禁词从作品活设定读取；不写死单本样章人名。
"""

from __future__ import annotations

import re
from typing import Any

from regent.novel.domain.dossiers import resolve_narration_roles
from regent.novel.domain.world_bible import (
    pov_contract_hard_fails,
    prose_style_writer_hint,
    resolve_prose_style,
    wants_she_host_narration,
)

# 元叙事账号感：模式类禁令（不是某本书的角色名）
_META_OPERATOR_RE = re.compile(r"(临时)?操作员\s*[A-Za-z甲乙丙丁0-9一二三四五六七八九十]")
_META_OPERATOR_PHRASES = (
    "临时操作员",
    "操作员代号",
)

_POWER_MARKERS: tuple[str, ...] = (
    "系统",
    "抽取",
    "羁绊",
    "浓度",
    "冷却",
    "金手指",
    "浮名",
    "鱼塘",
)

# 脚本点名标记的可读同义/场面表现；避免正文写了机制却因缺一字硬杀。
_POWER_ALIASES: dict[str, tuple[str, ...]] = {
    "冷却": (
        "冷却",
        "冷却时间",
        "暂时失灵",
        "一时听不见",
        "听不见了",
        "耳鸣",
        "需要缓一缓",
        "缓一缓",
        "力竭",
        "余温",
        "再听不到",
        "暂时用不出",
    ),
    "系统": ("系统", "面板", "进度条"),
    "抽取": ("抽取", "抽到", "抽中"),
    "羁绊": ("羁绊",),
    "浓度": ("浓度",),
    "金手指": ("金手指", "外挂", "超能"),
    "浮名": ("浮名",),
    "鱼塘": ("鱼塘",),
}

# 章审校耗尽后允许 coerce 的顾问级前缀（事实错误不在此列）
REVIEW_COERCE_PREFIXES: tuple[str, ...] = (
    "[editor-soft:",
    "[soft-outline]",
    "[commons:",
    "[soft-cont:",
)


def front_gate_writer_hint(
    *,
    prose_style: dict[str, Any] | None = None,
    chapter_no: int = 1,
    cast: dict[str, Any] | list[str] | None = None,
    world_bible: dict[str, Any] | None = None,
    reader_contract: dict[str, Any] | None = None,
) -> str:
    """执笔 system 追加：把易拖到责编的硬伤提前写死。"""
    parts: list[str] = []
    style_hint = prose_style_writer_hint(prose_style)
    if style_hint:
        parts.append(style_hint)
    roles = resolve_narration_roles(
        prose_style=prose_style, cast=cast, world_bible=world_bible
    )
    host = str(roles.get("host_name") or "")
    traveler = str(roles.get("traveler_name") or "")
    identity = ""
    if host or traveler:
        identity = (
            f"叙述宿主名={host or '未指定'}；意识名={traveler or '未指定'}；"
            "正文叙述主语须用宿主真名与契约人称，禁止另造元叙事账号称呼。"
        )
    avoid = []
    if isinstance(prose_style, dict):
        avoid.extend(str(x) for x in (prose_style.get("avoid") or []) if str(x).strip())
    if isinstance(reader_contract, dict):
        avoid.extend(
            str(x) for x in (reader_contract.get("forbidden") or []) if str(x).strip()
        )
    avoid_txt = "、".join(avoid[:6]) if avoid else "无额外禁用"
    parts.append(
        "【前置硬约束】"
        + identity
        + "禁止用『操作员+代号』等非卡司元叙事账号称呼叙述者；"
        "章题人名须与正文宿主/卡司一致；"
        "金手指/系统规则须在正文被读者看见（能做/不能做/代价至少落地一处），"
        f"禁止只甩黑话面板；作品禁用={avoid_txt}。"
    )
    if int(chapter_no) == 1:
        parts.append(
            "首章必须让读者站稳：谁在壳里、金手指怎么用、眼前压力是什么。"
        )
    return " ".join(parts)


def front_gate_hard_fails(
    content: str,
    *,
    prose_style: dict[str, Any] | None = None,
    title: str = "",
    cast: dict[str, Any] | list[str] | None = None,
    production_packet: dict[str, Any] | None = None,
    chapter_no: int = 1,
    min_chars: int = 600,
    world_bible: dict[str, Any] | None = None,
    reader_contract: dict[str, Any] | None = None,
) -> list[str]:
    """汇总前置硬失败（确定性，无模型）。"""
    text = (content or "").strip()
    ps = prose_style or {}
    fails: list[str] = []
    fails.extend(
        pov_contract_hard_fails(
            text,
            ps,
            min_chars=min_chars,
            cast=cast,
            world_bible=world_bible,
        )
    )
    fails.extend(
        _banned_label_fails(
            text,
            cast=cast,
            prose_style=ps,
            reader_contract=reader_contract,
            world_bible=world_bible,
        )
    )
    if len(text) >= min_chars:
        fails.extend(_first_person_leak_fails(text, ps))
        fails.extend(
            _title_cast_fails(
                title=title,
                content=text,
                cast=cast,
                prose_style=ps,
                world_bible=world_bible,
            )
        )
        fails.extend(
            _power_surface_fails(
                text,
                production_packet=production_packet,
                chapter_no=chapter_no,
            )
        )
        fails.extend(_near_duplicate_paragraph_fails(text))
    return list(dict.fromkeys(fails))


def review_issues_may_coerce(issues: list[Any] | None) -> bool:
    """仅结构化顾问标签允许修满后 coerce；禁止靠「入场/连续」等关键词误放。

    章级审校没有可靠入场状态快照时，不得调用空状态误报启发式。
    """
    rows = [str(x) for x in (issues or []) if str(x).strip()]
    if not rows:
        return True
    for item in rows:
        if item.startswith("[pov:"):
            return False
        if item.startswith(
            ("[front:title", "[front:cast", "[front:banned", "[front:redundant")
        ):
            return False
        if item.startswith("[front:power_surface"):
            continue
        if item.startswith(REVIEW_COERCE_PREFIXES):
            continue
        # 无结构化 soft 前缀的其余问题（含自然语言「跳场/入场」）不得 coerce
        return False
    return True


def _banned_label_fails(
    text: str,
    *,
    cast: dict[str, Any] | list[str] | None,
    prose_style: dict[str, Any] | None,
    reader_contract: dict[str, Any] | None,
    world_bible: dict[str, Any] | None,
) -> list[str]:
    hits: list[str] = []
    for phrase in _META_OPERATOR_PHRASES:
        if phrase in text:
            hits.append(phrase)
    if _META_OPERATOR_RE.search(text):
        hits.append("操作员+代号")
    # 作品自行声明的禁用（活配置）
    banned_cfg: list[str] = []
    if isinstance(prose_style, dict):
        banned_cfg.extend(str(x) for x in (prose_style.get("avoid") or []))
    if isinstance(reader_contract, dict):
        banned_cfg.extend(str(x) for x in (reader_contract.get("forbidden") or []))
    roles = resolve_narration_roles(
        prose_style=prose_style, cast=cast, world_bible=world_bible
    )
    cast_names = set(roles.get("cast_names") or [])
    for raw in banned_cfg:
        token = str(raw).strip()
        if len(token) < 2 or token in cast_names:
            continue
        if token in text and ("操作员" in token or "代号" in token or "账号" in token):
            hits.append(token)
    if not hits:
        return []
    return [
        "[front:banned_label] 正文出现非卡司元叙事称呼（"
        + "、".join(list(dict.fromkeys(hits))[:4])
        + "）；须改成角色真名/宿主叙述，不得交责编。"
    ]


def _strip_quotes(text: str) -> str:
    """去掉对白引号内内容，降低『我』误报。"""
    out = text
    for a, b in (("「", "」"), ("『", "』"), ('"', '"'), ("“", "”"), ("'", "'")):
        out = re.sub(re.escape(a) + r"[\s\S]*?" + re.escape(b), " ", out)
    return out


def _first_person_leak_fails(text: str, prose_style: dict[str, Any]) -> list[str]:
    if not wants_she_host_narration(prose_style):
        return []
    body = _strip_quotes(text)
    wo = body.count("我")
    she = text.count("她")
    if wo >= 12 and wo > max(she, 1) * 0.6:
        return [
            "[front:first_person] 行文契约为第三人称『她』/宿主壳，"
            f"但叙述层『我』约{wo}次、正文『她』{she}次，明显串成第一人称；"
            "须执笔改回，不得交责编。"
        ]
    return []


def _cast_names(cast: dict[str, Any] | list[str] | None) -> list[str]:
    if isinstance(cast, dict):
        names = list(cast.keys())
    else:
        names = [str(x) for x in (cast or [])]
    cleaned: list[str] = []
    for raw in names:
        name = str(raw).split("（", 1)[0].split("(", 1)[0].strip()
        if name:
            cleaned.append(name)
    return cleaned


def _title_cast_fails(
    *,
    title: str,
    content: str,
    cast: dict[str, Any] | list[str] | None,
    prose_style: dict[str, Any],
    world_bible: dict[str, Any] | None = None,
) -> list[str]:
    title_s = (title or "").strip()
    if not title_s:
        return []
    if re.match(r"^第\s*\d+\s*章", title_s) or re.match(
        r"^第[一二三四五六七八九十百零〇两\d]+章", title_s
    ):
        return []
    roles = resolve_narration_roles(
        prose_style=prose_style, cast=cast, world_bible=world_bible
    )
    names = list(roles.get("cast_names") or _cast_names(cast))
    host = str(roles.get("host_name") or "")
    fails: list[str] = []
    # 章题里 2–4 字中文名：不在卡司且宿主已在正文出现 → 脏大纲名
    for m in re.finditer(r"[\u4e00-\u9fff]{2,4}", title_s):
        ghost = m.group(0)
        if ghost in names:
            continue
        if host and ghost != host and host in content[:1200] and ghost not in content:
            fails.append(
                f"[front:title_cast] 章题含非卡司名『{ghost}』，与宿主『{host}』冲突；"
                "须改正题名后再审，不得交责编。"
            )
            break
    for name in names:
        if name in title_s and name not in content:
            fails.append(
                f"[front:title_cast] 章题含『{name}』但正文未出现该名；"
                "题文不一致，须执笔或改题对齐。"
            )
            break
    return fails


def _power_surface_fails(
    text: str,
    *,
    production_packet: dict[str, Any] | None,
    chapter_no: int,
) -> list[str]:
    packet = production_packet or {}
    script = packet.get("script") if isinstance(packet.get("script"), dict) else {}
    power_blob = " ".join(
        str(script.get(k) or "")
        for k in (
            "power_mechanism",
            "power_limits",
            "power_payoff",
            "cost",
            "cost_type",
        )
    )
    if not power_blob.strip() and int(chapter_no) != 1:
        return []
    expected = [m for m in _POWER_MARKERS if m in power_blob]
    if not expected:
        return []
    present: list[str] = []
    for m in expected:
        aliases = _POWER_ALIASES.get(m, (m,))
        if any(a in text for a in aliases):
            present.append(m)
    if present:
        return []
    return [
        "[front:power_surface] 选定剧本需要金手指外显，但正文完全未出现"
        f"（缺：{'、'.join(expected[:5])}）；"
        "须让读者看见机制或代价，不得只留给责编挑黑话。"
    ]


def _near_duplicate_paragraph_fails(text: str) -> list[str]:
    from regent.novel.domain.repair_locate import find_near_duplicate_paragraphs

    hits = find_near_duplicate_paragraphs(text)
    if not hits:
        return []
    hit = hits[0]
    snip = hit.quote[:36].replace("\n", " ")
    if hit.kind == "identical":
        return [
            "[front:redundant] 同章出现完全相同段落重复；"
            f"须剪辑去重后再验收｜段落下标 {hit.para_index_a},{hit.para_index_b}"
            f"｜摘录「{snip}」"
        ]
    return [
        "[front:redundant] 同章大段叙述几乎整段复读；"
        f"须去重压缩｜段落下标 {hit.para_index_a},{hit.para_index_b}"
        f"｜摘录「{snip}」"
    ]


__all__ = [
    "REVIEW_COERCE_PREFIXES",
    "front_gate_hard_fails",
    "front_gate_writer_hint",
    "resolve_prose_style",
    "review_issues_may_coerce",
]

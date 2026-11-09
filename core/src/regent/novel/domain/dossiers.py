"""穿越/双身小传：穿越者意识 vs 原身躯壳——结构要求，不是某一本书的人设模板。"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

# 命中任一则要求双小传（身份账本拆分）
_DUAL_TRIGGERS = (
    "性转",
    "穿越",
    "穿书",
    "重生",
    "换身",
    "附身",
    "系统流",
)

TRAVELER_KIND = "traveler"
HOST_BODY_KIND = "host_body"

_KIND_ALIASES = {
    "穿越者": TRAVELER_KIND,
    "穿越意识": TRAVELER_KIND,
    "主角意识": TRAVELER_KIND,
    "灵魂": TRAVELER_KIND,
    "traveler": TRAVELER_KIND,
    "原身": HOST_BODY_KIND,
    "原主": HOST_BODY_KIND,
    "宿主身体": HOST_BODY_KIND,
    "躯壳": HOST_BODY_KIND,
    "host_body": HOST_BODY_KIND,
    "host": HOST_BODY_KIND,
}


def needs_dual_dossiers(keywords: Iterable[str] | None) -> bool:
    tokens = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not tokens:
        return False
    for t in _DUAL_TRIGGERS:
        if any(t in kw or kw in t for kw in tokens):
            return True
    return False


def normalize_kind(raw: str) -> str:
    text = str(raw or "").strip()
    if text in (TRAVELER_KIND, HOST_BODY_KIND):
        return text
    return _KIND_ALIASES.get(text, "")


def persona_kind(persona: dict[str, Any] | None) -> str:
    if not isinstance(persona, dict):
        return ""
    identity = persona.get("identity")
    if isinstance(identity, dict):
        kind = normalize_kind(str(identity.get("kind") or identity.get("role_kind") or ""))
        if kind:
            return kind
        kind = normalize_kind(str(identity.get("role") or ""))
        if kind:
            return kind
    kind = normalize_kind(str(persona.get("kind") or ""))
    if kind:
        return kind
    name = str(persona.get("name") or "")
    if "原身" in name:
        return HOST_BODY_KIND
    if "穿越" in name:
        return TRAVELER_KIND
    return ""


def persona_bio(persona: dict[str, Any] | None) -> str:
    if not isinstance(persona, dict):
        return ""
    identity = persona.get("identity")
    if isinstance(identity, dict):
        for key in ("bio", "dossier", "backstory", "role"):
            val = str(identity.get(key) or "").strip()
            if val and key != "role":
                return val
            if key == "role" and len(val) >= 12:
                return val
    for key in ("bio", "dossier", "drives", "drive"):
        raw = persona.get(key)
        if isinstance(raw, dict):
            val = str(raw.get("primary") or raw.get("bio") or "").strip()
        else:
            val = str(raw or "").strip()
        if len(val) >= 8:
            return val
    return ""


def cast_as_persona_list(cast: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, meta in (cast or {}).items():
        row = dict(meta or {})
        row["name"] = name
        out.append(row)
    return out


def _clean_persona_name(raw: str) -> str:
    return str(raw or "").split("（", 1)[0].split("(", 1)[0].strip()


def resolve_narration_roles(
    *,
    prose_style: dict[str, Any] | None = None,
    cast: dict[str, Any] | list[str] | None = None,
    world_bible: dict[str, Any] | None = None,
    personas: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """从作品活设定解析宿主叙述名 / 穿越者意识名（不绑样本书人名）。"""
    rows: list[dict[str, Any]] = list(personas or [])
    if not rows and isinstance(world_bible, dict):
        rows.extend(
            x for x in (world_bible.get("personas") or []) if isinstance(x, dict)
        )
    if isinstance(cast, dict):
        rows.extend(cast_as_persona_list(cast))
    elif isinstance(cast, list):
        for name in cast:
            rows.append({"name": str(name)})

    host = ""
    traveler = ""
    cast_names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = _clean_persona_name(str(row.get("name") or ""))
        if not name:
            continue
        if name not in seen:
            cast_names.append(name)
            seen.add(name)
        kind = persona_kind(row)
        if kind == HOST_BODY_KIND and not host:
            host = name
        elif kind == TRAVELER_KIND and not traveler:
            traveler = name

    ps = prose_style or {}
    blob = (
        f"{ps.get('viewpoint') or ''}"
        f"{ps.get('narrative_distance') or ''}"
        f"{ps.get('tone') or ''}"
    )
    # 视角文案里点名的卡司名：优先补宿主
    if not host:
        for name in cast_names:
            if name and name in blob and (
                "宿主" in blob or "躯壳" in blob or "第三人称" in blob or "她" in blob
            ):
                host = name
                break
    if not traveler:
        for name in cast_names:
            if name and name in blob and ("穿越" in blob or "意识" in blob):
                if name != host:
                    traveler = name
                    break
    return {
        "host_name": host,
        "traveler_name": traveler,
        "cast_names": cast_names,
        "style_blob": blob,
    }


def dual_dossier_gaps(
    personas: Sequence[dict[str, Any]] | None,
) -> list[str]:
    """缺穿越者/原身小传时返回人类可读问题（不绑定某书人设）。"""
    rows = list(personas or [])
    by_kind = {persona_kind(p): p for p in rows if persona_kind(p)}
    issues: list[str] = []
    if TRAVELER_KIND not in by_kind:
        issues.append(
            "缺少「穿越者」小传：须写明穿越前身份、死因/穿越契机、性格声纹、当下目标"
        )
    else:
        bio = persona_bio(by_kind[TRAVELER_KIND])
        if len(bio) < 20:
            issues.append("「穿越者」小传过短：须可核对的前史与动机，不能只有名字")
    if HOST_BODY_KIND not in by_kind:
        issues.append(
            "缺少「原身」小传：须写明外貌/行业资源、为何能独自自由活动、过人之处或金丝笼风险"
        )
    else:
        bio = persona_bio(by_kind[HOST_BODY_KIND])
        if len(bio) < 20:
            issues.append(
                "「原身」小传过短：若能高调独自活动，须交代过人之处或处境，否则易穿帮"
            )
        # 抽象混串：原身小传里同时出现「穿越者前职死亡」模式（职业+已死）
        blob = bio + str((by_kind[HOST_BODY_KIND].get("identity") or {}))
        if any(x in blob for x in ("已死亡", "已死", "尸体")) and any(
            x in blob for x in ("穿越前", "前世", "原来的工作", "前职")
        ):
            issues.append(
                "原身小传疑似写入了穿越者死亡/前职：两套身份须分栏，勿混成一条宿主档案"
            )
    return issues


def outline_persona_instructions() -> str:
    return (
        "若方向含性转/穿越/换身/系统：personas 必须至少包含两类小传——"
        "① kind=traveler（穿越者意识：前史身份、死因或穿越契机、声纹、目标）；"
        "② kind=host_body（原身躯壳：姓名外表、行业资源、为何能独自自由活动、过人之处；"
        "禁止把穿越者的死亡/前职写成原身）。每条须有 bio（≥40字）。"
        "具体人名与职业由本作发明，勿套用他书模板。"
    )

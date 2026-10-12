"""大纲用名到世界书规范名的显式映射。

只替换世界书里写明的别名，不按相似名字猜测合并。
"""

from __future__ import annotations

from typing import Any


def alias_map_from_bible(bible: dict[str, Any] | None) -> dict[str, str]:
    """返回 别名 -> 规范名。同名冲突时保留先出现的规范名。"""
    if not isinstance(bible, dict):
        return {}
    mapping: dict[str, str] = {}
    for persona in bible.get("personas") or []:
        if not isinstance(persona, dict):
            continue
        canon = str(persona.get("name") or "").strip()
        if not canon:
            continue
        for raw in persona.get("aliases") or []:
            alias = str(raw or "").strip()
            if alias and alias != canon and alias not in mapping:
                mapping[alias] = canon
    for place in bible.get("places") or []:
        if not isinstance(place, dict):
            continue
        canon = str(place.get("name") or "").strip()
        if not canon:
            continue
        for raw in place.get("aliases") or []:
            alias = str(raw or "").strip()
            if alias and alias != canon and alias not in mapping:
                mapping[alias] = canon
    return mapping


def apply_alias_map(value: Any, mapping: dict[str, str]) -> Any:
    """在字符串、字典和列表里替换别名。长别名优先，避免短名截断长名。"""
    if not mapping:
        return value
    if isinstance(value, str):
        text = value
        for alias in sorted(mapping, key=len, reverse=True):
            text = text.replace(alias, mapping[alias])
        return text
    if isinstance(value, dict):
        return {key: apply_alias_map(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [apply_alias_map(item, mapping) for item in value]
    return value


def accept_alias_bindings(
    bible: dict[str, Any] | None,
    bindings: list[Any] | None,
    *,
    outline_text: str,
    bible_text: str,
) -> dict[str, Any]:
    """只收下两边原文里都出现、且证据同时写出两边原词的同一实体。"""
    import copy

    out = copy.deepcopy(bible if isinstance(bible, dict) else {})
    personas = [dict(p) for p in (out.get("personas") or []) if isinstance(p, dict)]
    by_name = {str(p.get("name") or ""): p for p in personas if str(p.get("name") or "")}
    places = [dict(p) for p in (out.get("places") or []) if isinstance(p, dict)]
    place_by_name = {str(p.get("name") or ""): p for p in places if str(p.get("name") or "")}
    outline = str(outline_text or "")
    bible_blob = str(bible_text or "")
    for item in bindings or []:
        if not isinstance(item, dict) or not item.get("same_entity"):
            continue
        alias = str(item.get("alias") or "").strip()
        canon = str(item.get("canon_name") or "").strip()
        evidence = str(item.get("evidence") or "")
        if not alias or not canon or alias == canon or alias not in outline:
            continue
        if alias not in evidence or canon not in evidence:
            continue
        if canon not in by_name and canon not in bible_blob:
            continue
        if canon in by_name:
            aliases = [str(a) for a in (by_name[canon].get("aliases") or [])]
            if alias not in aliases:
                aliases.append(alias)
                by_name[canon]["aliases"] = aliases
            continue
        place = place_by_name.get(canon)
        if place is None:
            place = {"name": canon, "aliases": []}
            places.append(place)
            place_by_name[canon] = place
        aliases = [str(a) for a in (place.get("aliases") or [])]
        if alias not in aliases:
            aliases.append(alias)
            place["aliases"] = aliases
    if personas:
        out["personas"] = personas
    if places:
        out["places"] = places
    return out


def outline_mentions_canon(bible: dict[str, Any] | None, texts: list[str]) -> bool:
    """大纲文本里已经出现规范名或已登记别名时，不必再请模型配对。"""
    if not isinstance(bible, dict):
        return True
    names: list[str] = []
    for persona in bible.get("personas") or []:
        if not isinstance(persona, dict):
            continue
        canon = str(persona.get("name") or "").strip()
        if canon:
            names.append(canon)
        aliases = persona.get("aliases") or []
        names.extend(str(alias).strip() for alias in aliases if str(alias).strip())
    for place in bible.get("places") or []:
        if not isinstance(place, dict):
            continue
        canon = str(place.get("name") or "").strip()
        if canon:
            names.append(canon)
        aliases = place.get("aliases") or []
        names.extend(str(alias).strip() for alias in aliases if str(alias).strip())
    if not names:
        return True
    blob = "\n".join(str(text or "") for text in texts)
    if not blob.strip():
        return True
    return any(name in blob for name in names)


def bible_alias_text(bible: dict[str, Any] | None) -> str:
    if not isinstance(bible, dict):
        return ""
    parts = [
        str(bible.get("world_premise") or ""),
        str(bible.get("background") or ""),
        str(bible.get("power_system") or ""),
    ]
    for persona in bible.get("personas") or []:
        if isinstance(persona, dict):
            parts.append(str(persona.get("name") or ""))
            parts.append(str(persona.get("bio") or ""))
    for place in bible.get("places") or []:
        if isinstance(place, dict):
            parts.append(str(place.get("name") or ""))
    return "\n".join(part for part in parts if part)


__all__ = [
    "accept_alias_bindings",
    "alias_map_from_bible",
    "apply_alias_map",
    "bible_alias_text",
    "outline_mentions_canon",
]

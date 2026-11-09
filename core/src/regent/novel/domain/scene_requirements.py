"""场景核验要求清单：从已结算事件冻结，执笔与核验共用。

状态身份用 ``state_key``（结算侧实体键或 ``实体.属性``），自然语言 ``expected_value``
只作解释，不承担字典字符串全等比较。隐藏事件进入世界状态但不要求正文直接披露。

``REQUIREMENTS_VERSION`` 升级时不得原地改写已冻结 take 的清单（由 direction 钉版本）。
"""

from __future__ import annotations

from typing import Any


# r21_v2：实体+属性拆分；被覆盖的可见中间写入保留为 event 要求。
REQUIREMENTS_VERSION = "r21_v2"
LEGACY_REQUIREMENTS_VERSION = "r21_v1"

_ATTR_SEPARATORS = (".", "/", "·", ":")

# 文学细节属性：进入 working_state 留痕，但不构成硬核验要求。
# 关键世界状态（位置/归属/知识/承诺等）继续 require_direct_evidence。
_SOFT_STATE_ATTRIBUTES = frozenset(
    {
        "mood",
        "emotion",
        "posture",
        "expression",
        "gaze",
        "tone",
        "manner",
        "pose",
        "情绪",
        "姿态",
        "神情",
        "语气",
        "表情",
        "目光",
        "神态",
    }
)


def parse_state_slot(key: str) -> tuple[str, str]:
    """把结算键规范为 (entity, attribute)。

    仅识别显式分隔符（有来源规范化）；无分隔符时整键为实体，属性默认为 ``state``。
    不从自由句子机械猜属性。
    """
    text = str(key or "").strip()
    if not text:
        return ("", "state")
    for sep in _ATTR_SEPARATORS:
        if sep in text:
            entity, attr = text.split(sep, 1)
            entity, attr = entity.strip(), attr.strip()
            if entity and attr:
                return entity, attr
    return text, "state"


def freeze_scene_requirements(
    events: list[dict[str, Any]],
    *,
    version: str | None = None,
) -> list[dict[str, Any]]:
    """按事件顺序归并最终状态，并保留被覆盖的关键中间事件要求。"""
    ver = version or REQUIREMENTS_VERSION
    if ver == LEGACY_REQUIREMENTS_VERSION:
        return _freeze_v1(events)
    return _freeze_v2(events)


def _freeze_v1(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """r21_v1：每实体键一条最终要求（后写覆盖）。"""
    finals: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        visible = bool(event.get("reader_visible", True))
        statement = str(event.get("statement") or "")
        for key, value in (event.get("state_changes") or {}).items():
            state_key = str(key)
            finals[state_key] = {
                "requirement_id": f"state:{state_key}:final",
                "kind": "state",
                "event_index": index,
                "event_statement": statement,
                "state_key": state_key,
                "entity": state_key,
                "attribute": "state",
                "expected_value": str(value),
                "reader_visible": visible,
                "require_direct_evidence": visible,
            }
    return [finals[k] for k in sorted(finals)]


def _freeze_v2(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """r21_v2：独立属性不互相覆盖；同属性后写覆盖前写；被覆盖的可见写入→event 要求。"""
    finals: dict[tuple[str, str], dict[str, Any]] = {}
    event_reqs: list[dict[str, Any]] = []

    for index, event in enumerate(events):
        visible = bool(event.get("reader_visible", True))
        statement = str(event.get("statement") or "")
        for key, value in (event.get("state_changes") or {}).items():
            raw_key = str(key)
            entity, attr = parse_state_slot(raw_key)
            if not entity:
                continue
            # 无显式分隔符：state_key / requirement_id 保持实体键口径（兼容旧罐装）。
            # 有分隔符：state_key 为 实体.属性，id 带属性段。
            explicit_attr = attr != "state" or any(sep in raw_key for sep in _ATTR_SEPARATORS)
            if explicit_attr:
                state_key = f"{entity}.{attr}"
                state_rid = f"state:{entity}:{attr}:final"
                event_rid = f"event:{{idx}}:{entity}:{attr}"
            else:
                state_key = raw_key
                state_rid = f"state:{entity}:final"
                event_rid = f"event:{{idx}}:{entity}"
            slot = (entity, attr)
            prev = finals.get(slot)
            if (
                prev is not None
                and prev["event_index"] != index
                and prev.get("reader_visible")
                and prev["expected_value"] != str(value)
            ):
                # 中间关键动作：先前可见写入被后写覆盖为不同值，仍须正文曾呈现该动作。
                # 即便最终属性是文学细节（软核验），中间可见变化仍记 event 要求。
                event_reqs.append(
                    {
                        "requirement_id": event_rid.format(idx=prev["event_index"]),
                        "kind": "event",
                        "event_index": prev["event_index"],
                        "event_statement": prev.get("event_statement") or "",
                        "state_key": prev["state_key"],
                        "entity": entity,
                        "attribute": attr,
                        "expected_value": prev["expected_value"],
                        "reader_visible": True,
                        "require_direct_evidence": True,
                    }
                )
            finals[slot] = {
                "requirement_id": state_rid,
                "kind": "state",
                "event_index": index,
                "event_statement": statement,
                "state_key": state_key,
                "entity": entity,
                "attribute": attr,
                "expected_value": str(value),
                "reader_visible": visible,
                # 文学细节不硬核验；位置/归属/知识等关键状态仍要求正文证据。
                "require_direct_evidence": visible and attr not in _SOFT_STATE_ATTRIBUTES,
            }

    # 去重 event requirement_id（同槽多次覆盖只保留最早被挤掉的那次？保留全部按写入序）
    seen_event: set[str] = set()
    unique_events: list[dict[str, Any]] = []
    for item in event_reqs:
        rid = item["requirement_id"]
        if rid in seen_event:
            continue
        seen_event.add(rid)
        unique_events.append(item)

    state_list = [finals[k] for k in sorted(finals, key=lambda s: (s[0], s[1]))]
    return state_list + unique_events


def must_preserve_for_writer(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执笔/修订必须呈现的可见最终状态与关键中间事件。"""
    out: list[dict[str, Any]] = []
    for item in requirements:
        if not item.get("require_direct_evidence"):
            continue
        out.append(
            {
                "requirement_id": item["requirement_id"],
                "kind": item.get("kind") or "state",
                "state_key": item["state_key"],
                "expected_value": item["expected_value"],
                "source_event_index": item["event_index"],
                "source_excerpt": str(item.get("event_statement") or "")[:180],
            }
        )
    return out


def revision_conflicts_settled(
    revision_instruction: str,
    requirements: list[dict[str, Any]],
) -> list[str]:
    """粗检：修订指令显式要求删除/取消已结算且需证据的内容时回报导演。

    不做模糊语义匹配；只抓明显的「删除…」类指令与要求摘要的词面碰撞。
    """
    text = (revision_instruction or "").strip()
    if not text:
        return []
    delete_markers = ("删除", "去掉", "取消", "不要写", "勿写", "略去")
    if not any(marker in text for marker in delete_markers):
        return []
    conflicts: list[str] = []
    for item in requirements:
        if not item.get("require_direct_evidence"):
            continue
        blob = " ".join(
            [
                str(item.get("expected_value") or ""),
                str(item.get("state_key") or ""),
                str(item.get("event_statement") or ""),
            ]
        )
        # ≥4 字的连续汉字片段；命中修订指令即视为可能删掉已结算依据
        tokens = {
            blob[i : i + n]
            for n in (3, 4, 5, 6)
            for i in range(0, max(0, len(blob) - n + 1))
        }
        hit = next((tok for tok in tokens if len(tok.strip()) >= 3 and tok in text), None)
        if hit:
            conflicts.append(
                f"{item['requirement_id']}（{item['state_key']}）与修订指令冲突；"
                "改变已结算事件须 RETAKE，不能只靠 REWRITE"
            )
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for line in conflicts:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out

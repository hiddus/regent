"""故事主线进度：编剧动笔前主线必须已在，用到三分之二再续。"""

from __future__ import annotations

from typing import Any

CHAPTERS_PER_NODE = 3


def mainline_extension_due(
    chapter_no: int,
    node_count: int,
    *,
    chapters_per_node: int = CHAPTERS_PER_NODE,
) -> bool:
    """当前章已经走进主线覆盖范围的后三分之一时，先把主线往后续一截。"""
    if node_count <= 0 or chapter_no <= 1:
        return False
    covered = node_count * chapters_per_node
    return chapter_no * 3 > covered * 2


def current_node_index(
    chapter_no: int,
    *,
    chapters_per_node: int = CHAPTERS_PER_NODE,
) -> int:
    return max(0, (int(chapter_no) - 1) // chapters_per_node)


def node_payload(node: Any) -> dict[str, Any]:
    return {
        "node_id": str(getattr(node, "node_id", "") or ""),
        "ordinal": int(getattr(node, "ordinal", 0) or 0),
        "title": str(getattr(node, "title", "") or ""),
        "node_type": str(getattr(node, "node_type", "") or ""),
        "promise": str(getattr(node, "promise", "") or ""),
        "preconditions": list(getattr(node, "preconditions", None) or []),
        "consequences": list(getattr(node, "consequences", None) or []),
    }


def mainline_for_chapter(
    nodes: list[Any],
    chapter_no: int,
    *,
    chapters_per_node: int = CHAPTERS_PER_NODE,
) -> dict[str, Any]:
    """给编剧的主线切片：当前节点，以及前后各一个节点。"""
    if not nodes:
        return {"nodes": [], "current": None, "previous": None, "following": None}
    index = min(len(nodes) - 1, current_node_index(chapter_no, chapters_per_node=chapters_per_node))
    previous = node_payload(nodes[index - 1]) if index > 0 else None
    following = node_payload(nodes[index + 1]) if index + 1 < len(nodes) else None
    return {
        "nodes": [node_payload(node) for node in nodes],
        "current": node_payload(nodes[index]),
        "previous": previous,
        "following": following,
    }

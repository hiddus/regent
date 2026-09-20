"""Director issue ledger rules."""

from __future__ import annotations

from typing import Any

_HARD_ISSUE_STATUSES = frozenset({"missing", "contradicted"})
_OPEN_LEDGER_OUTCOMES = frozenset({"new", "repeat", "open"})


def _hard_issue_specs(validation: dict[str, Any] | None) -> list[dict[str, str]]:
    """从核验报告提取可追踪硬问题（requirement_id + status）。"""
    specs: list[dict[str, str]] = []
    for item in (validation or {}).get("requirements") or []:
        status = str(item.get("status") or "")
        if status not in _HARD_ISSUE_STATUSES:
            continue
        rid = str(item.get("requirement_id") or "").strip()
        if not rid:
            continue
        specs.append({"id": f"{rid}:{status}", "class": status})
    return specs


def _record_issue_ledger(
    take: dict[str, Any], *, content_hash: str, validation: dict[str, Any]
) -> list[dict[str, Any]]:
    """VALIDATE 出口：对比上轮硬问题，写入 issue_ledger（new/repeat/open/solved）。"""
    current = _hard_issue_specs(validation)
    ledger = list(take.get("issue_ledger") or [])
    prev_round = int(take.get("issue_ledger_round", 0) or 0)
    prev_by_id: dict[str, dict[str, Any]] = {}
    for entry in ledger:
        if int(entry.get("round", 0) or 0) == prev_round:
            prev_by_id[str(entry.get("id") or "")] = entry
    round_no = prev_round + 1
    take["issue_ledger_round"] = round_no
    current_ids = {item["id"] for item in current}
    entries: list[dict[str, Any]] = []
    for item in current:
        prev = prev_by_id.get(item["id"])
        if prev is None:
            outcome = "new"
        elif str(prev.get("prose_hash") or "") == content_hash:
            outcome = "repeat"
        else:
            outcome = "open"
        entries.append(
            {
                "id": item["id"],
                "class": item["class"],
                "prose_hash": content_hash,
                "outcome": outcome,
                "round": round_no,
            }
        )
    for pid, prev in prev_by_id.items():
        if not pid or pid in current_ids:
            continue
        if str(prev.get("outcome") or "") == "solved":
            continue
        entries.append(
            {
                "id": pid,
                "class": prev.get("class") or "",
                "prose_hash": content_hash,
                "outcome": "solved",
                "round": round_no,
            }
        )
    take["issue_ledger"] = ledger + entries
    return entries


def _rewrite_blocked_by_ledger(take: dict[str, Any]) -> str | None:
    """硬问题无进展时禁止再 REWRITE（不自动改 RETAKE）。

    两类无进展：
    1. 正文 hash 未变且问题重复（``repeat``）；
    2. 正文已改但仍是同一组硬问题 id（``open``）——换措辞不等于进展。
    """
    ledger = take.get("issue_ledger") or []
    if not ledger:
        return None
    last_round = max(int(e.get("round", 0) or 0) for e in ledger)
    open_hard = [
        e
        for e in ledger
        if int(e.get("round", 0) or 0) == last_round
        and str(e.get("outcome") or "") in _OPEN_LEDGER_OUTCOMES
    ]
    if not open_hard:
        return None
    if all(str(e.get("outcome") or "") == "repeat" for e in open_hard):
        ids = "；".join(str(e.get("id") or "") for e in open_hard[:8])
        return f"同类硬问题无进展，禁止再 REWRITE；请 RETAKE、请求裁决或重新规划。重复项：{ids}"
    if last_round < 2:
        return None
    prev_ids = {
        str(e.get("id") or "")
        for e in ledger
        if int(e.get("round", 0) or 0) == last_round - 1
        and str(e.get("outcome") or "") in _OPEN_LEDGER_OUTCOMES
        and str(e.get("id") or "")
    }
    cur_ids = {str(e.get("id") or "") for e in open_hard if str(e.get("id") or "")}
    if cur_ids and cur_ids == prev_ids:
        ids = "；".join(sorted(cur_ids)[:8])
        return (
            "硬问题集合无进展（正文已改但仍是同一组问题），禁止再 REWRITE；"
            "请 RETAKE、请求裁决或重新规划。"
            f"未消项：{ids}"
        )
    return None

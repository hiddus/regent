"""自审约束的程序复验与语义问题关闭（工作包4）。

程序只关闭带 text_must_include / text_must_exclude 的结构化约束。
语义问题保持 OPEN，直到下游给出可接地证据或明确撤回。
"""

from __future__ import annotations

import re
from typing import Any

# 事件引用的字段，不是分场正文。自审常把它们写进 text_must_exclude。
_ROLE_FIELD = re.compile(
    r'^"role"\s*:\s*"(?:reference|occurrence|consequence|new_round)"$'
)


def verify_structural_constraints(text: str, issues: list[Any] | None) -> list[str]:
    """复验自审给出的文字约束。通过才算这条结构性意见关闭。"""
    fails: list[str] = []
    body = str(text or "")
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("category") or "") != "structural":
            continue
        include = str(issue.get("text_must_include") or "").strip()
        exclude = str(issue.get("text_must_exclude") or "").strip()
        if include and include not in body:
            fails.append(f"constraint_missing:{include[:40]}")
        if exclude and exclude in body:
            fails.append(f"constraint_present:{exclude[:40]}")
    return fails


def remember_semantic_issues(
    open_issues: list[dict[str, Any]] | None,
    classified: list[Any] | None,
    *,
    origin: str,
) -> list[dict[str, Any]]:
    """把新的语义意见登记为 OPEN。已存在的描述不重复添加。"""
    out = [dict(item) for item in (open_issues or []) if isinstance(item, dict)]
    known = {str(item.get("description") or "").strip() for item in out}
    for issue in classified or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("category") or "") != "semantic":
            continue
        desc = str(issue.get("description") or "").strip()
        if not desc or desc in known:
            continue
        known.add(desc)
        out.append(
            {
                "description": desc,
                "origin": origin,
                "status": "OPEN",
                "involves_prior_chapter": bool(issue.get("involves_prior_chapter")),
            }
        )
    return out


def apply_semantic_resolutions(
    open_issues: list[dict[str, Any]] | None,
    resolutions: list[Any] | None,
    content: str,
) -> list[dict[str, Any]]:
    """按描述匹配关闭语义问题。

    closed 必须带当前正文里的逐字证据。withdrawn 记为撤回。
    描述出现在审校问题列表里不算关闭。
    """
    from regent.novel.domain.review_issues import quote_in_content

    by_desc: dict[str, dict[str, Any]] = {}
    for item in resolutions or []:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description") or "").strip()
        if desc:
            by_desc[desc] = item
    out: list[dict[str, Any]] = []
    for issue in open_issues or []:
        if not isinstance(issue, dict):
            continue
        row = dict(issue)
        if row.get("status") in {"RESOLVED", "WITHDRAWN"}:
            out.append(row)
            continue
        desc = str(row.get("description") or "").strip()
        res = by_desc.get(desc)
        if res is None:
            row["status"] = "OPEN"
            out.append(row)
            continue
        verdict = str(res.get("verdict") or "open")
        evidence = str(res.get("evidence") or "").strip()
        if verdict == "withdrawn":
            row["status"] = "WITHDRAWN"
            row["evidence"] = evidence
        elif verdict == "closed" and evidence and quote_in_content(evidence, content):
            row["status"] = "RESOLVED"
            row["evidence"] = evidence
        else:
            row["status"] = "OPEN"
        out.append(row)
    return out


def ground_structural_quotes(text: str, issues: list[Any] | None) -> list[dict[str, Any]]:
    """结构约束必须是当前成果里的逐字原文。对不上的改记为语义问题，避免逼计划写进一句新句子。"""
    body = str(text or "")
    out: list[dict[str, Any]] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        row = dict(issue)
        if str(row.get("category") or "") != "structural":
            out.append(row)
            continue
        include = str(row.get("text_must_include") or "").strip()
        exclude = str(row.get("text_must_exclude") or "").strip()
        if include and include not in body:
            include = ""
        if exclude and exclude not in body:
            exclude = ""
        row["text_must_include"] = include
        row["text_must_exclude"] = exclude
        if not include and not exclude:
            row["category"] = "semantic"
        out.append(row)
    return out


_WHOLE_PASSAGE_MIN = 24


def demote_sole_passage_deletes(
    text: str,
    issues: list[Any] | None,
    passages: list[str],
) -> list[dict[str, Any]]:
    """整段情节只出现一次、又被标成整段删除时，改记语义。

    拆场或改顺序不能靠删掉唯一一次发生。短句（如重复的动作短语）仍保持删除约束。
    """
    body = str(text or "")
    kept = [p for p in passages if str(p or "").strip()]
    out: list[dict[str, Any]] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        row = dict(issue)
        if str(row.get("category") or "") != "structural":
            out.append(row)
            continue
        exclude = str(row.get("text_must_exclude") or "").strip()
        include = str(row.get("text_must_include") or "").strip()
        covers_one = any(
            exclude in passage and len(exclude) >= 0.6 * len(passage)
            for passage in kept
        )
        if (
            len(exclude) >= _WHOLE_PASSAGE_MIN
            and body.count(exclude) == 1
            and covers_one
        ):
            row["text_must_exclude"] = ""
            if not include:
                row["category"] = "semantic"
        out.append(row)
    return out


def demote_role_field_excludes(issues: list[Any] | None) -> list[dict[str, Any]]:
    """`"role": "reference"` 这类字段碎片不是要删的句子。

    改记语义，留给分场改稿去改 event_refs。整份 JSON 里必然还有这些字段，
    拿它们做删除约束会永远 constraint_present。
    """
    out: list[dict[str, Any]] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        row = dict(issue)
        if str(row.get("category") or "") != "structural":
            out.append(row)
            continue
        exclude = str(row.get("text_must_exclude") or "").strip()
        include = str(row.get("text_must_include") or "").strip()
        if _ROLE_FIELD.fullmatch(exclude):
            row["text_must_exclude"] = ""
            if not include or _ROLE_FIELD.fullmatch(include):
                row["text_must_include"] = ""
                row["category"] = "semantic"
        out.append(row)
    return out


def strip_excluded_phrases(text: str, issues: list[Any] | None) -> str:
    """从改后的计划里删掉自审点名的短句。整段删除已改记语义，不会进到这里。"""
    body = str(text or "")
    includes = [
        str(issue.get("text_must_include") or "").strip()
        for issue in issues or []
        if isinstance(issue, dict) and str(issue.get("category") or "") == "structural"
    ]
    includes = [item for item in includes if item]
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("category") or "") != "structural":
            continue
        exclude = str(issue.get("text_must_exclude") or "").strip()
        if len(exclude) < 4:
            continue
        if any(exclude in include for include in includes):
            continue
        body = body.replace(exclude, "")
    return body


def explain_plan_constraint(code: str) -> str:
    """把程序约束码写成重分场能执行的句子。"""
    if code.startswith("constraint_present:"):
        quote = code.split(":", 1)[1]
        return f"分场原文不得再出现「{quote}」"
    if code.startswith("constraint_missing:"):
        quote = code.split(":", 1)[1]
        return f"分场原文必须保留「{quote}」"
    return code


def unclassified_opinions(review: dict[str, Any] | None) -> list[str]:
    """自审只写了自然语言、没有分类时，返回这些原文。已有 classified_issues 则不再追问。"""
    if not isinstance(review, dict) or review.get("classified_issues"):
        return []
    opinions = [str(item).strip() for item in (review.get("issues") or []) if str(item).strip()]
    note = str(review.get("revision_note") or "").strip()
    if note and note not in opinions:
        opinions.append(note)
    return opinions


def fill_unclassified_as_semantic(review: dict[str, Any] | None) -> dict[str, Any]:
    """分类自审仍没有结构化结果时，把原意见登记为语义问题，避免静默丢失。"""
    base = dict(review or {})
    opinions = unclassified_opinions(base)
    if not opinions:
        return base
    prior = bool(base.get("prior_chapter"))
    base["classified_issues"] = [
        {
            "category": "semantic",
            "description": text[:200],
            "involves_prior_chapter": prior,
            "text_must_include": "",
            "text_must_exclude": "",
        }
        for text in opinions
    ]
    return base


def should_trigger_self_review(
    scene_text: str,
    scene_card: dict[str, Any],
    entry_summary: str,
    working_state: dict[str, Any],
    ledger: dict[str, Any],
    repetition_warnings: list[str] | None = None,
    boundary_violations: list[str] | None = None,
    ledger_violations: list[str] | None = None,
) -> bool:
    """条件触发：只在程序检查报出越界、照抄或台账冲突时触发。"""
    del scene_text, scene_card, entry_summary, working_state, ledger
    return bool(repetition_warnings or boundary_violations or ledger_violations)


__all__ = [
    "apply_semantic_resolutions",
    "demote_role_field_excludes",
    "demote_sole_passage_deletes",
    "explain_plan_constraint",
    "fill_unclassified_as_semantic",
    "ground_structural_quotes",
    "remember_semantic_issues",
    "should_trigger_self_review",
    "strip_excluded_phrases",
    "unclassified_opinions",
    "verify_structural_constraints",
]

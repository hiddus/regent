"""Scene requirements and validation rules."""

from __future__ import annotations

from typing import Any

from regent.novel.application.directing_contracts import SceneValidation
from regent.novel.application.directing_evidence import _is_grounded
from regent.novel.domain.prose_patch import paragraphs_payload, split_paragraphs
from regent.novel.domain.scene_requirements import REQUIREMENTS_VERSION, freeze_scene_requirements

EVERYONE_KNOWS = "ALL"


def _catastrophic_prose_loss(
    old: str, new: str, paragraphs: list[dict[str, str]] | None = None
) -> str | None:
    """检测「局部稿被当成整场」式丢失（run21：3263→511）。

    字数骤减本身不是文学硬门；若旧段落开头大面积从新稿消失，则判定为协议失败。
    """
    if not old.strip() or not new.strip():
        return None
    if len(new) >= max(200, int(0.4 * len(old))):
        return None
    paras = paragraphs or paragraphs_payload(split_paragraphs(old))
    if not paras:
        return None
    kept = 0
    for item in paras:
        head = str(item.get("text") or "").strip()[:40]
        if head and (head in new or _is_grounded(head, new)):
            kept += 1
    if kept < max(1, (len(paras) + 1) // 2):
        return (
            f"修订后正文过短且丢失过半旧段落（{len(old)}→{len(new)} 字，"
            f"保留段首 {kept}/{len(paras)}）；拒绝覆盖。请用补丁只改声明的段落。"
        )
    return None


def _ensure_requirements(take: dict[str, Any]) -> list[dict[str, Any]]:
    """冻结或复用本场 requirement 清单（协议版本钉在 take 上）。

    一旦冻结不得因全局版本升级原地改写旧 take 的清单（R21-F3）。
    """
    if (
        isinstance(take.get("requirements"), list)
        and take["requirements"]
        and take.get("requirements_version")
    ):
        return take["requirements"]
    requirements = freeze_scene_requirements(take.get("events") or [])
    take["requirements"] = requirements
    take["requirements_version"] = REQUIREMENTS_VERSION
    return requirements


def _validation_report_defects(
    result: SceneValidation,
    draft: str,
    requirements: list[dict[str, Any]],
    *,
    protocol_version: str = "",
) -> list[str]:
    """报告无效（格式/覆盖/伪造引文），不是正文缺失。

    协议版本钉在 take 上：新协议不能因模型漏填 requirements 就静默退回旧格式；
    有需证据的状态要求时，双空报告一律无效（R21-F1）。
    """
    defects: list[str] = []
    expected = [item for item in requirements if item.get("require_direct_evidence")]
    expected_ids = {item["requirement_id"] for item in expected}
    expected_keys = {str(item.get("state_key") or "") for item in expected if item.get("state_key")}
    # 已冻结要求清单 ⇒ 按新协议验收；不得靠漏字段选择回退。
    enforce_coverage = bool(protocol_version == REQUIREMENTS_VERSION or requirements)

    for fact in result.facts:
        if fact.quote.strip() and not _is_grounded(fact.quote, draft):
            defects.append("事实 quote 不在当前正文中（核验器伪造）")

    if not result.requirements:
        for change in result.state_changes:
            if change.quote.strip() and not _is_grounded(change.quote, draft):
                defects.append("状态 quote 不在当前正文中（核验器伪造）")
        if enforce_coverage and expected:
            if not result.state_changes:
                defects.append("核验报告未覆盖状态要求：requirements 与 state_changes 均为空")
            else:
                covered = {change.key for change in result.state_changes}
                missing = sorted(k for k in expected_keys if k and k not in covered)
                if missing:
                    defects.append("旧格式核验未覆盖状态键：" + "、".join(missing[:20]))
        return defects

    seen: set[str] = set()
    known_ids = {item["requirement_id"] for item in requirements}
    for verdict in result.requirements:
        rid = verdict.requirement_id
        if rid in seen:
            defects.append(f"requirement_id 重复：{rid}")
        seen.add(rid)
        if rid not in known_ids:
            defects.append(f"未知 requirement_id：{rid}")
        if verdict.status in {"supported", "contradicted"}:
            if not verdict.quote.strip():
                defects.append(f"{rid} 状态为 {verdict.status} 但缺少 quote")
            elif not _is_grounded(verdict.quote, draft):
                defects.append(f"{rid} 的 quote 不在当前正文中（核验器伪造或串稿）")
        elif verdict.status == "missing" and verdict.quote.strip():
            if not _is_grounded(verdict.quote, draft):
                defects.append(f"{rid} missing 却引用了正文不存在的句子")
    missing_ids = expected_ids - seen
    if missing_ids:
        defects.append("核验报告未覆盖 requirement_id：" + "、".join(sorted(missing_ids)[:20]))
    return defects


def _substantive_validation_issues(
    result: SceneValidation,
    draft: str,
    requirements: list[dict[str, Any]],
    cast: dict[str, Any],
) -> list[str]:
    """正文层面的真实问题（在报告已通过格式检查之后）。"""
    # 模型常把「…符合要求」说明误写入 issues；那是 explanation，不是硬失败。
    issues = [
        item
        for item in result.issues
        if not any(t in str(item) for t in ("符合要求", "满足要求", "已满足要求"))
    ]
    for fact in result.facts:
        if any(name != EVERYONE_KNOWS and name not in cast for name in fact.known_by):
            issues.append("事实知情范围含未定义人物")
    by_id = {item["requirement_id"]: item for item in requirements}
    for verdict in result.requirements:
        meta = by_id.get(verdict.requirement_id)
        if meta is None:
            continue
        if not meta.get("require_direct_evidence"):
            continue
        if verdict.status == "missing":
            issues.append(f"状态要求缺失正文证据：{verdict.requirement_id}")
        elif verdict.status == "contradicted":
            issues.append(f"正文与结算状态矛盾：{verdict.requirement_id}")
        elif verdict.status == "supported" and not verdict.quote.strip():
            issues.append(f"状态要求缺少正文证据：{verdict.requirement_id}")
    # 旧格式（仅 state_changes）：报告层已强制覆盖；此处检查引文与实质缺失。
    if not result.requirements and result.state_changes:
        expected_keys = {
            item["state_key"]
            for item in requirements
            if item.get("require_direct_evidence") and item.get("state_key")
        }
        verified_keys = {change.key for change in result.state_changes}
        if expected_keys - verified_keys:
            issues.append("正文实际状态与场景结算不一致")
        if any(not _is_grounded(change.quote, draft) for change in result.state_changes):
            issues.append("状态变化缺少正文证据")
    return issues


def _working_state_from_requirements(
    requirements: list[dict[str, Any]],
    result: SceneValidation,
) -> dict[str, str]:
    """接受场次后写入 working_state：用结算最终值，不采核验器自由改写。"""
    supported = {v.requirement_id for v in result.requirements if v.status == "supported"}
    out: dict[str, str] = {}
    for item in requirements:
        if item["requirement_id"] in supported or not item.get("require_direct_evidence"):
            out[item["state_key"]] = item["expected_value"]
    if not result.requirements and result.state_changes:
        out.update({change.key: change.value for change in result.state_changes})
    return out

"""本作公约：由 Agent 根据原则透镜 + premise 推导，供导演与审校引用。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkConvention(BaseModel):
    """单条本作公约——必须具体到本故事，不能是引擎里的万用禁句。"""

    convention_id: str = Field(min_length=1, max_length=80)
    lens_id: str = Field(min_length=1, description="来源原则透镜")
    statement: str = Field(
        min_length=12,
        description="本作具体约定：机制名、发现顺序、身份字段分工等",
    )
    check_hint: str = Field(
        default="",
        description="审校时在正文里找什么（仍须针对本作，勿写死引擎梗）",
    )


class WorkConventionBundle(BaseModel):
    conventions: list[WorkConvention] = Field(min_length=1, max_length=12)
    rationale: str = Field(
        default="",
        description="为何这些约定覆盖了已激活透镜",
    )


def conventions_as_rails(bundle: WorkConventionBundle | dict[str, Any] | None) -> list[dict[str, Any]]:
    """写成与 commons_rails 兼容的结构；无确定性 check，只走 LLM 审校。"""
    if bundle is None:
        return []
    if isinstance(bundle, dict):
        items = list(bundle.get("conventions") or [])
    else:
        items = [c.model_dump(mode="json") for c in bundle.conventions]
    rails: list[dict[str, Any]] = []
    for item in items:
        cid = str(item.get("convention_id") or item.get("lens_id") or "").strip()
        statement = str(item.get("statement") or "").strip()
        if not cid or not statement:
            continue
        rails.append(
            {
                "pack_id": "work_conventions",
                "rule_id": cid,
                "kind": "work_convention",
                "statement": statement,
                "lens_id": str(item.get("lens_id") or ""),
                "check_hint": str(item.get("check_hint") or ""),
                "trigger_terms": [],
                # 本作公约引导审校与导演，不与器物硬门同等；过严会把「苏醒场清单」
                # 误套到后续场，或把风格偏好判成硬失败。
                "hard": False,
                "check": None,
            }
        )
    return rails


SYNTHESIZE_SYSTEM = (
    "你是小说世界观编辑。根据原则透镜与本作设定，推导『仅适用于本故事』的公约。"
    "每条公约必须点名本作自己的机制/身份/场景（例如本作外挂叫什么、原身叫什么），"
    "禁止输出引擎示例梗的复读（不要写死『裤管』『鱼塘断了就弄死』『外卖员林晚』这类他书句子，"
    "除非用户 premise 里真有这些元素——有则用本作名字重述逻辑）。"
    "覆盖每一条已激活透镜；statement 要让导演和审校能直接执行。"
    "若约定只适用于特定情境（如苏醒场、首次开面板），必须在 statement 里写明适用范围，"
    "避免被误读成每一场开篇都必须重复。"
    "双身份/系统面板：躯壳登记名与当前操控意识必须可映射到读者已认识的主角；"
    "禁止使用『操作员』『操作员A』『用户001』等与角色名无关的代号。"
    "系统首次出现须约定一句接入/绑定提示（谁绑定、叫什么、为何此刻弹出），禁止无声刷屏。"
    "宁少勿滥：只写执行必需的最小公约集；拿不准的放进 rationale，不要堆文风偏好。"
)

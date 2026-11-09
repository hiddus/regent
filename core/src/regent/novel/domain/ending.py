"""终局判定：这本小说讲完了没有（Plan §4 R2 / B-05）。

分寸（为什么要有这个模块）：

- **结束必须是被决定的事，不是扩不出来才做的事。** 原实现是「末节点完成 → 先
  扩卷 → 扩不出新节点才置 DONE」，于是正常故事永远不结束：终点不是「讲完了」，
  而是「生成失败」。验收要的是**正常生产入口自然 DONE**，不是把扩卷函数换成恒
  返回 None 之后也能 DONE。
- **用户认的终局优先于模型。** 用户说了「写三卷」，第三卷写完就是写完，不需要
  再问模型——问模型等于把用户的终局意图降级成一个建议。
- **模型只补位，且补不上就报 undecided。** provider 不可用或调用失败时既不
  完结也不扩卷：扩卷失败静默套用「变强／更强大的对手」模板，等于替用户改了
  创作方向，且改完还写在正文里，不可逆。
- undecided 不是终态：它带着可恢复上下文（卷号、章号、失败原因）落在运行上，
  下一次判定可以接着走，不留死局。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EndingChoice = Literal["complete", "expand", "undecided"]

COMPLETE: str = "complete"
EXPAND: str = "expand"
UNDECIDED: str = "undecided"

# 判定依据：结论必须能回答「凭什么说讲完了」
BASIS_USER_VOLUME = "user_target_volume"
BASIS_DIRECTOR = "director_verdict"
BASIS_NONE = "no_basis"


@dataclass(frozen=True)
class EndingIntent:
    """用户认可的终局。两项都空表示用户没说，只能交给导演补位。"""

    target_volume_count: int = 0
    ending_statement: str = ""

    @property
    def explicit(self) -> bool:
        return int(self.target_volume_count) > 0 or bool(self.ending_statement.strip())

    def as_payload(self) -> dict[str, object]:
        return {
            "target_volume_count": int(self.target_volume_count),
            "ending_statement": self.ending_statement,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object] | None) -> EndingIntent:
        data = payload or {}
        return cls(
            target_volume_count=int(data.get("target_volume_count") or 0),
            ending_statement=str(data.get("ending_statement") or ""),
        )


@dataclass(frozen=True)
class EndingDecision:
    """一次终局判定。``basis`` 说明结论的来源，缺依据时choice 只能是 undecided。"""

    choice: str = UNDECIDED
    reason: str = ""
    basis: str = BASIS_NONE

    @property
    def complete(self) -> bool:
        return self.choice == COMPLETE

    @property
    def expand(self) -> bool:
        return self.choice == EXPAND

    def as_payload(self) -> dict[str, object]:
        return {"choice": self.choice, "reason": self.reason, "basis": self.basis}

    @classmethod
    def from_payload(cls, payload: dict[str, object] | None) -> EndingDecision:
        data = payload or {}
        choice = str(data.get("choice") or UNDECIDED)
        if choice not in (COMPLETE, EXPAND, UNDECIDED):
            choice = UNDECIDED
        return cls(
            choice=choice,
            reason=str(data.get("reason") or ""),
            basis=str(data.get("basis") or BASIS_NONE),
        )


def decide_ending(
    *,
    intent: EndingIntent,
    volume_no: int,
    director_complete: bool | None = None,
    director_reason: str = "",
) -> EndingDecision:
    """按「用户终局 → 导演判定 → 无依据」的顺序决定结束、扩卷还是待定。

    ``director_complete`` 为 None 表示**导演没给出判定**（provider 不可用、
    调用失败、返回无法解析），不是「导演说没讲完」——这两种情况后果完全不同：
    前者是不知道，后者是知道没讲完。把它们都当成「继续扩卷」，等于把模型故障
    翻译成创作决策。
    """
    if int(intent.target_volume_count) > 0:
        if int(volume_no) >= int(intent.target_volume_count):
            return EndingDecision(
                choice=COMPLETE,
                reason=(
                    f"用户设定 {intent.target_volume_count} 卷，第 {volume_no} 卷"
                    "已完成：按用户认可的终局结束"
                ),
                basis=BASIS_USER_VOLUME,
            )
        return EndingDecision(
            choice=EXPAND,
            reason=(
                f"用户设定 {intent.target_volume_count} 卷，当前第 {volume_no} 卷：继续下一卷"
            ),
            basis=BASIS_USER_VOLUME,
        )
    if director_complete is None:
        return EndingDecision(
            choice=UNDECIDED,
            reason="导演没有给出终局判定：不得默认继续扩卷，也不得据默认置为完结",
            basis=BASIS_NONE,
        )
    if director_complete:
        return EndingDecision(
            choice=COMPLETE,
            reason=director_reason or "导演判定用户认可的终局已经达成",
            basis=BASIS_DIRECTOR,
        )
    return EndingDecision(
        choice=EXPAND,
        reason=director_reason or "导演判定终局尚未达成：继续下一卷",
        basis=BASIS_DIRECTOR,
    )

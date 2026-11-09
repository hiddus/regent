"""作品级质量终止：判「这本书已经没有读者要追」并停止续写。

为什么必须与「完结」分开：

- 完结是**故事讲完了**，读者还在，只是没有下一章；质量终止是**读者不追了**。
  两者落在同一个状态上，报表里就永远说不清「写完了几本」和「写废了几本」，
  验收也会把停掉的书算成完本（Plan R1）。
- 一个模型布尔值不足以证明读者没兴趣。章纲级的追读自评只能说「这几章可能不
  好看」，它看得是章纲不是正文；终止必须另有**正文依据**，而且证据要能被程序
  核对：引文真在那一章里，否则就是模型编的。
- 判断不可用（调用失败、预算耗尽）既不是「还有人追」也不是「没人追」。它只能
  记为待核验：既不停止，也不标完结，下一轮再判一次。

本模块只放纯函数：证据核对、结论落定、存档结构。取正文、发调用在
``application/quality_gate.py``。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# 存档状态
STATUS_STOPPED = "stopped"
STATUS_UNCONFIRMED = "unconfirmed"
STATUS_UNVERIFIED = "unverified"
STATUS_CLEAR = "clear"

# 判定依据
BASIS_PROSE_REVIEW = "prose_review"
BASIS_INSUFFICIENT_PROSE = "insufficient_prose"
BASIS_UNAVAILABLE = "unavailable"

# 一条引文至少这么长才算「指出具体位置」，更短的多半是「他」「钥匙」这类词。
MIN_QUOTE_CHARS = 12
# 至少几条被核对通过的正文证据才允许停止：一条足矣，但必须是核对通过的。
MIN_GROUNDED_EVIDENCE = 1


@dataclass(frozen=True)
class QualityQuote:
    """一条正文证据：哪一章、哪句原文、说明什么退化。"""

    chapter_no: int
    quote: str
    problem: str = ""
    prose_hash: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "chapter_no": int(self.chapter_no),
            "quote": self.quote,
            "problem": self.problem,
            "prose_hash": self.prose_hash,
        }


@dataclass(frozen=True)
class QualityStopDecision:
    """一次质量裁决的结论。``stop`` 为真才允许停止续写。"""

    status: str = STATUS_CLEAR
    stop: bool = False
    basis: str = BASIS_INSUFFICIENT_PROSE
    reason: str = ""
    uncertainty: str = ""
    evidence: tuple[QualityQuote, ...] = ()
    rejected: tuple[str, ...] = ()
    window: tuple[int, ...] = ()
    prose_hashes: dict[int, str] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop": bool(self.stop),
            "basis": self.basis,
            "reason": self.reason,
            "uncertainty": self.uncertainty,
            "evidence": [item.as_payload() for item in self.evidence],
            "rejected": list(self.rejected),
            "window": list(self.window),
            "prose_hashes": {str(k): v for k, v in self.prose_hashes.items()},
        }


def squash(text: str) -> str:
    """去掉全部空白再比对。

    分节、换行、缩进会因为排版不同而不一致，内容却完全一样；按字节比会把真引文
    当成伪造。这里只归版式，不归字词——改写过的话仍然对不上。
    """
    return "".join(str(text or "").split())


def ground_quotes(
    quotes: Iterable[QualityQuote], chapters: dict[int, str]
) -> tuple[list[QualityQuote], list[str]]:
    """只留下引文真在对应章节正文里的证据。

    ``chapters`` 是 章号 -> 正文。章号不在窗口里、引文太短、引文查不到，三种都
    记为拒收理由：它们说明模型在讲一个不是这几章里的事，不能据此停书。
    """
    grounded: list[QualityQuote] = []
    rejected: list[str] = []
    bodies = {int(no): squash(text) for no, text in chapters.items()}
    for item in quotes:
        quote = str(item.quote or "").strip()
        try:
            no = int(item.chapter_no)
        except (TypeError, ValueError):
            rejected.append(f"章号无法解析：{quote[:20]}")
            continue
        if no not in bodies:
            rejected.append(f"第{no}章不在复核窗口内：{quote[:20]}")
            continue
        if len(squash(quote)) < MIN_QUOTE_CHARS:
            rejected.append(f"引文过短无法核对：{quote[:20]}")
            continue
        if squash(quote) not in bodies[no]:
            rejected.append(f"引文不在第{no}章正文里：{quote[:20]}")
            continue
        grounded.append(
            QualityQuote(
                chapter_no=no,
                quote=quote,
                problem=str(item.problem or "").strip(),
                prose_hash=str(item.prose_hash or ""),
            )
        )
    return grounded, rejected


def decide_quality_stop(
    *,
    keeps_reading: bool | None,
    chapters: dict[int, str],
    prose_hashes: dict[int, str] | None = None,
    model_stop: bool | None = None,
    model_reason: str = "",
    model_uncertainty: str = "",
    quotes: Iterable[QualityQuote] = (),
    unavailable_reason: str = "",
) -> QualityStopDecision:
    """把「章纲自评 + 正文复核」合成一个结论。

    判定顺序：
    1. 章纲自评没说「不追」→ 不进入终止流程（clear）。
    2. 复核调用不可用 → 待核验（unverified）：不停止、不标完结。
    3. 模型说停，但没有任何一条引文能在正文里核对上 → 未证实（unconfirmed）。
    4. 模型说停且有核对通过的证据 → 停止。
    """
    window = tuple(sorted(int(no) for no in chapters))
    hashes = {int(k): str(v) for k, v in (prose_hashes or {}).items()}
    if keeps_reading is not False:
        return QualityStopDecision(
            status=STATUS_CLEAR,
            basis=BASIS_INSUFFICIENT_PROSE,
            reason="章纲追读自评没有判「不追」，不进入质量终止",
            window=window,
            prose_hashes=hashes,
        )
    if model_stop is None:
        return QualityStopDecision(
            status=STATUS_UNVERIFIED,
            basis=BASIS_UNAVAILABLE,
            reason=unavailable_reason or "正文复核不可用",
            window=window,
            prose_hashes=hashes,
        )
    grounded, rejected = ground_quotes(quotes, chapters)
    if not model_stop:
        return QualityStopDecision(
            status=STATUS_UNCONFIRMED,
            basis=BASIS_INSUFFICIENT_PROSE,
            reason=model_reason or "正文复核认为读者还在追",
            uncertainty=model_uncertainty,
            rejected=tuple(rejected),
            window=window,
            prose_hashes=hashes,
        )
    if len(grounded) < MIN_GROUNDED_EVIDENCE:
        return QualityStopDecision(
            status=STATUS_UNCONFIRMED,
            basis=BASIS_INSUFFICIENT_PROSE,
            reason=(
                "正文复核判「不追」但没有一条引文能在正文里核对上，"
                "按未证实处理，不停止续写"
            ),
            uncertainty=model_uncertainty,
            rejected=tuple(rejected),
            window=window,
            prose_hashes=hashes,
        )
    return QualityStopDecision(
        status=STATUS_STOPPED,
        stop=True,
        basis=BASIS_PROSE_REVIEW,
        reason=model_reason or "正文复核确认读者不再追读",
        uncertainty=model_uncertainty,
        evidence=tuple(grounded),
        rejected=tuple(rejected),
        window=window,
        prose_hashes=hashes,
    )


def quality_stop_record(work: Any) -> dict[str, Any]:
    """作品上存档的质量终止记录；没有返回空字典。"""
    bible = getattr(work, "story_bible", None)
    if not isinstance(bible, dict):
        return {}
    record = bible.get("quality_stop")
    return dict(record) if isinstance(record, dict) else {}


def work_quality_stopped(work: Any) -> bool:
    """作品是否已因质量终止停写。

    以状态为准，记录为辅：状态是队列、恢复、扩卷共同读的那一列；只写记录不改
    状态，等于留了一个可以被后台补偿重新拉起的口子。
    """
    from regent.novel.domain.states import StoryWorkState

    if str(getattr(work, "state", "") or "") == StoryWorkState.STOPPED_QUALITY.value:
        return True
    return str(quality_stop_record(work).get("status") or "") == STATUS_STOPPED

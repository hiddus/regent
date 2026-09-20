"""D-15：引文覆盖判据忽略空白差异。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

# 1. 新增 _normalize_ws 辅助（在 _QUOTE_ELLIPSIS 之后）
OLD_ELLIPSIS = '''# 省略号：模型引长句时几乎必然跳读，跳读不等于捏造。
_QUOTE_ELLIPSIS = re.compile(r"…|\\.{2,}|．{2,}")


def _covered(quote: str, text: str) -> bool:
    """逐字覆盖度判据（不含省略号拆分）。见 ``_quote_check`` 的说明。"""
    if not quote:
        return False
    if quote in text:
        return True
    run = _longest_common_run(quote, text)
    need = min(len(quote), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(quote))))
    return run >= need'''
NEW_ELLIPSIS = '''# 省略号：模型引长句时几乎必然跳读，跳读不等于捏造。
_QUOTE_ELLIPSIS = re.compile(r"…|\\.{2,}|．{2,}")
# 空白：引文把 draft 的段落换行压成句号是版式差异，不是内容差异；覆盖判据
# 不能因为一个换行把真实引文判成伪造。fabrication（凭空的词）依然会被拒。
_WS = re.compile(r"\\s+")
_FULLWIDTH_PUNCT = str.maketrans({"　": " ", "，": ",", "。": ".", "：": ":"})


def _normalize_ws(s: str) -> str:
    return _WS.sub("", s.translate(_FULLWIDTH_PUNCT))


def _covered(quote: str, text: str) -> bool:
    """逐字覆盖度判据（不含省略号拆分）。见 ``_quote_check`` 的说明。

    引文与正文都先做**空白归一**——段落换行、全角空格、全角标点后的空格都是
    版式差异，不是内容差异。
    """
    if not quote:
        return False
    q = _normalize_ws(quote)
    t = _normalize_ws(text)
    if q in t:
        return True
    run = _longest_common_run(q, t)
    need = min(len(q), max(QUOTE_MIN_RUN, int(QUOTE_MIN_RATIO * len(q))))
    return run >= need'''

EDITS = [
    ("normalize", OLD_ELLIPSIS, NEW_ELLIPSIS),
]


def main() -> int:
    src = TARGET.read_text(encoding="utf-8")
    orig = src
    for name, old, new in EDITS:
        count = src.count(old)
        assert count == 1, f"{name}: anchor hit {count} times, expected 1"
        src = src.replace(old, new, 1)
    assert src != orig
    compile(src, str(TARGET), "exec")
    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK] direction.py 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

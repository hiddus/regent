"""缺陷 15：引文覆盖判据忽略空白/段落差异。

真机死法（run18）：引文 ``然后滴答声骤然放大。煤烟味。`` 在字面比对上
与 draft 的 ``然后滴答声骤然放大。\\n\\n煤烟味。`` 不算连续子串——但内容完全
一致，只差一个段落换行。fabrication（凭空的词）依然会被拒；只压了一下
行/段落的真实引文才应被接受。
"""

from __future__ import annotations

import pytest

from regent.novel.application import direction as d

DRAFT = "先是……一段安静。\n\n然后滴答声骤然放大。\n\n煤烟味。浓烈苦涩的煤烟味猛然灌进他的鼻腔。"


@pytest.mark.parametrize(
    "quote, expected",
    [
        # 段落换行压缩成句号——版式差异，应通过
        ("然后滴答声骤然放大。煤烟味。", True),
        # 全角空格、半角空格、tab 混合——也应通过
        ("然后滴答声骤然放大。  煤烟味。", True),
        ("然后滴答声骤然放大。\t煤烟味。", True),
        # 真实虚构——与正文无关的词，应被拒
        ("他在月亮上喝咖啡。", False),
        # 内容大部分对了但关键动词改了——仍按覆盖度判
        ("然后滴答声骤然消失。煤烟味。", False),  # 放大→消失
    ],
)
def test_is_grounded_treats_whitespace_as_formatting(quote, expected):
    assert d._is_grounded(quote, DRAFT) is expected


def test_quote_check_does_not_fail_on_paragraph_compression():
    """_quote_check 是导演判断的入口；它必须接受段落压成句号的引文。"""
    # 第一次因为字段名错（``draft:`` 之类）——本次只看空白
    d._quote_check(["然后滴答声骤然放大。煤烟味。"], DRAFT)


def test_quote_check_still_rejects_fabrication_after_whitespace_normalization():
    """归一不能放过凭空的词。"""
    with pytest.raises(d.ProductionStopped):
        d._quote_check(["他在月亮上喝咖啡。", "然后滴答声骤然放大。煤烟味。"], DRAFT)

"""场景正文局部补丁：切段、校验、合并。纯函数，不读模型、不碰 IO。

局部修订不得把后半段自由文本当成整场覆盖——合并由代码按段落 id 完成，
未声明修改的段落字节保持不变（run21：3263→511 丢前文）。

一个 replacement 只允许连续段落范围；非连续修改必须拆成多个 replacement（R21-F2）。
有合法 offset 时按原稿切片拼接，保留段间原始分隔符。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


class PatchError(ValueError):
    """补丁无效：调用方不得覆盖旧稿。"""


_PARA_SPLIT = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Paragraph:
    paragraph_id: str
    text: str
    start: int
    end: int


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_paragraphs(text: str) -> list[Paragraph]:
    """按空行切段；段落 id 仅用于协议，不进入读者正文。

    单段文本（无空行）整篇为一段。保留段间分隔符于相邻段的边界，
    合并优先用原稿 offset 拼接以保持分隔符字节不变。
    """
    if not text:
        return []
    parts: list[Paragraph] = []
    chunks = _PARA_SPLIT.split(text)
    # re.split 丢分隔符；用逐段搜索还原起止，便于诊断与 offset 合并。
    search_from = 0
    for index, chunk in enumerate(chunks):
        if chunk == "" and index < len(chunks) - 1:
            continue
        at = text.find(chunk, search_from) if chunk else search_from
        if at < 0:
            at = search_from
        end = at + len(chunk)
        parts.append(
            Paragraph(
                paragraph_id=f"p{len(parts):04d}",
                text=chunk,
                start=at,
                end=end,
            )
        )
        search_from = end
    if not parts:
        parts.append(Paragraph("p0000", text, 0, len(text)))
    return parts


def paragraphs_payload(paragraphs: list[Paragraph]) -> list[dict[str, str]]:
    return [{"paragraph_id": p.paragraph_id, "text": p.text} for p in paragraphs]


def _indices_contiguous(indices: list[int]) -> bool:
    if not indices:
        return False
    ordered = sorted(indices)
    return ordered == list(range(ordered[0], ordered[-1] + 1))


def _has_usable_offsets(ordered: list[Paragraph], base_text: str) -> bool:
    if not ordered or not base_text:
        return False
    if any(p.end < p.start or p.end > len(base_text) for p in ordered):
        return False
    if any(base_text[p.start : p.end] != p.text for p in ordered):
        return False
    return True


def apply_patch(
    *,
    base_text: str,
    base_hash: str,
    paragraphs: list[Paragraph] | list[dict[str, str]],
    patch_hash: str,
    replacements: list[dict[str, object]],
    allowed_paragraph_ids: list[str] | frozenset[str] | None = None,
) -> str:
    """应用局部补丁，返回合并后的完整场景正文。

    ``replacements`` 每项：``paragraph_ids: list[str]``（必须连续）、``text: str``。
    同一 id 不得出现在多个 replacement 中；未知 id、越界 id、hash 不符一律拒绝。
    """
    if patch_hash != base_hash:
        raise PatchError(
            f"补丁 base_content_hash 过期：期望 {base_hash[:12]}…，收到 {patch_hash[:12]}…"
        )
    if content_hash(base_text) != base_hash:
        raise PatchError("原稿 hash 与正文不一致，拒绝合并")

    ordered: list[Paragraph]
    if paragraphs and isinstance(paragraphs[0], Paragraph):
        ordered = list(paragraphs)  # type: ignore[arg-type]
    else:
        # 无 offset 的字典表：若正文可切分且 id/text 对齐则重建 offset，否则回退 join。
        dict_rows = list(paragraphs)  # type: ignore[arg-type]
        rebuilt = split_paragraphs(base_text) if base_text else []
        if len(rebuilt) == len(dict_rows) and all(
            rebuilt[i].paragraph_id == str(dict_rows[i]["paragraph_id"])
            and rebuilt[i].text == str(dict_rows[i]["text"])
            for i in range(len(rebuilt))
        ):
            ordered = rebuilt
        else:
            ordered = [
                Paragraph(
                    paragraph_id=str(item["paragraph_id"]),
                    text=str(item["text"]),
                    start=0,
                    end=0,
                )
                for item in dict_rows
            ]
    if not ordered:
        raise PatchError("段落表为空，无法应用局部补丁")

    allowed: frozenset[str] | None = None
    if allowed_paragraph_ids is not None:
        allowed = frozenset(allowed_paragraph_ids)
        if not allowed:
            raise PatchError("允许修改的段落范围为空")

    by_id = {p.paragraph_id: i for i, p in enumerate(ordered)}
    claimed: dict[str, int] = {}
    # (start_index, end_index_inclusive, text)
    spans: list[tuple[int, int, str]] = []

    if not replacements:
        raise PatchError("补丁 replacements 为空")

    for r_index, item in enumerate(replacements):
        ids = item.get("paragraph_ids") or item.get("paragraph_id")
        if isinstance(ids, str):
            id_list = [ids]
        else:
            id_list = [str(x) for x in (ids or [])]
        text = str(item.get("text") or "")
        if not id_list:
            raise PatchError(f"replacement[{r_index}] 缺少 paragraph_ids")
        if not text.strip():
            raise PatchError(f"replacement[{r_index}] 替换文本为空")
        indices: list[int] = []
        for pid in id_list:
            if pid not in by_id:
                raise PatchError(f"未知段落 id：{pid}")
            if allowed is not None and pid not in allowed:
                raise PatchError(f"段落 id 超出可改范围：{pid}")
            if pid in claimed:
                raise PatchError(f"段落 id 重叠：{pid}")
            claimed[pid] = r_index
            indices.append(by_id[pid])
        if not _indices_contiguous(indices):
            raise PatchError(
                f"replacement[{r_index}] 段落 id 不连续；"
                "非连续修改请拆成多个 replacement"
            )
        start_i = min(indices)
        end_i = max(indices)
        spans.append((start_i, end_i, text))

    spans.sort(key=lambda item: item[0])
    for left, right in zip(spans, spans[1:]):
        if left[1] >= right[0]:
            raise PatchError("replacement 区间重叠")

    if _has_usable_offsets(ordered, base_text):
        return _merge_by_offsets(base_text, ordered, spans)
    return _merge_by_join(ordered, spans)


def _merge_by_offsets(
    base_text: str,
    ordered: list[Paragraph],
    spans: list[tuple[int, int, str]],
) -> str:
    """按原稿 offset 拼接：未修改区间（含分隔符）保持原字节。"""
    by_start = {start: (end, text) for start, end, text in spans}
    pieces: list[str] = []
    cursor = 0
    i = 0
    while i < len(ordered):
        if i in by_start:
            end_i, text = by_start[i]
            pieces.append(base_text[cursor : ordered[i].start])
            pieces.append(text)
            cursor = ordered[end_i].end
            i = end_i + 1
            continue
        pieces.append(base_text[cursor : ordered[i].end])
        cursor = ordered[i].end
        i += 1
    pieces.append(base_text[cursor:])
    return "".join(pieces)


def _merge_by_join(
    ordered: list[Paragraph],
    spans: list[tuple[int, int, str]],
) -> str:
    """无可靠 offset 时的回退：``\\n\\n`` 连接；仍禁止跳过间隙段。"""
    by_start = {start: (end, text) for start, end, text in spans}
    pieces: list[str] = []
    i = 0
    while i < len(ordered):
        if i in by_start:
            end_i, text = by_start[i]
            pieces.append(text)
            i = end_i + 1
            continue
        pieces.append(ordered[i].text)
        i += 1
    return "\n\n".join(pieces)

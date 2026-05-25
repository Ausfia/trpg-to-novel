"""Sliding-window candidate scanner — no LLM, pure text heuristics."""

from __future__ import annotations

import re
from typing import Sequence

from trpg2novel.chapterize.schema import CandidateCut

# Priority for dedup: higher number = preferred. 短章网文节奏下，段落边界
# 比 scene 结束更适合保留悬念和中段切章。
_TYPE_PRIORITY = {"paragraph_break": 3, "scene_end": 2, "word_count_pressure": 1}
_DEDUP_DISTANCE = 50  # chars — candidates closer than this are merged

_PARAGRAPH_RE = re.compile(r"\n\n")


def find_cut_candidates(
    text: str,
    cursor: int,
    scene_offsets: list[dict],
    *,
    target_words: int = 2000,
    tolerance: int = 300,
    hard_cap_max: int = 2600,
) -> list[CandidateCut]:
    """在文本窗口内扫描候选切点。

    Args:
        text: 骨架全文。
        cursor: 当前扫描起点（字符偏移）。
        scene_offsets: skeleton 中 scene 边界偏移列表。
        target_words: 目标章字数。
        tolerance: 允许偏差。
        hard_cap_max: 硬上限（窗口内无候选时在此强制切断）。

    Returns:
        候选切点列表，按偏移升序。
    """
    text_len = len(text)

    window_start = cursor + max(1, target_words - tolerance)
    window_end = min(cursor + target_words + tolerance, cursor + hard_cap_max, text_len)

    # 若光标已接近文末，直接返回末尾
    if window_start >= text_len:
        return [CandidateCut(offset=text_len, type="word_count_pressure")]

    candidates: list[CandidateCut] = []

    # ---- scene_end ----
    for so in scene_offsets:
        end = so.get("char_end", 0)
        if window_start <= end <= window_end:
            candidates.append(
                CandidateCut(offset=end, type="scene_end", scene_id=so.get("scene_id", ""))
            )

    # ---- paragraph_break ----
    for m in _PARAGRAPH_RE.finditer(text, window_start, window_end):
        candidates.append(CandidateCut(offset=m.start(), type="paragraph_break"))

    # ---- word_count_pressure fallback ----
    if not candidates:
        candidates.append(CandidateCut(offset=window_end, type="word_count_pressure"))

    # ---- dedup nearby candidates ----
    candidates = _dedup(candidates)

    # ---- attach nearby text context ----
    context_before = 200
    context_after = 300
    for c in candidates:
        start = max(0, c.offset - context_before)
        end = min(text_len, c.offset + context_after)
        c.nearby_text = text[start:end]

    return sorted(candidates, key=lambda c: c.offset)


def _dedup(candidates: list[CandidateCut]) -> list[CandidateCut]:
    """合并距离过近的候选，保留高优先级类型。"""
    if len(candidates) <= 1:
        return candidates

    sorted_c = sorted(candidates, key=lambda c: c.offset)
    result: list[CandidateCut] = []
    current = sorted_c[0]

    for nxt in sorted_c[1:]:
        if nxt.offset - current.offset < _DEDUP_DISTANCE:
            if _TYPE_PRIORITY.get(nxt.type, 0) > _TYPE_PRIORITY.get(current.type, 0):
                current = nxt
        else:
            result.append(current)
            current = nxt
    result.append(current)
    return result


def get_scene_ids_in_range(
    scene_offsets: Sequence[dict],
    char_start: int,
    char_end: int,
) -> list[str]:
    """返回落在字符区间内的 scene_id 列表。"""
    ids: list[str] = []
    for so in scene_offsets:
        so_start = so.get("char_start", 0)
        so_end = so.get("char_end", 0)
        # scene 与区间有重叠
        if so_start < char_end and so_end > char_start:
            ids.append(so.get("scene_id", ""))
    return ids

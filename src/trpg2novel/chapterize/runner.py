"""Chapterize runner — sliding window + LLM judge main loop."""

from __future__ import annotations

import sys
from typing import Sequence

from trpg2novel.chapterize.candidates import find_cut_candidates, get_scene_ids_in_range
from trpg2novel.chapterize.llm_judge import judge_best_cut
from trpg2novel.chapterize.schema import CandidateCut, ChapterCut, ChapterizeResult
from trpg2novel.outline.schema import VolumeOutline

_MIN_TAIL_CHARS = 600    # last chapter shorter than this → merge into previous


def chapterize_volume(
    skeleton_text: str,
    scene_offsets: list[dict],
    volume_outline: VolumeOutline,
    *,
    target_words: int = 2000,
    tolerance: int = 300,
    hard_cap_max: int = 2600,
    api_key: str,
    base_url: str,
    model: str,
    start_chapter_index: int = 1,
    progress_callback=None,
) -> ChapterizeResult:
    """把卷骨架文本切分为若干章。

    Args:
        skeleton_text: 骨架全文。
        scene_offsets: SCENE_BOUNDARY 偏移。
        volume_outline: 卷大纲（提供 emotion_arc 等叙事节奏参考）。
        target_words: 每章目标字数。
        tolerance: 允许偏差。
        hard_cap_max: 窗口内无候选时的硬上限。
        start_chapter_index: 章节起始编号。
        progress_callback: 可选 ``callback(idx, total_estimate)``。

    Returns:
        ChapterizeResult，含 cuts 列表与统计信息。
    """
    text_len = len(skeleton_text)

    # 如果全文太短直接成单章
    if text_len <= target_words + tolerance:
        cut = ChapterCut(
            chosen_offset=text_len,
            type="auto_single",
            reason="全文不足一章，自动成章",
            suggested_title=volume_outline.working_title,
            scene_ids_covered=[so.get("scene_id", "") for so in scene_offsets],
            char_range=(0, text_len),
            word_count=text_len,
        )
        return ChapterizeResult(
            volume_index=volume_outline.volume_index,
            cuts=[cut],
            skeleton_word_count=text_len,
            chapter_count=1,
            hard_cap_count=0,
        )

    cursor = 0
    cuts: list[ChapterCut] = []
    chapter_index = start_chapter_index
    total_estimate = max(1, text_len // target_words + 1)
    hard_cap_count = 0

    while cursor < text_len:
        remaining = text_len - cursor
        # 剩余文本很少 → 并入上一章
        if remaining < _MIN_TAIL_CHARS and cuts:
            cuts[-1].char_range = (cuts[-1].char_range[0], text_len)
            cuts[-1].chosen_offset = text_len
            cuts[-1].word_count = text_len - cuts[-1].char_range[0]
            cuts[-1].reason += "（并入尾部剩余文本）"
            break

        candidates = find_cut_candidates(
            text=skeleton_text,
            cursor=cursor,
            scene_offsets=scene_offsets,
            target_words=target_words,
            tolerance=tolerance,
            hard_cap_max=hard_cap_max,
        )

        if not candidates:
            # 兜底：在 hard_cap_max 处强制切
            hard_cut = min(cursor + hard_cap_max, text_len)
            candidates = [CandidateCut(offset=hard_cut, type="word_count_pressure")]

        # 如果只剩一个候选且是 word_count_pressure → 直接使用，不调 LLM。
        # 候选点正好是全文末尾时，这是正常收束，不应计入硬切。
        if len(candidates) == 1 and candidates[0].type == "word_count_pressure":
            is_terminal = candidates[0].offset >= text_len
            decision = {
                "chosen_offset": candidates[0].offset,
                "type": "chapter_end" if is_terminal else "word_count_pressure",
                "reason": "剩余内容收束为末章" if is_terminal else "窗口内无 scene_end/paragraph_break 候选，按目标字数切分",
                "suggested_title": "",
            }
        else:
            chapter_text = skeleton_text[cursor:candidates[-1].offset + 300]
            try:
                decision = judge_best_cut(
                    candidates,
                    chapter_text,
                    volume_outline,
                    chapter_index=chapter_index,
                    accumulated_words=cursor,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                )
            except Exception as exc:
                print(f"[WARN] LLM judge 失败，回退到第一个候选：{exc}", file=sys.stderr)
                decision = {
                    "chosen_offset": candidates[0].offset,
                    "type": candidates[0].type,
                    "reason": f"LLM 调用失败，回退到 {candidates[0].type}",
                    "suggested_title": "",
                }

        chosen = decision["chosen_offset"]
        # 防御：LLM 返回的偏移不合理时回退
        if chosen <= cursor or chosen > text_len:
            chosen = candidates[0].offset
            decision["type"] = candidates[0].type
            decision["reason"] = "LLM 返回偏移异常，回退到第一个候选"

        if decision["type"] == "word_count_pressure":
            hard_cap_count += 1

        char_start = cursor
        char_end = chosen
        scene_ids = get_scene_ids_in_range(scene_offsets, char_start, char_end)

        cuts.append(ChapterCut(
            chosen_offset=chosen,
            type=decision["type"],
            reason=decision["reason"],
            suggested_title=decision["suggested_title"],
            scene_ids_covered=scene_ids,
            char_range=(char_start, char_end),
            word_count=char_end - char_start,
        ))

        if progress_callback:
            progress_callback(chapter_index, total_estimate)

        cursor = chosen
        chapter_index += 1

    return ChapterizeResult(
        volume_index=volume_outline.volume_index,
        cuts=cuts,
        skeleton_word_count=text_len,
        chapter_count=len(cuts),
        hard_cap_count=hard_cap_count,
    )

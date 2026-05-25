"""LLM judge — send candidates with context to LLM, get best cut decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.chapterize.schema import CandidateCut
from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.outline.schema import VolumeOutline

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


def judge_best_cut(
    candidates: Sequence[CandidateCut],
    chapter_text: str,                    # skeleton slice from last cut to current window
    volume_outline: VolumeOutline,
    *,
    chapter_index: int,
    accumulated_words: int,
    api_key: str,
    base_url: str,
    model: str,
) -> dict:
    """让 LLM 从候选切点中选出最佳位置。

    Returns:
        {"chosen_offset": int, "type": str, "reason": str, "suggested_title": str}
    """
    system_prompt = _env.get_template("chapterize_system.j2").render()
    user_prompt = _env.get_template("chapterize_user.j2").render(
        chapter_index=chapter_index,
        accumulated_words=accumulated_words,
        chapter_text_preview=_truncate_tail(chapter_text, 3000),
        candidates=[
            {
                "offset": c.offset,
                "type": c.type,
                "scene_id": c.scene_id,
                "nearby_text": c.nearby_text,
            }
            for c in candidates
        ],
        emotion_arc=[
            {"position": e.position, "label": e.label, "intensity": e.intensity}
            for e in volume_outline.emotion_arc
        ],
        volume_title=volume_outline.working_title,
        ending_strategy=volume_outline.ending_strategy,
    )

    client = make_client(api_key, base_url)
    raw = chat_json(
        client,
        model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=800,
    )
    return {
        "chosen_offset": int(raw.get("chosen_offset", 0)),
        "type": raw.get("type", "word_count_pressure"),
        "reason": raw.get("reason", ""),
        "suggested_title": raw.get("suggested_title", ""),
    }


def _truncate_tail(text: str, max_chars: int) -> str:
    """截取文本末尾 max_chars 字符（切点判断更依赖尾部上下文）。"""
    if len(text) <= max_chars:
        return text
    return "…（上文已省略）\n" + text[-max_chars:]

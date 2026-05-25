# -*- coding: utf-8 -*-
"""[8] 章节文学化成稿（Polish 阶段）。

把结构正确但文学性不足的章节稿直接改写为小说正文。事实一致性仍交给后续
review 阶段。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from trpg2novel.config import StageLLMConfig
from trpg2novel.llm.client import chat, make_client
from trpg2novel.chapterize.anchors import anchors_to_prompt_text
from trpg2novel.outline.schema import VolumeOutline
from trpg2novel.style.profile import StyleProfile, profile_from_recipe, profile_to_prompt_dict
from trpg2novel.style.recipe import StyleRecipe
from trpg2novel.worldview import Worldview, load_worldview

if TYPE_CHECKING:
    from trpg2novel.rag.store import KnowledgeBase, RetrievedChunk

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_jenv = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


@dataclass(frozen=True)
class PolishModelSet:
    rewrite: StageLLMConfig
    check: StageLLMConfig | None = None

    @classmethod
    def single(cls, api_key: str, base_url: str, model: str) -> "PolishModelSet":
        cfg = StageLLMConfig(api_key=api_key, base_url=base_url, model=model)
        return cls(rewrite=cfg, check=cfg)


def _style_dict(style_profile: StyleProfile | None, style_recipe: StyleRecipe | None = None) -> dict[str, Any]:
    if style_profile is not None:
        return profile_to_prompt_dict(style_profile)
    if style_recipe is not None:
        return profile_from_recipe(style_recipe).to_prompt_dict()
    return {}


def _query_style_kb_direct(
    style_kb: "KnowledgeBase | None",
    *,
    chapter_title: str,
    revised_text: str,
    anchor_prompt: str = "",
    anchors: dict[str, Any] | None = None,
    volume_context: dict | None = None,
    top_k: int | None = None,
) -> list["RetrievedChunk"]:
    if style_kb is None:
        return []
    query = _build_style_kb_query(
        chapter_title=chapter_title,
        revised_text=revised_text,
        anchors=anchors,
        volume_context=volume_context,
        anchor_prompt=anchor_prompt,
    )
    try:
        return style_kb.query(query, top_k=top_k)
    except Exception:
        return []


def _build_style_kb_query(
    *,
    chapter_title: str,
    revised_text: str,
    anchors: dict[str, Any] | None = None,
    volume_context: dict | None = None,
    anchor_prompt: str = "",
) -> str:
    """Build a single style-search query from chapter-wide signals."""
    parts = [
        _section_text("章节信息", chapter_title),
        _section_text("卷内位置", _volume_context_for_style_query(volume_context)),
        _section_text("相关 beats", _nearest_beats_for_style_query(volume_context)),
        _section_text("素材锚点摘要", _anchors_for_style_query(anchors, anchor_prompt=anchor_prompt)),
        _section_text("正文窗口", _sample_text_windows(revised_text)),
        f"检索意图：{_style_query_intent(anchors=anchors, volume_context=volume_context)}",
    ]
    return "\n\n".join(part for part in parts if part.strip())


def _section_text(title: str, body: str) -> str:
    body = _clean_query_text(body)
    return f"{title}：\n{body}" if body else ""


def _clean_query_text(text: Any) -> str:
    return " ".join(str(text or "").split())


def _truncate_query_text(text: Any, max_chars: int) -> str:
    cleaned = _clean_query_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1] + "…"


def _sample_text_windows(text: str) -> str:
    """Sample beginning, middle, and ending windows from chapter text."""
    cleaned = _clean_query_text(text)
    if not cleaned:
        return ""
    if len(cleaned) <= 1200:
        return _truncate_query_text(cleaned, 1000)

    window = 350
    mid_start = max(0, (len(cleaned) // 2) - (window // 2))
    mid_end = mid_start + window
    samples = [
        f"开头：{cleaned[:window]}",
        f"中段：{cleaned[mid_start:mid_end]}",
        f"结尾：{cleaned[-window:]}",
    ]
    return "\n".join(samples)


def _anchors_for_style_query(
    anchors: dict[str, Any] | None,
    *,
    anchor_prompt: str = "",
) -> str:
    """Summarize style-relevant anchors only: emotions, choices, dialogues."""
    if not anchors:
        return _truncate_query_text(anchor_prompt, 500)

    labels = {
        "emotions": ("情绪/关系", 8),
        "choices": ("角色选择", 6),
        "dialogues": ("关键台词", 6),
    }
    lines: list[str] = []
    for key, (label, limit) in labels.items():
        items = anchors.get(key) or []
        if not items:
            continue
        lines.append(f"{label}：")
        for item in items[:limit]:
            if isinstance(item, dict):
                speaker = _clean_query_text(item.get("speaker", ""))
                text = _truncate_query_text(item.get("text", ""), 80)
                prefix = f"{speaker}: " if speaker else ""
                if text:
                    lines.append(f"- {prefix}{text}")
            else:
                text = _truncate_query_text(item, 80)
                if text:
                    lines.append(f"- {text}")
    return "\n".join(lines)


def _volume_context_for_style_query(volume_context: dict | None) -> str:
    if not volume_context:
        return ""
    fields = [
        volume_context.get("working_title", ""),
        volume_context.get("position_label", ""),
        volume_context.get("ending_strategy", ""),
    ]
    chapter_no = volume_context.get("chapter_in_volume")
    chapter_total = volume_context.get("total_chapters")
    if chapter_no and chapter_total:
        fields.append(f"第 {chapter_no}/{chapter_total} 章")
    return " ".join(_clean_query_text(field) for field in fields if _clean_query_text(field))


def _nearest_beats_for_style_query(volume_context: dict | None) -> str:
    if not volume_context:
        return ""
    beats = volume_context.get("nearest_beats") or []
    lines: list[str] = []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        beat_type = _clean_query_text(beat.get("type", ""))
        desc = _truncate_query_text(beat.get("description", ""), 120)
        chars = "、".join(str(x) for x in (beat.get("featured_characters") or []) if str(x).strip())
        bits = [x for x in (beat_type, desc, f"角色：{chars}" if chars else "") if x]
        if bits:
            lines.append("- " + " / ".join(bits))
    return "\n".join(lines)


def _chapter_role_from_position(position_label: str) -> str:
    label = _clean_query_text(position_label)
    if "开篇" in label:
        return "开篇"
    if "高潮" in label or "转折" in label:
        return "高潮/转折"
    if "结尾" in label:
        return "结尾"
    if "中段" in label:
        return "中段"
    if "单章" in label:
        return "中段"
    return ""


def _chapter_role_instruction(chapter_role: str) -> str:
    instructions = {
        "开篇": "先给正在发生的画面或人物动作，再交代设定；设定说明必须短，并尽量嵌入行动或对话。",
        "中段": "允许补充必要设定，但必须服务当前冲突、选择或转场。",
        "高潮/转折": "优先动作、压力、选择代价，压缩背景说明。",
        "结尾": "优先余韵、钩子、状态变化，不做大段新设定说明。",
    }
    return instructions.get(chapter_role, "")


def _first_featured_character(volume_context: dict | None) -> str:
    if not volume_context:
        return ""
    beats = volume_context.get("nearest_beats") or []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        for char in beat.get("featured_characters") or []:
            name = _clean_query_text(char)
            if name:
                return name
    return ""


def _speaker_from_anchor_item(item: Any) -> str:
    if isinstance(item, dict):
        return _clean_query_text(item.get("speaker", ""))
    return ""


def _suggest_pov_anchor(
    *,
    protagonist: str = "",
    volume_context: dict | None = None,
    anchors: dict[str, Any] | None = None,
) -> str:
    explicit = _clean_query_text(protagonist)
    if explicit:
        return explicit

    from_beats = _first_featured_character(volume_context)
    if from_beats:
        return from_beats

    if not anchors:
        return ""
    counts: dict[str, int] = {}
    for key in ("emotions", "choices", "dialogues"):
        for item in anchors.get(key) or []:
            speaker = _speaker_from_anchor_item(item)
            if speaker:
                counts[speaker] = counts.get(speaker, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def _style_query_intent(
    *,
    anchors: dict[str, Any] | None = None,
    volume_context: dict | None = None,
) -> str:
    terms = [
        "旁白距离",
        "段落节奏",
        "对话间隙",
        "转场方式",
        "反AI腔",
        "克制描写",
        "具体画面",
    ]
    anchors = anchors or {}
    if len(anchors.get("dialogues") or []) >= 2:
        terms.extend(["对白节奏", "潜台词", "角色声音"])
    if len(anchors.get("emotions") or []) >= 2:
        terms.extend(["情绪藏在动作里", "克制心理描写"])

    position_label = ""
    if volume_context:
        position_label = _clean_query_text(volume_context.get("position_label", ""))
        chapter_role = _clean_query_text(volume_context.get("chapter_role", ""))
        position_label = " ".join(x for x in (position_label, chapter_role) if x)
    if "高潮" in position_label or "转折" in position_label:
        terms.extend(["动作节奏", "危机压力", "短句", "战斗描写"])

    seen: set[str] = set()
    deduped = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            deduped.append(term)
    return " ".join(deduped)


def rewrite_direct(
    revised_text: str,
    worldview: Worldview,
    pc_facts: dict[str, list[str]] | None,
    last_chapter_summary: str,
    *,
    chapter_title: str,
    style_profile: StyleProfile | None = None,
    style_recipe: StyleRecipe | None = None,
    pov_mode: str = "",
    protagonist: str = "",
    worldview_retrieved: list["RetrievedChunk"] | None = None,
    style_retrieved: list["RetrievedChunk"] | None = None,
    model_cfg: StageLLMConfig,
    target_word_count: int = 0,
    anchor_prompt: str = "",
    volume_context: dict | None = None,
    suggested_pov: str = "",
) -> str:
    system_tmpl = _jenv.get_template("polish_system.j2")
    user_tmpl = _jenv.get_template("polish_user.j2")
    system_prompt = system_tmpl.render(
        worldview=worldview,
        pc_facts=pc_facts or {},
        last_chapter_summary=last_chapter_summary,
        style_recipe=_style_dict(style_profile, style_recipe),
        pov_mode=pov_mode,
        protagonist=protagonist,
        worldview_retrieved=worldview_retrieved or [],
        style_retrieved=style_retrieved or [],
        target_word_count=target_word_count,
        anchor_prompt=anchor_prompt,
        volume_context=volume_context or {},
        suggested_pov=suggested_pov,
    )
    user_prompt = user_tmpl.render(
        chapter_title=chapter_title,
        revised_text=revised_text,
        anchor_prompt=anchor_prompt,
        volume_context=volume_context or {},
        suggested_pov=suggested_pov,
    )
    client = make_client(model_cfg.api_key, model_cfg.base_url)
    return chat(client, model_cfg.model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=float(os.environ.get("LLM_POLISH_REWRITE_TEMPERATURE", "0.9")),
        max_tokens=int(os.environ.get("LLM_POLISH_REWRITE_MAX_TOKENS", "16000")))


def self_check_literary_scope(polished_text: str, *, model_cfg: StageLLMConfig | None) -> str:
    if model_cfg is None:
        return polished_text
    system_prompt = "你是小说成稿流程的轻量质检器。只检查输出是否包含解释性注释、Markdown围栏或明显非正文内容；不要做事实一致性审查。若文本可以直接作为正文发布，原样返回正文。"
    user_prompt = f"请清理以下文本中的非正文说明，只输出正文：\n\n{polished_text}"
    client = make_client(model_cfg.api_key, model_cfg.base_url)
    return chat(client, model_cfg.model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], temperature=0.1, max_tokens=int(os.environ.get("LLM_POLISH_CHECK_MAX_TOKENS", "16000")))


def polish_chapter(
    revised_text: str,
    worldview: Worldview | None = None,
    pc_facts: dict[str, list[str]] | None = None,
    last_chapter_summary: str = "",
    *,
    api_key: str,
    base_url: str,
    model: str,
    kb: "KnowledgeBase | None" = None,
    chapter_title: str = "",
    style_profile: StyleProfile | None = None,
    style_recipe: StyleRecipe | None = None,
    style_kb: "KnowledgeBase | None" = None,
    pov_mode: str = "",
    protagonist: str = "",
    model_set: PolishModelSet | None = None,
    run_self_check: bool = False,
    volume_outline: "VolumeOutline | None" = None,
    target_word_count: int = 0,
    chapter_in_volume: int = 0,
    total_chapters: int = 0,
    anchors: dict[str, Any] | None = None,
) -> str:
    """对修订稿进行文学化成稿，返回 polished 正文。"""
    import sys
    if worldview is None:
        worldview = load_worldview("dnd5e")
    if model_set is None:
        model_set = PolishModelSet.single(api_key, base_url, model)
    if style_profile is None and style_recipe is not None:
        style_profile = profile_from_recipe(style_recipe)

    anchor_prompt = anchors_to_prompt_text(anchors or {}) if anchors else ""

    # 组装卷上下文（若提供）
    volume_context: dict | None = None
    if volume_outline is not None:
        position_label = _position_label(chapter_in_volume, total_chapters)
        chapter_role = _chapter_role_from_position(position_label)
        nearest_beats = _find_nearest_beats(
            volume_outline, chapter_in_volume, total_chapters
        ) if total_chapters > 0 else []
        volume_context = {
            "working_title": volume_outline.working_title,
            "chapter_in_volume": chapter_in_volume,
            "total_chapters": total_chapters,
            "position_label": position_label,
            "chapter_role": chapter_role,
            "chapter_role_instruction": _chapter_role_instruction(chapter_role),
            "ending_strategy": volume_outline.ending_strategy,
            "nearest_beats": nearest_beats,
        }
        print(f"✓ 卷上下文：vol{volume_outline.volume_index:02d} 第 {chapter_in_volume}/{total_chapters} 章 ({position_label})", file=sys.stderr, flush=True)
    suggested_pov = _suggest_pov_anchor(
        protagonist=protagonist,
        volume_context=volume_context,
        anchors=anchors,
    )
    if suggested_pov:
        print(f"✓ 建议主视角/主情绪锚点：{suggested_pov}", file=sys.stderr, flush=True)
    if anchor_prompt:
        print("✓ 已加载素材锚点，润色将优先保留玩家行为与台词", file=sys.stderr, flush=True)
    else:
        print("⚠ 未找到素材锚点，将仅依据章节草稿润色", file=sys.stderr, flush=True)

    # 阶段 1：世界观 KB 检索
    print("📥 [1/3] 检索世界观知识库...", file=sys.stderr, flush=True)
    worldview_retrieved: list["RetrievedChunk"] = []
    if kb is not None:
        query = " ".join(filter(None, [chapter_title, revised_text[:300]]))
        try:
            worldview_retrieved = kb.query(query)
            print(f"   ✓ 检索到 {len(worldview_retrieved)} 条世界观参考", file=sys.stderr, flush=True)
        except Exception:
            worldview_retrieved = []
            print("   ⚠ 世界观检索失败，跳过", file=sys.stderr, flush=True)
    else:
        print("   - 未配置世界观知识库", file=sys.stderr, flush=True)

    # 阶段 2：检索风格 KB
    print("📚 [2/3] 检索风格知识库...", file=sys.stderr, flush=True)
    style_top_k = style_profile.style_kb_top_k if style_profile is not None else None
    style_retrieved = _query_style_kb_direct(
        style_kb,
        chapter_title=chapter_title,
        revised_text=revised_text,
        anchor_prompt=anchor_prompt,
        anchors=anchors,
        volume_context=volume_context,
        top_k=style_top_k,
    )
    if style_retrieved:
        print(f"   ✓ 检索到 {len(style_retrieved)} 条风格参考", file=sys.stderr, flush=True)
    else:
        print("   - 未使用风格知识库", file=sys.stderr, flush=True)

    # 阶段 3：文学化改写
    print(f"✍️ [3/3] 直接文学化成稿（模型：{model_set.rewrite.model}）...", file=sys.stderr, flush=True)
    polished = rewrite_direct(
        revised_text,
        worldview,
        pc_facts,
        last_chapter_summary,
        chapter_title=chapter_title,
        style_profile=style_profile,
        style_recipe=style_recipe,
        pov_mode=pov_mode,
        protagonist=protagonist,
        worldview_retrieved=worldview_retrieved,
        style_retrieved=style_retrieved,
        model_cfg=model_set.rewrite,
        target_word_count=target_word_count,
        anchor_prompt=anchor_prompt,
        volume_context=volume_context,
        suggested_pov=suggested_pov,
    )
    print(f"   ✓ 改写完成：{len(polished)} 字", file=sys.stderr, flush=True)

    # 可选：自检
    if run_self_check:
        check_model = model_set.check.model if model_set.check else "N/A"
        print(f"🔍 [额外] 轻量自检（模型：{check_model}）...", file=sys.stderr, flush=True)
        polished = self_check_literary_scope(polished, model_cfg=model_set.check)
        print("   ✓ 自检完成", file=sys.stderr, flush=True)

    return polished


def _position_label(chapter_in_volume: int, total_chapters: int) -> str:
    """返回章节在卷中的位置标签。"""
    if total_chapters <= 1:
        return "单章"
    ratio = chapter_in_volume / total_chapters
    if ratio <= 0.25:
        return "开篇"
    elif ratio <= 0.6:
        return "中段"
    elif ratio <= 0.85:
        return "高潮/转折"
    else:
        return "结尾"


def _find_nearest_beats(
    volume_outline: VolumeOutline,
    chapter_in_volume: int,
    total_chapters: int,
) -> list[dict]:
    """找到与本章位置最接近的 key_beats。"""
    if total_chapters <= 0 or not volume_outline.key_beats:
        return []
    chapter_pos = chapter_in_volume / total_chapters
    beats = volume_outline.key_beats
    total_beats = len(beats)
    center_idx = int(chapter_pos * (total_beats - 1))
    start = max(0, center_idx - 1)
    end = min(total_beats, center_idx + 2)
    return [
        {
            "type": b.type,
            "description": b.description,
            "featured_characters": b.featured_characters,
        }
        for b in beats[start:end]
    ]

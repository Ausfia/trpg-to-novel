from __future__ import annotations

from trpg2novel.narrate.polish import (
    _anchors_for_style_query,
    _build_style_kb_query,
    _chapter_role_from_position,
    _sample_text_windows,
    _suggest_pov_anchor,
)


def test_sample_text_windows_uses_beginning_middle_and_ending() -> None:
    text = "开头旅途" + "甲" * 700 + "中段战斗" + "乙" * 700 + "结尾决断"

    sampled = _sample_text_windows(text)

    assert "开头：开头旅途" in sampled
    assert "中段：" in sampled
    assert "中段战斗" in sampled
    assert "结尾：" in sampled
    assert "结尾决断" in sampled


def test_anchors_for_style_query_keeps_only_emotions_choices_dialogues() -> None:
    anchors = {
        "actions": [{"speaker": "雷恩", "text": "冲向城门"}],
        "emotions": [{"speaker": "诺菲雅", "text": "看着平民逃散，微微皱眉"}],
        "choices": [{"speaker": "比阿特丽丝", "text": "决定前往镇子救人"}],
        "dialogues": [{"speaker": "丹德莱", "text": "「就几条？」"}],
        "discarded_noise": [{"speaker": "骰娘", "text": "1d20=7"}],
    }

    query = _anchors_for_style_query(anchors)

    assert "诺菲雅: 看着平民逃散" in query
    assert "比阿特丽丝: 决定前往镇子救人" in query
    assert "丹德莱: 「就几条？」" in query
    assert "冲向城门" not in query
    assert "1d20" not in query


def test_build_style_kb_query_includes_volume_context_and_nearest_beats() -> None:
    volume_context = {
        "working_title": "龙影初临",
        "chapter_in_volume": 1,
        "total_chapters": 8,
        "position_label": "开篇",
        "ending_strategy": "cliffhanger",
        "nearest_beats": [
            {
                "type": "opening",
                "description": "至绿镇被蓝龙袭击，队伍决定入城救人",
                "featured_characters": ["丹德莱", "雷恩"],
            }
        ],
    }

    query = _build_style_kb_query(
        chapter_title="刃向龙影",
        revised_text="开头" + "甲" * 700 + "中段危机" + "乙" * 700 + "结尾奔赴火场",
        anchors={"emotions": [], "choices": [], "dialogues": []},
        volume_context=volume_context,
    )

    assert "章节信息" in query
    assert "刃向龙影" in query
    assert "龙影初临" in query
    assert "第 1/8 章" in query
    assert "opening" in query
    assert "至绿镇被蓝龙袭击" in query
    assert "丹德莱、雷恩" in query
    assert "中段危机" in query
    assert "结尾奔赴火场" in query
    assert "旁白距离 段落节奏" in query


def test_build_style_kb_query_falls_back_without_anchors_or_volume_context() -> None:
    query = _build_style_kb_query(
        chapter_title="无锚点章节",
        revised_text="短章正文",
        anchors=None,
        volume_context=None,
    )

    assert "无锚点章节" in query
    assert "短章正文" in query
    assert "检索意图" in query


def test_chapter_role_from_position_label() -> None:
    assert _chapter_role_from_position("开篇") == "开篇"
    assert _chapter_role_from_position("中段") == "中段"
    assert _chapter_role_from_position("高潮/转折") == "高潮/转折"
    assert _chapter_role_from_position("结尾") == "结尾"
    assert _chapter_role_from_position("单章") == "中段"


def test_suggest_pov_anchor_priority_uses_protagonist_first() -> None:
    volume_context = {
        "nearest_beats": [
            {"featured_characters": ["丹德莱"]},
        ],
    }
    anchors = {
        "dialogues": [{"speaker": "雷恩", "text": "我来开路"}],
        "emotions": [{"speaker": "雷恩", "text": "压住恐惧"}],
    }

    assert _suggest_pov_anchor(
        protagonist="比阿特丽丝",
        volume_context=volume_context,
        anchors=anchors,
    ) == "比阿特丽丝"


def test_suggest_pov_anchor_uses_beats_before_anchor_counts() -> None:
    volume_context = {
        "nearest_beats": [
            {"featured_characters": ["丹德莱", "雷恩"]},
        ],
    }
    anchors = {
        "dialogues": [{"speaker": "雷恩", "text": "我来开路"}],
        "emotions": [{"speaker": "雷恩", "text": "压住恐惧"}],
    }

    assert _suggest_pov_anchor(
        protagonist="",
        volume_context=volume_context,
        anchors=anchors,
    ) == "丹德莱"


def test_suggest_pov_anchor_falls_back_to_anchor_speaker_counts() -> None:
    anchors = {
        "emotions": [{"speaker": "诺菲雅", "text": "看着火光沉默"}],
        "choices": [{"speaker": "雷恩", "text": "决定冲进去"}],
        "dialogues": [
            {"speaker": "雷恩", "text": "跟上。"},
            {"speaker": "丹德莱", "text": "就几条？"},
        ],
    }

    assert _suggest_pov_anchor(
        protagonist="",
        volume_context=None,
        anchors=anchors,
    ) == "雷恩"


def test_climax_style_query_adds_battle_intent_without_action_anchor_text() -> None:
    anchors = {
        "actions": [{"speaker": "雷恩", "text": "冲向蓝龙的右翼"}],
        "emotions": [
            {"speaker": "诺菲雅", "text": "意识到镇民被困"},
            {"speaker": "丹德莱", "text": "把恐惧压进玩笑"},
        ],
        "dialogues": [
            {"speaker": "丹德莱", "text": "「就几条？」"},
            {"speaker": "雷恩", "text": "「先救人。」"},
        ],
    }
    volume_context = {
        "position_label": "高潮/转折",
        "chapter_role": "高潮/转折",
        "nearest_beats": [],
    }

    query = _build_style_kb_query(
        chapter_title="龙翼下的火",
        revised_text="火焰逼近，队伍冲进街道。",
        anchors=anchors,
        volume_context=volume_context,
    )

    assert "动作节奏" in query
    assert "危机压力" in query
    assert "战斗描写" in query
    assert "对白节奏" in query
    assert "情绪藏在动作里" in query
    assert "冲向蓝龙的右翼" not in query

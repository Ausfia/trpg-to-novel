"""测试 [2] Classify：三标记切段、骰命令配对、战斗模板识别、无标记 warning。"""

from __future__ import annotations

from trpg2novel.parse.classify import (
    SessionConfig,
    classify_events,
)
from trpg2novel.parse.md_loader import Event


def _ev(eid: str, ts: str, speaker: str, body: str, flags=None) -> Event:
    return Event(id=eid, timestamp=ts, speaker=speaker, body=body, flags=flags or {})


CFG_S01 = SessionConfig(
    session_id="s01",
    dm_handle="狗dm",
    bot_handles=["二阶堂希罗（请看标签❗"],
    player_handles=["雷恩", "丹德莱", "比阿特丽丝", "艾尔莉洁", "诺菲雅", "泰洛尔"],
)


def kinds(segments):
    return [s.kind for s in segments]


def test_pc_pure_dialogue():
    e = _ev("s01-0001", "20:30:00", "丹德莱", '"哦哇，我还是第一次挤马车呢。"')
    [t] = classify_events([e], CFG_S01)
    assert t.source == "pc"
    assert kinds(t.segments) == ["pc_dialogue"]
    assert t.segments[0].text == "哦哇，我还是第一次挤马车呢。"


def test_pc_pure_action_no_separator():
    e = _ev("s01-0002", "20:30:00", "雷恩", "#在看到不正常的浓烟时就绷直了身体")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["pc_action"]
    assert t.segments[0].text == "在看到不正常的浓烟时就绷直了身体"


def test_pc_action_then_dialogue_inline():
    e = _ev(
        "s01-0003",
        "20:30:00",
        "比阿特丽丝",
        '#像是下定决心似的，要往巨龙的方向去"我觉得我们不能见死不救"',
    )
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["pc_action", "pc_dialogue"]
    assert t.segments[0].text == "像是下定决心似的，要往巨龙的方向去"
    assert t.segments[1].text == "我觉得我们不能见死不救"


def test_pc_dialogue_then_action_inline():
    e = _ev(
        "s01-0004",
        "20:30:00",
        "雷恩",
        '"我们的终点在镇里吗"#扭头问向雇主，发出闷闷的沉声',
    )
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["pc_dialogue", "pc_action"]


def test_pc_ooc_fullwidth():
    e = _ev("s01-0005", "20:30:00", "诺菲雅", "（这下真的麻烦了）")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["pc_ooc"]
    assert t.segments[0].text == "这下真的麻烦了"


def test_pc_ooc_halfwidth():
    e = _ev("s01-0006", "20:30:00", "诺菲雅", "(笑出声)")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["pc_ooc"]


def test_pc_unmarked_warning():
    e = _ev("s01-0007", "20:30:00", "雷恩", "什么标记都没有的裸文本")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["unmarked_warning"]


def test_pc_roll_cmd_halfwidth():
    e = _ev("s01-0008", "20:30:00", "丹德莱", ".ri")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["roll_cmd"]


def test_pc_roll_cmd_fullwidth_with_modifier():
    e = _ev("s01-0009", "20:30:00", "雷恩", "。ri+2")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["roll_cmd"]


def test_pc_init_end_treated_as_roll_cmd():
    e = _ev("s01-0010", "20:30:00", "泰洛尔", ".init end")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["roll_cmd"]


def test_dm_narration_keeps_paragraphs():
    e = _ev(
        "s01-0011",
        "20:30:00",
        "狗dm",
        "至绿镇，这个坐落于剑湾南部的小镇。\n\n下一段叙述。",
    )
    [t] = classify_events([e], CFG_S01)
    assert t.source == "dm"
    assert kinds(t.segments) == ["dm_narration"]
    assert "至绿镇" in t.segments[0].text
    assert "下一段叙述" in t.segments[0].text


def test_dm_roll_for_npc():
    e = _ev("s01-0012", "20:30:00", "狗dm", "。ri+1 利南·斯威夫特")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["roll_cmd"]


def test_bot_roll_result():
    e = _ev(
        "s01-0013",
        "20:30:00",
        "二阶堂希罗（请看标签❗",
        "<雷恩>对先攻点数设置如下:\n\n1. 雷恩: 3+2=5",
    )
    [t] = classify_events([e], CFG_S01)
    assert t.source == "bot"
    assert kinds(t.segments) == ["roll_result"]
    assert t.segments[0].extra["subject"] == "雷恩"


def test_bot_turn_marker():
    e = _ev(
        "s01-0014",
        "20:30:00",
        "二阶堂希罗（请看标签❗",
        "【艾尔莉洁】戏份结束了。\n\n下面该【泰洛尔】@泰洛尔 hp28/28ac17dc13出场了！",
    )
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["turn_marker"]


def test_bot_initiative_clear():
    e = _ev("s01-0015", "20:30:00", "二阶堂希罗（请看标签❗", "先攻列表已清除")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["initiative_clear"]


def test_bot_record_meta_flag():
    e = _ev(
        "s01-0000",
        "20:30:00",
        "二阶堂希罗（请看标签❗",
        "魔女审判，现在开庭。\n\n记录已经开启",
        flags={"is_record_meta": True},
    )
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["record_meta"]


def test_roll_pair_pc_to_bot():
    cmd = _ev("s01-0100", "20:30:00", "雷恩", ".ri+2")
    res = _ev(
        "s01-0101",
        "20:30:01",
        "二阶堂希罗（请看标签❗",
        "<雷恩>对先攻点数设置如下:\n\n1. 雷恩: 3+2=5",
    )
    [tc, tr] = classify_events([cmd, res], CFG_S01)
    cmd_seg = tc.segments[0]
    res_seg = tr.segments[0]
    assert cmd_seg.extra.get("paired_result_event") == "s01-0101"
    assert res_seg.extra.get("paired_cmd_event") == "s01-0100"


def test_roll_pair_dm_npc():
    cmd = _ev("s01-0102", "20:30:00", "狗dm", "。ri+1 利南·斯威夫特")
    res = _ev(
        "s01-0103",
        "20:30:01",
        "二阶堂希罗（请看标签❗",
        "<狗dm>对先攻点数设置如下:\n\n1. 利南·斯威夫特: 6+1=7",
    )
    [tc, tr] = classify_events([cmd, res], CFG_S01)
    assert tc.segments[0].extra.get("paired_result_event") == "s01-0103"


def test_image_placeholder_line():
    e = _ev("s01-0200", "20:30:00", "狗dm", "[1]")
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["image"]


def test_pc_image_dump_lines():
    e = _ev(
        "s01-0201",
        "20:30:00",
        "泰洛尔",
        "[图片: 6B9E6C9CA2142EFDC7813CD75C7DD652.png]\n\n资源: 1 个文件",
    )
    [t] = classify_events([e], CFG_S01)
    assert kinds(t.segments) == ["image", "image_meta"]


def test_unknown_speaker():
    e = _ev("s01-0300", "20:30:00", "陌生人", "随便说点啥")
    [t] = classify_events([e], CFG_S01)
    assert t.source == "unknown"

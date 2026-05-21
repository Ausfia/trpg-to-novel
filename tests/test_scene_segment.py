"""测试 [3] Scene Segment：战斗边界、时间间隙、DM 转折词。"""

from __future__ import annotations

from trpg2novel.parse.classify import Segment, SessionConfig, TaggedEvent, classify_events
from trpg2novel.parse.md_loader import Event
from trpg2novel.segment import segment_scenes


def _ev(eid: str, ts: str, speaker: str, body: str, flags=None) -> Event:
    return Event(id=eid, timestamp=ts, speaker=speaker, body=body, flags=flags or {})


CFG = SessionConfig(
    session_id="s01",
    dm_handle="狗dm",
    bot_handles=["骰娘"],
    player_handles=["雷恩", "丹德莱"],
)


def _tag(ev: Event) -> TaggedEvent:
    return classify_events([ev], CFG)[0]


def test_single_scene_no_boundary():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "狗dm", "至绿镇，剑湾南部的小镇。")),
        _tag(_ev("s01-0002", "20:30:10", "雷恩", "#拔出长刀")),
    ]
    scenes = segment_scenes(events, "s01")
    assert len(scenes) == 1
    assert scenes[0].kind == "narration"
    assert len(scenes[0].event_ids) == 2


def test_battle_start_on_initiative_list():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "狗dm", "战斗开始了。")),
        _tag(_ev("s01-0002", "20:30:05", "骰娘", "当前先攻列表为:\n1. 雷恩: 15")),
        _tag(_ev("s01-0003", "20:30:10", "雷恩", "#攻击")),
    ]
    scenes = segment_scenes(events, "s01")
    assert len(scenes) == 2
    assert scenes[0].kind == "narration"
    assert scenes[0].event_ids == ["s01-0001"]
    assert scenes[1].kind == "battle"
    assert "battle_start:initiative_list" in scenes[1].triggers


def test_battle_end_on_initiative_clear():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "骰娘", "当前先攻列表为:\n1. 雷恩: 15")),
        _tag(_ev("s01-0002", "20:30:10", "雷恩", "#攻击")),
        _tag(_ev("s01-0003", "20:30:30", "骰娘", "先攻列表已清除")),
        _tag(_ev("s01-0004", "20:30:40", "狗dm", "战斗结束，镇民欢呼。")),
    ]
    scenes = segment_scenes(events, "s01")
    assert len(scenes) == 2
    assert scenes[0].kind == "battle"
    # initiative_clear event belongs to battle scene
    assert "s01-0003" in scenes[0].event_ids
    assert scenes[1].kind == "narration"
    assert "battle_end:initiative_clear" in scenes[1].triggers
    assert "s01-0004" in scenes[1].event_ids


def test_time_gap_splits_scene():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "狗dm", "开场。")),
        _tag(_ev("s01-0002", "20:36:00", "狗dm", "六分钟后。")),  # gap = 360s > 300s
    ]
    scenes = segment_scenes(events, "s01", gap_threshold_seconds=300)
    assert len(scenes) == 2
    assert "time_gap:360s" in scenes[1].triggers


def test_time_gap_below_threshold_no_split():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "狗dm", "开场。")),
        _tag(_ev("s01-0002", "20:32:00", "狗dm", "两分钟后。")),  # gap = 120s < 300s
    ]
    scenes = segment_scenes(events, "s01", gap_threshold_seconds=300)
    assert len(scenes) == 1


def test_dm_transition_keyword_splits():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "狗dm", "一夜过去了。")),
        _tag(_ev("s01-0002", "20:30:10", "狗dm", "次日清晨，阳光照进房间。")),
        _tag(_ev("s01-0003", "20:30:20", "雷恩", '"大家醒了吗"')),
    ]
    scenes = segment_scenes(events, "s01")
    assert len(scenes) == 2
    assert any("dm_transition" in t for t in scenes[1].triggers)


def test_scene_ids_sequential():
    events = [
        _tag(_ev("s01-0001", "20:30:00", "骰娘", "当前先攻列表为:\n1. 雷恩: 15")),
        _tag(_ev("s01-0002", "20:30:10", "骰娘", "先攻列表已清除")),
        _tag(_ev("s01-0003", "20:30:20", "狗dm", "战后。")),
    ]
    scenes = segment_scenes(events, "s01")
    assert [s.id for s in scenes] == ["s01-scene-001", "s01-scene-002"]


def test_empty_events():
    assert segment_scenes([], "s01") == []

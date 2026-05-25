from pathlib import Path

from trpg2novel.narrate import skeleton as sk
from trpg2novel.outline.schema import VolumeOutline
from trpg2novel.parse.classify import Segment, TaggedEvent
from trpg2novel.segment.scene import Scene
from trpg2novel.state.story_state import StoryState
from trpg2novel.worldview import Worldview


def _scene(scene_id: str, event_id: str) -> Scene:
    return Scene(
        id=scene_id,
        session_id=scene_id.split("-")[0],
        kind="narration",
        start_ts="",
        end_ts="",
        event_ids=[event_id],
        triggers=[],
    )


def _event(event_id: str, text: str) -> TaggedEvent:
    return TaggedEvent(
        id=event_id,
        timestamp="",
        speaker="雷恩",
        source="pc",
        body=text,
        flags={},
        segments=[Segment(kind="pc_action", text=text)],
    )


def _worldview() -> Worldview:
    return Worldview(
        system="test",
        display_name="测试规则",
        banned_words=[],
        roll_translation_rules={},
        prose_style={"pov": "third_limited", "tone": "adventure"},
    )


def test_validate_scene_draft_rejects_missing_boundary():
    ok, err = sk.validate_scene_draft("只有正文", "s01-scene-001", min_words=1)
    assert not ok
    assert "SCENE_BOUNDARY" in err


def test_validate_scene_draft_rejects_wrong_scene_id():
    ok, err = sk.validate_scene_draft("<!-- SCENE_BOUNDARY: other -->\n正文", "s01-scene-001", min_words=1)
    assert not ok
    assert "不匹配" in err


def test_incremental_generation_writes_manifest_and_rebuilds(monkeypatch, tmp_path):
    scenes = [_scene("s01-scene-001", "e1"), _scene("s01-scene-002", "e2")]
    events = {"e1": _event("e1", "冲向门口"), "e2": _event("e2", "扶起同伴")}
    outline = VolumeOutline(volume_index=1, based_on_scenes=[s.id for s in scenes], working_title="测试卷")

    def fake_chat(client, model, messages, **kwargs):
        user = messages[-1]["content"]
        scene_id = "s01-scene-001" if "s01-scene-001" in user else "s01-scene-002"
        return f"<!-- SCENE_BOUNDARY: {scene_id} -->\n" + ("细节" * 300)

    monkeypatch.setattr(sk, "make_client", lambda api_key, base_url: object())
    monkeypatch.setattr(sk, "chat", fake_chat)

    result = sk.draft_skeleton_incremental(
        outline,
        scenes,
        events,
        StoryState(),
        chapters_dir=tmp_path,
        worldview=_worldview(),
        api_key="fake",
        base_url="https://example.invalid/v1",
        model="fake-model",
        target_words_per_scene=1000,
    )

    assert result.complete
    assert result.word_count > 1000
    assert (tmp_path / "vol01_skeleton.md").exists()
    manifest = sk.load_manifest(tmp_path / "vol01_draft_manifest.json")
    assert manifest is not None
    assert manifest.complete
    assert [s.status for s in manifest.scenes] == ["complete", "complete"]

    rebuilt = sk.draft_skeleton_incremental(
        outline,
        scenes,
        events,
        StoryState(),
        chapters_dir=tmp_path,
        worldview=_worldview(),
        api_key="",
        base_url="",
        model="",
        target_words_per_scene=1000,
        rebuild_only=True,
    )
    assert rebuilt.complete
    assert len(rebuilt.scene_offsets) == 2


def test_rebuild_is_incomplete_when_scene_missing(tmp_path):
    scenes = [_scene("s01-scene-001", "e1"), _scene("s01-scene-002", "e2")]
    outline = VolumeOutline(volume_index=1, based_on_scenes=[s.id for s in scenes], working_title="测试卷")
    path = sk.scene_draft_path(tmp_path, 1, "s01-scene-001")
    path.parent.mkdir(parents=True)
    path.write_text("<!-- SCENE_BOUNDARY: s01-scene-001 -->\n" + ("细节" * 300), encoding="utf-8")

    result = sk.rebuild_skeleton_from_scene_drafts(
        outline,
        scenes,
        chapters_dir=tmp_path,
        target_word_count=2000,
        min_total_word_count=1200,
    )

    assert not result.complete
    assert len(result.scene_offsets) == 1

from trpg2novel.chapterize.runner import chapterize_volume
from trpg2novel.chapterize.writer import write_chapters
from trpg2novel.parse.classify import Segment, TaggedEvent
from trpg2novel.outline.schema import VolumeOutline
from trpg2novel.segment.scene import Scene


def test_chapterize_volume_scans_skeleton_text_without_llm():
    skeleton_text = "甲" * 700 + "乙" * 700 + "丙" * 700
    scene_offsets = [
        {"scene_id": "s01_sc001", "char_start": 0, "char_end": 700},
        {"scene_id": "s01_sc002", "char_start": 700, "char_end": 1400},
        {"scene_id": "s01_sc003", "char_start": 1400, "char_end": 2100},
    ]
    outline = VolumeOutline(
        volume_index=1,
        based_on_scenes=["s01_sc001", "s01_sc002", "s01_sc003"],
        working_title="测试卷",
    )
    progress: list[tuple[int, int]] = []

    result = chapterize_volume(
        skeleton_text,
        scene_offsets,
        outline,
        target_words=800,
        tolerance=50,
        hard_cap_max=900,
        api_key="",
        base_url="",
        model="",
        progress_callback=lambda idx, total: progress.append((idx, total)),
    )

    assert result.volume_index == 1
    assert result.chapter_count >= 2
    assert result.skeleton_word_count == len(skeleton_text)
    assert progress
    assert result.cuts[0].char_range[0] == 0
    assert result.cuts[-1].char_range[1] == len(skeleton_text)


def test_write_chapters_outputs_reviewable_drafts(tmp_path):
    skeleton_text = "甲" * 900 + "乙" * 900
    outline = VolumeOutline(volume_index=2, working_title="测试卷")
    result = chapterize_volume(
        skeleton_text,
        [{"scene_id": "s01_sc001", "char_start": 0, "char_end": 900}],
        outline,
        target_words=800,
        tolerance=50,
        hard_cap_max=900,
        api_key="",
        base_url="",
        model="",
    )

    paths = write_chapters(result, skeleton_text, tmp_path, skeleton_source="vol02_skeleton.md")

    assert paths
    assert paths[0].name == "ch01_draft.md"
    text = paths[0].read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "<!-- scenes:" in text
    assert "volume_draft: vol02" in text


def test_write_chapters_outputs_anchor_sidecar(tmp_path):
    skeleton_text = "甲" * 900 + "\n\n" + "乙" * 900
    outline = VolumeOutline(volume_index=2, working_title="测试卷")
    result = chapterize_volume(
        skeleton_text,
        [{"scene_id": "s01_sc001", "char_start": 0, "char_end": len(skeleton_text)}],
        outline,
        target_words=800,
        tolerance=50,
        hard_cap_max=900,
        api_key="",
        base_url="",
        model="",
    )
    scene = Scene(
        id="s01_sc001",
        session_id="s01",
        kind="narration",
        start_ts="",
        end_ts="",
        event_ids=["e1", "e2"],
        triggers=[],
    )
    events = {
        "e1": TaggedEvent(
            id="e1",
            timestamp="",
            speaker="雷恩",
            source="pc",
            body="",
            flags={},
            segments=[Segment(kind="pc_action", text="决定冲向门口保护同伴")],
        ),
        "e2": TaggedEvent(
            id="e2",
            timestamp="",
            speaker="雷恩",
            source="pc",
            body="",
            flags={},
            segments=[Segment(kind="pc_dialogue", text="别退，我来挡住它。")],
        ),
    }

    paths = write_chapters(
        result,
        skeleton_text,
        tmp_path,
        skeleton_source="vol02_skeleton.md",
        scenes_by_id={scene.id: scene},
        events_by_id=events,
    )

    anchor_path = paths[0].with_name("ch01_anchors.json")
    assert anchor_path.exists()
    text = anchor_path.read_text(encoding="utf-8")
    assert "决定冲向门口保护同伴" in text
    assert "别退，我来挡住它。" in text


def test_terminal_chapter_end_is_not_counted_as_hard_cut():
    skeleton_text = "甲" * 9400
    outline = VolumeOutline(volume_index=1, working_title="测试卷")

    result = chapterize_volume(
        skeleton_text,
        [],
        outline,
        target_words=5000,
        tolerance=1000,
        hard_cap_max=6500,
        api_key="",
        base_url="",
        model="",
    )

    assert result.chapter_count == 2
    assert result.cuts[-1].type == "chapter_end"
    assert result.hard_cap_count == 1


def test_default_short_chapter_cadence_splits_long_detail_draft():
    skeleton_text = ("甲" * 1900 + "\n\n") * 5
    outline = VolumeOutline(volume_index=1, working_title="测试卷")

    result = chapterize_volume(
        skeleton_text,
        [],
        outline,
        api_key="",
        base_url="",
        model="",
    )

    assert 4 <= result.chapter_count <= 6
    assert all(c.word_count <= 2600 for c in result.cuts)

from trpg2novel.outline.lifecycle import (
    find_volume,
    mark_closed,
    mark_drafting,
    promote_to_confirmed,
    register_volume_draft,
)
from trpg2novel.state import load_state, save_state


def test_volume_state_can_rollback_generated_stages(tmp_path):
    path = tmp_path / "story_state.yaml"
    state = load_state(path)
    register_volume_draft(
        state,
        volume_index=1,
        scene_ids=["s01_sc001", "s01_sc002"],
        outline_path="vol01.draft.yaml",
        status="proposed",
    )
    promote_to_confirmed(state, 1)
    mark_drafting(state, 1, skeleton_path="vol01_skeleton.md")
    mark_closed(state, 1, chapter_indices=[1, 2], word_count=1234)

    rec = find_volume(state, 1)
    assert rec is not None
    rec.status = "drafting"
    rec.chapter_indices = []
    rec.closed_at = None
    save_state(state, path)

    loaded = load_state(path)
    rec = find_volume(loaded, 1)
    assert rec is not None
    assert rec.status == "drafting"
    assert rec.chapter_indices == []
    assert loaded.processed_scene_ids == ["s01_sc001", "s01_sc002"]

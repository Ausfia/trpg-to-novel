from __future__ import annotations

from ui.shared import (
    chapter_stage_paths,
    chapter_status,
    final_chapter_path,
)


def test_chapter_stage_paths_are_derived_from_draft_path(tmp_path) -> None:
    draft = tmp_path / "ch01_draft.md"
    paths = chapter_stage_paths(draft)

    assert paths["draft"].name == "ch01_draft.md"
    assert paths["revised"].name == "ch01_revised.md"
    assert paths["polished"].name == "ch01_polished.md"
    assert paths["reviewed"].name == "ch01_reviewed.md"


def test_final_chapter_path_uses_markdown_title_and_filters_windows_chars(tmp_path) -> None:
    draft = tmp_path / "ch01_draft.md"
    text = "# 刃:向/龙*影?\n\n正文"

    final_path = final_chapter_path(draft, text, {})

    assert final_path.name == "第1章-刃向龙影.md"


def test_final_chapter_path_falls_back_to_chapter_index_title(tmp_path) -> None:
    draft = tmp_path / "ch02_draft.md"
    state = {"chapter_index": [{"file": "ch02_draft.md", "title": "安全之门"}]}

    final_path = final_chapter_path(draft, "没有标题的正文", state)

    assert final_path.name == "第2章-安全之门.md"


def test_chapter_status_priority(tmp_path) -> None:
    draft = tmp_path / "ch03_draft.md"
    draft.write_text("draft", encoding="utf-8")
    (tmp_path / "ch03_revised.md").write_text("revised", encoding="utf-8")
    (tmp_path / "ch03_polished.md").write_text("polished", encoding="utf-8")
    (tmp_path / "ch03_reviewed.md").write_text("reviewed", encoding="utf-8")
    final = tmp_path / "第3章-终稿.md"
    final.write_text("final", encoding="utf-8")
    state = {"chapter_index": [{"file": "ch03_draft.md", "final_file": final.name}]}

    status = chapter_status(draft, state)

    assert status["stage"] == "final"
    assert status["label"] == "已成稿"

"""Chapterize writer — slice volume skeleton into reviewable chapter drafts."""

from __future__ import annotations

from pathlib import Path

from trpg2novel.chapterize.anchors import anchor_path_for_chapter, build_chapter_anchor_payload, save_anchor_file
from trpg2novel.chapterize.schema import ChapterCut, ChapterizeResult
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene


def write_chapters(
    result: ChapterizeResult,
    skeleton_text: str,
    output_dir: Path,
    *,
    start_chapter_index: int = 1,
    skeleton_source: str = "",
    scenes_by_id: dict[str, Scene] | None = None,
    events_by_id: dict[str, TaggedEvent] | None = None,
) -> list[Path]:
    """把切分结果写出为 ch{NN}_draft.md，供章节审稿页继续编辑。

    Args:
        result: 切分结果。
        skeleton_text: 卷级细节粗稿全文。
        output_dir: 输出目录（通常是 campaign.chapters_dir）。
        start_chapter_index: 章节起始编号。
        skeleton_source: 卷级细节粗稿来源文件名（写入 metadata comment）。
        scenes_by_id/events_by_id: 若提供，同步生成 chNN_anchors.json 素材锚点包。

    Returns:
        写出文件的路径列表。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i, cut in enumerate(result.cuts):
        ch_index = start_chapter_index + i
        ch_path = output_dir / f"ch{ch_index:02d}_draft.md"
        chapter_body = skeleton_text[cut.char_range[0]:cut.char_range[1]]

        metadata = _build_metadata_comments(
            chapter_index=ch_index,
            volume_index=result.volume_index,
            total_chapters=result.chapter_count,
            cut=cut,
            skeleton_source=skeleton_source,
        )

        title = cut.suggested_title or f"第 {ch_index} 章"
        content = f"# {title}\n{metadata}\n\n{chapter_body}"
        ch_path.write_text(content, encoding="utf-8")
        if scenes_by_id is not None and events_by_id is not None:
            anchors = build_chapter_anchor_payload(
                chapter_name=ch_path.name,
                volume_index=result.volume_index,
                cut=cut,
                scenes_by_id=scenes_by_id,
                events_by_id=events_by_id,
            )
            save_anchor_file(anchor_path_for_chapter(ch_path), anchors)

        paths.append(ch_path)

    return paths


def _build_metadata_comments(
    chapter_index: int,
    volume_index: int,
    total_chapters: int,
    cut: ChapterCut,
    skeleton_source: str,
) -> str:
    scenes = ", ".join(cut.scene_ids_covered)
    title = cut.suggested_title or f"第 {chapter_index} 章"
    return "\n".join([
        f"<!-- scenes: {scenes} | events: 0 | focus: {title} -->",
        (
            f"<!-- volume_draft: vol{volume_index:02d} | chapter_in_volume: {chapter_index} "
            f"| total_chapters: {total_chapters} | skeleton_source: {skeleton_source} "
            f"| char_range: {cut.char_range[0]}-{cut.char_range[1]} | cut_type: {cut.type} -->"
        ),
        f"<!-- cut_reason: {cut.reason} -->",
    ])

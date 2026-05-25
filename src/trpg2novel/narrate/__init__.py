"""[6] Narrate 阶段。"""

from trpg2novel.narrate.align import (
    AlignmentResult,
    ParagraphMapping,
    align_paragraphs_to_events,
    load_alignment,
    save_alignment,
)
from trpg2novel.narrate.chapter import (
    ChapterResult,
    DetectionResult,
    detect_boundary,
    draft_chapter,
    save_chapter_draft,
)
from trpg2novel.narrate.narrative_feed import NarrativeEntry, build_feed, feed_to_text
from trpg2novel.narrate.polish import polish_chapter
from trpg2novel.narrate.skeleton import (
    SkeletonResult,
    draft_skeleton_incremental,
    draft_skeleton,
    load_manifest,
    manifest_path,
    save_skeleton,
    scene_draft_path,
)

__all__ = [
    "AlignmentResult",
    "ChapterResult",
    "DetectionResult",
    "NarrativeEntry",
    "ParagraphMapping",
    "SkeletonResult",
    "align_paragraphs_to_events",
    "build_feed",
    "detect_boundary",
    "draft_chapter",
    "draft_skeleton_incremental",
    "draft_skeleton",
    "feed_to_text",
    "load_alignment",
    "load_manifest",
    "manifest_path",
    "polish_chapter",
    "save_alignment",
    "save_chapter_draft",
    "save_skeleton",
    "scene_draft_path",
]

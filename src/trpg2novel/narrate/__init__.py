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

__all__ = [
    "AlignmentResult",
    "ChapterResult",
    "DetectionResult",
    "NarrativeEntry",
    "ParagraphMapping",
    "align_paragraphs_to_events",
    "build_feed",
    "detect_boundary",
    "draft_chapter",
    "feed_to_text",
    "load_alignment",
    "polish_chapter",
    "save_alignment",
    "save_chapter_draft",
]

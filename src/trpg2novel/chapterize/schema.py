"""Chapterize schemas — chapter cut data structures."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CandidateCut:
    """A candidate cut point in the skeleton text."""

    offset: int                           # character position
    type: str                             # scene_end / paragraph_break / word_count_pressure
    scene_id: str = ""                    # for scene_end type
    nearby_text: str = ""                 # ~500 chars around this point


@dataclass
class ChapterCut:
    """A confirmed chapter cut with metadata."""

    chosen_offset: int
    type: str
    reason: str = ""
    suggested_title: str = ""
    scene_ids_covered: list[str] = field(default_factory=list)
    char_range: tuple[int, int] = (0, 0)  # (start, end) in skeleton
    word_count: int = 0


@dataclass
class ChapterizeResult:
    """Result of chapterizing a volume skeleton."""

    volume_index: int
    cuts: list[ChapterCut]
    skeleton_word_count: int
    chapter_count: int
    hard_cap_count: int = 0               # how many cuts were forced by word count pressure

    @property
    def hard_cap_ratio(self) -> float:
        return self.hard_cap_count / max(1, self.chapter_count)

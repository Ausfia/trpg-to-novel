"""Outline schemas — campaign / volume 大纲数据类。

设计原则：
- 所有 dataclass 提供 ``to_dict`` / ``from_dict`` 便于 YAML 序列化。
- 业务校验放在 generate.py / lifecycle.py，本模块只定义结构。
- 时间戳一律 ISO 字符串（无 datetime 对象进入 YAML）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Campaign 大纲
# ---------------------------------------------------------------------------


@dataclass
class KeyCharacter:
    """大纲层的关键角色记录（与人物卡保持同步）。"""

    name: str
    motivation: str = ""
    arc_position: str = ""               # 在主弧中的位置/作用
    status: str = "active"               # active / retired / not_yet_joined
    first_appearance_session: str | None = None
    retired_after_session: str | None = None
    exit_story: str | None = None
    role_in_arcs: list[str] = field(default_factory=list)  # major_arc.id 列表

    @classmethod
    def from_dict(cls, data: dict) -> "KeyCharacter":
        return cls(
            name=data.get("name", ""),
            motivation=data.get("motivation", ""),
            arc_position=data.get("arc_position", ""),
            status=data.get("status", "active"),
            first_appearance_session=data.get("first_appearance_session"),
            retired_after_session=data.get("retired_after_session"),
            exit_story=data.get("exit_story"),
            role_in_arcs=list(data.get("role_in_arcs") or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MajorArc:
    """主线弧。"""

    id: str
    name: str
    status: str = "planned"   # planned / ongoing / resolved / diverged
    summary: str = ""
    last_evidence_session_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "MajorArc":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            status=data.get("status", "planned"),
            summary=data.get("summary", ""),
            last_evidence_session_id=data.get("last_evidence_session_id"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VolumePlanItem:
    """volume_plan 中的一项（campaign 视角的卷规划，非详细卷大纲）。"""

    volume_index: int
    working_title: str = ""
    target_chapter_count_range: str = ""   # 例如 "5–8"
    status: str = "planned"                # planned / in_progress / closed
    summary: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VolumePlanItem":
        return cls(
            volume_index=int(data.get("volume_index", 0)),
            working_title=data.get("working_title", ""),
            target_chapter_count_range=data.get("target_chapter_count_range", ""),
            status=data.get("status", "planned"),
            summary=data.get("summary", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvolutionNote:
    """大纲修订史记录。"""

    timestamp: str
    triggered_by_sessions: list[str] = field(default_factory=list)
    summary: str = ""
    roster_changes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | str | None) -> "EvolutionNote":
        if isinstance(data, str):
            return cls(timestamp="", summary=data)
        data = data or {}
        return cls(
            timestamp=data.get("timestamp", ""),
            triggered_by_sessions=list(data.get("triggered_by_sessions") or []),
            summary=data.get("summary", ""),
            roster_changes=list(data.get("roster_changes") or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CampaignOutline:
    """整团长期大纲。"""

    title: str = ""
    synopsis: str = ""
    themes: list[str] = field(default_factory=list)
    protagonist: str = ""
    pov_default: str = ""
    major_arcs: list[MajorArc] = field(default_factory=list)
    volume_plan: list[VolumePlanItem] = field(default_factory=list)
    key_characters: list[KeyCharacter] = field(default_factory=list)
    notes: str = ""
    based_on_sessions: list[str] = field(default_factory=list)
    last_updated_at: str = ""
    evolution_notes: list[EvolutionNote] = field(default_factory=list)
    pending_revision: dict | None = None        # PR2 才会写入

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignOutline":
        return cls(
            title=data.get("title", ""),
            synopsis=data.get("synopsis", ""),
            themes=list(data.get("themes") or []),
            protagonist=data.get("protagonist", ""),
            pov_default=data.get("pov_default", ""),
            major_arcs=[MajorArc.from_dict(a) for a in (data.get("major_arcs") or [])],
            volume_plan=[VolumePlanItem.from_dict(v) for v in (data.get("volume_plan") or [])],
            key_characters=[KeyCharacter.from_dict(k) for k in (data.get("key_characters") or [])],
            notes=data.get("notes", ""),
            based_on_sessions=list(data.get("based_on_sessions") or []),
            last_updated_at=data.get("last_updated_at", ""),
            evolution_notes=[EvolutionNote.from_dict(n) for n in (data.get("evolution_notes") or [])],
            pending_revision=data.get("pending_revision"),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "title": self.title,
            "synopsis": self.synopsis,
            "themes": list(self.themes),
            "protagonist": self.protagonist,
            "pov_default": self.pov_default,
            "major_arcs": [a.to_dict() for a in self.major_arcs],
            "volume_plan": [v.to_dict() for v in self.volume_plan],
            "key_characters": [k.to_dict() for k in self.key_characters],
            "notes": self.notes,
            "based_on_sessions": list(self.based_on_sessions),
            "last_updated_at": self.last_updated_at,
            "evolution_notes": [n.to_dict() for n in self.evolution_notes],
        }
        if self.pending_revision is not None:
            out["pending_revision"] = self.pending_revision
        return out


# ---------------------------------------------------------------------------
# Volume 大纲
# ---------------------------------------------------------------------------


@dataclass
class EmotionBeat:
    """情绪曲线节点。position 0–1，intensity 1–10。"""

    position: float
    label: str = ""
    intensity: int = 5

    @classmethod
    def from_dict(cls, data: dict) -> "EmotionBeat":
        return cls(
            position=float(data.get("position", 0.0)),
            label=data.get("label", ""),
            intensity=int(data.get("intensity", 5)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KeyBeat:
    """关键情节节点。featured_characters 必须 ⊆ roster.active_in_volume。"""

    id: str
    anchor_scene_id: str
    type: str                                 # opening/rising/turning_point/climax/resolution/cliffhanger/farewell
    description: str = ""
    must_appear: bool = True
    featured_characters: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "KeyBeat":
        return cls(
            id=data.get("id", ""),
            anchor_scene_id=data.get("anchor_scene_id", ""),
            type=data.get("type", ""),
            description=data.get("description", ""),
            must_appear=bool(data.get("must_appear", True)),
            featured_characters=list(data.get("featured_characters") or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Subplot:
    name: str
    weave_in_beats: list[str] = field(default_factory=list)   # KeyBeat.id 列表
    cadence: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Subplot":
        return cls(
            name=data.get("name", ""),
            weave_in_beats=list(data.get("weave_in_beats") or []),
            cadence=data.get("cadence", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RetiringCharacter:
    name: str
    retire_after_session: str
    exit_story: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "RetiringCharacter":
        return cls(
            name=data.get("name", ""),
            retire_after_session=data.get("retire_after_session", ""),
            exit_story=data.get("exit_story", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JoiningCharacter:
    name: str
    first_appearance_session: str

    @classmethod
    def from_dict(cls, data: dict) -> "JoiningCharacter":
        return cls(
            name=data.get("name", ""),
            first_appearance_session=data.get("first_appearance_session", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VolumeRoster:
    """卷内阵容快照。供 LLM 规划 key_beats 时取用。"""

    active_in_volume: list[str] = field(default_factory=list)
    absent_sessions: dict[str, list[str]] = field(default_factory=dict)     # name -> ["s02","s05（在神殿闭关）"]
    retiring_in_volume: list[RetiringCharacter] = field(default_factory=list)
    joining_in_volume: list[JoiningCharacter] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "VolumeRoster":
        return cls(
            active_in_volume=list(data.get("active_in_volume") or []),
            absent_sessions={k: list(v) for k, v in (data.get("absent_sessions") or {}).items()},
            retiring_in_volume=[
                RetiringCharacter.from_dict(r) for r in (data.get("retiring_in_volume") or [])
            ],
            joining_in_volume=[
                JoiningCharacter.from_dict(j) for j in (data.get("joining_in_volume") or [])
            ],
        )

    def to_dict(self) -> dict:
        return {
            "active_in_volume": list(self.active_in_volume),
            "absent_sessions": {k: list(v) for k, v in self.absent_sessions.items()},
            "retiring_in_volume": [r.to_dict() for r in self.retiring_in_volume],
            "joining_in_volume": [j.to_dict() for j in self.joining_in_volume],
        }


@dataclass
class VolumeOutline:
    """单卷详细大纲。"""

    volume_index: int
    based_on_scenes: list[str] = field(default_factory=list)
    scene_range: list[str] = field(default_factory=list)        # [first_scene_id, last_scene_id]
    session_ids: list[str] = field(default_factory=list)
    target_word_count_estimate: int = 0
    target_chapter_count_estimate: int = 0
    working_title: str = ""
    theme_summary: str = ""
    emotion_arc: list[EmotionBeat] = field(default_factory=list)
    key_beats: list[KeyBeat] = field(default_factory=list)
    subplots: list[Subplot] = field(default_factory=list)
    pacing_notes: list[str] = field(default_factory=list)
    ending_strategy: str = "closed"                              # closed / cliffhanger / open
    roster: VolumeRoster = field(default_factory=VolumeRoster)
    # 生命周期
    status: str = "draft"            # draft / confirmed / drafting / closed
    user_confirmed: bool = False
    confirmed_at: str | None = None
    closed_at: str | None = None
    # 来源元信息（propose 阶段写入，便于追溯）
    proposal_reasoning: str = ""
    last_updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "VolumeOutline":
        return cls(
            volume_index=int(data.get("volume_index", 0)),
            based_on_scenes=list(data.get("based_on_scenes") or []),
            scene_range=list(data.get("scene_range") or []),
            session_ids=list(data.get("session_ids") or []),
            target_word_count_estimate=int(data.get("target_word_count_estimate", 0)),
            target_chapter_count_estimate=int(data.get("target_chapter_count_estimate", 0)),
            working_title=data.get("working_title", ""),
            theme_summary=data.get("theme_summary", ""),
            emotion_arc=[EmotionBeat.from_dict(e) for e in (data.get("emotion_arc") or [])],
            key_beats=[KeyBeat.from_dict(b) for b in (data.get("key_beats") or [])],
            subplots=[Subplot.from_dict(s) for s in (data.get("subplots") or [])],
            pacing_notes=list(data.get("pacing_notes") or []),
            ending_strategy=data.get("ending_strategy", "closed"),
            roster=VolumeRoster.from_dict(data.get("roster") or {}),
            status=data.get("status", "draft"),
            user_confirmed=bool(data.get("user_confirmed", False)),
            confirmed_at=data.get("confirmed_at"),
            closed_at=data.get("closed_at"),
            proposal_reasoning=data.get("proposal_reasoning", ""),
            last_updated_at=data.get("last_updated_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "volume_index": self.volume_index,
            "based_on_scenes": list(self.based_on_scenes),
            "scene_range": list(self.scene_range),
            "session_ids": list(self.session_ids),
            "target_word_count_estimate": self.target_word_count_estimate,
            "target_chapter_count_estimate": self.target_chapter_count_estimate,
            "working_title": self.working_title,
            "theme_summary": self.theme_summary,
            "emotion_arc": [e.to_dict() for e in self.emotion_arc],
            "key_beats": [b.to_dict() for b in self.key_beats],
            "subplots": [s.to_dict() for s in self.subplots],
            "pacing_notes": list(self.pacing_notes),
            "ending_strategy": self.ending_strategy,
            "roster": self.roster.to_dict(),
            "status": self.status,
            "user_confirmed": self.user_confirmed,
            "confirmed_at": self.confirmed_at,
            "closed_at": self.closed_at,
            "proposal_reasoning": self.proposal_reasoning,
            "last_updated_at": self.last_updated_at,
        }

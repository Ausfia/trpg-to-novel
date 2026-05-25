"""Volume detail draft generation.

The public output remains ``volNN_skeleton.md`` for compatibility, but the draft
is now generated as restartable per-scene files and then assembled.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.llm.client import chat, make_client
from trpg2novel.narrate.narrative_feed import build_feed, feed_to_text
from trpg2novel.outline.schema import KeyBeat, VolumeOutline
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene
from trpg2novel.state.story_state import StoryState
from trpg2novel.worldview import Worldview

if TYPE_CHECKING:
    from trpg2novel.rag.store import KnowledgeBase, RetrievedChunk

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))

BOUNDARY_RE = re.compile(r"<!--\s*SCENE_BOUNDARY:\s*(\S+)\s*-->")


@dataclass
class SkeletonResult:
    volume_index: int
    skeleton_text: str
    scene_ids: list[str]
    scene_offsets: list[dict]
    word_count: int
    target_chapter_count: int
    manifest_path: str = ""
    complete: bool = True
    target_word_count: int = 0


@dataclass
class SceneDraftStatus:
    scene_id: str
    status: str = "pending"  # pending / complete / failed
    path: str = ""
    word_count: int = 0
    target_words: int = 0
    min_words: int = 0
    event_count: int = 0
    input_chars: int = 0
    error: str = ""
    updated_at: str = ""
    needs_retry: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SceneDraftStatus":
        return cls(
            scene_id=str(data.get("scene_id") or ""),
            status=str(data.get("status") or "pending"),
            path=str(data.get("path") or ""),
            word_count=int(data.get("word_count") or 0),
            target_words=int(data.get("target_words") or 0),
            min_words=int(data.get("min_words") or 0),
            event_count=int(data.get("event_count") or 0),
            input_chars=int(data.get("input_chars") or 0),
            error=str(data.get("error") or ""),
            updated_at=str(data.get("updated_at") or ""),
            needs_retry=bool(data.get("needs_retry") or False),
        )


@dataclass
class VolumeDraftManifest:
    volume_index: int
    scene_ids: list[str] = field(default_factory=list)
    target_word_count: int = 0
    min_total_word_count: int = 0
    skeleton_path: str = ""
    scene_drafts_dir: str = ""
    complete: bool = False
    total_word_count: int = 0
    target_chapter_count_estimate: int = 0
    updated_at: str = ""
    scenes: list[SceneDraftStatus] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["scenes"] = [s.to_dict() for s in self.scenes]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "VolumeDraftManifest":
        return cls(
            volume_index=int(data.get("volume_index") or 0),
            scene_ids=list(data.get("scene_ids") or []),
            target_word_count=int(data.get("target_word_count") or 0),
            min_total_word_count=int(data.get("min_total_word_count") or 0),
            skeleton_path=str(data.get("skeleton_path") or ""),
            scene_drafts_dir=str(data.get("scene_drafts_dir") or ""),
            complete=bool(data.get("complete") or False),
            total_word_count=int(data.get("total_word_count") or 0),
            target_chapter_count_estimate=int(data.get("target_chapter_count_estimate") or 0),
            updated_at=str(data.get("updated_at") or ""),
            scenes=[SceneDraftStatus.from_dict(s) for s in (data.get("scenes") or [])],
        )


def scene_drafts_dir(chapters_dir: Path, volume_index: int) -> Path:
    return chapters_dir / f"vol{volume_index:02d}_scene_drafts"


def manifest_path(chapters_dir: Path, volume_index: int) -> Path:
    return chapters_dir / f"vol{volume_index:02d}_draft_manifest.json"


def scene_draft_path(chapters_dir: Path, volume_index: int, scene_id: str) -> Path:
    safe = scene_id.replace("/", "_").replace("\\", "_")
    return scene_drafts_dir(chapters_dir, volume_index) / f"{safe}.md"


def load_manifest(path: Path) -> VolumeDraftManifest | None:
    if not path.exists():
        return None
    return VolumeDraftManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_manifest(manifest: VolumeDraftManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def validate_scene_draft(text: str, scene_id: str, *, min_words: int = 500) -> tuple[bool, str]:
    body = _strip_front_matter(text).strip()
    if not body:
        return False, "草稿为空"
    matches = BOUNDARY_RE.findall(body)
    if not matches:
        return False, "缺少 SCENE_BOUNDARY"
    if matches[0] != scene_id:
        return False, f"SCENE_BOUNDARY scene_id 不匹配：{matches[0]}"
    if len(body) < min_words:
        return False, f"字数过短：{len(body)} < {min_words}"
    return True, ""


def draft_skeleton(
    volume_outline: VolumeOutline,
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    state: StoryState,
    *,
    worldview: Worldview,
    pc_facts: dict[str, list[str]] | None = None,
    last_volume_ending_marker: str = "",
    api_key: str,
    base_url: str,
    model: str,
    story_name: str = "巨龙僭政",
    kb: "KnowledgeBase | None" = None,
    target_words_per_scene: int = 2200,
) -> SkeletonResult:
    """Compatibility wrapper: generate all scene drafts in-memory temp dir."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        return draft_skeleton_incremental(
            volume_outline,
            scenes,
            events_by_id,
            state,
            chapters_dir=Path(tmp),
            worldview=worldview,
            pc_facts=pc_facts,
            last_volume_ending_marker=last_volume_ending_marker,
            api_key=api_key,
            base_url=base_url,
            model=model,
            story_name=story_name,
            kb=kb,
            target_words_per_scene=target_words_per_scene,
            force=True,
        )


def draft_skeleton_incremental(
    volume_outline: VolumeOutline,
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    state: StoryState,
    *,
    chapters_dir: Path,
    worldview: Worldview,
    pc_facts: dict[str, list[str]] | None = None,
    last_volume_ending_marker: str = "",
    api_key: str,
    base_url: str,
    model: str,
    story_name: str = "巨龙僭政",
    kb: "KnowledgeBase | None" = None,
    target_words_per_scene: int = 2200,
    scene_id: str = "",
    force: bool = False,
    rebuild_only: bool = False,
    progress_callback: Callable[[str, str], None] | None = None,
) -> SkeletonResult:
    """Generate or rebuild a volume detail draft from restartable scene drafts."""
    scene_list = list(scenes)
    scene_ids = [s.id for s in scene_list]
    target_total = len(scene_list) * target_words_per_scene
    min_total = round(target_total * 0.6)
    out_path = chapters_dir / f"vol{volume_outline.volume_index:02d}_skeleton.md"
    man_path = manifest_path(chapters_dir, volume_outline.volume_index)
    drafts_dir = scene_drafts_dir(chapters_dir, volume_outline.volume_index)
    drafts_dir.mkdir(parents=True, exist_ok=True)

    manifest = _init_manifest(
        volume_outline.volume_index,
        scene_ids,
        target_total,
        min_total,
        out_path,
        drafts_dir,
        target_words_per_scene,
        events_by_id,
        scene_list,
    )
    existing = load_manifest(man_path)
    if existing is not None:
        _merge_manifest(manifest, existing)

    if not rebuild_only:
        client = make_client(api_key, base_url)
        retrieved = _query_world_kb(kb, volume_outline)
        previous_tail = last_volume_ending_marker
        for scene in scene_list:
            if scene_id and scene.id != scene_id:
                existing_path = scene_draft_path(chapters_dir, volume_outline.volume_index, scene.id)
                if existing_path.exists():
                    previous_tail = _tail(existing_path.read_text(encoding="utf-8"))
                continue
            status = _status_for(manifest, scene.id)
            draft_path = scene_draft_path(chapters_dir, volume_outline.volume_index, scene.id)
            if draft_path.exists() and status.status == "complete" and not force:
                previous_tail = _tail(draft_path.read_text(encoding="utf-8"))
                if progress_callback:
                    progress_callback(scene.id, "skip")
                continue
            if progress_callback:
                progress_callback(scene.id, "start")
            try:
                text = _draft_scene(
                    client=client,
                    model=model,
                    volume_outline=volume_outline,
                    scene=scene,
                    events_by_id=events_by_id,
                    state=state,
                    worldview=worldview,
                    pc_facts=pc_facts or {},
                    story_name=story_name,
                    target_words=target_words_per_scene,
                    previous_scene_tail=previous_tail,
                    retrieved=retrieved,
                )
                if not BOUNDARY_RE.search(text):
                    text = f"<!-- SCENE_BOUNDARY: {scene.id} -->\n{text.strip()}"
                ok, err = validate_scene_draft(text, scene.id, min_words=_min_scene_words(scene, target_words_per_scene))
                draft_path.write_text(text.strip() + "\n", encoding="utf-8")
                status.path = str(draft_path)
                status.word_count = len(text)
                status.error = err
                status.status = "complete" if ok else "failed"
                status.needs_retry = not ok
                status.updated_at = _now()
                previous_tail = _tail(text)
                if progress_callback:
                    progress_callback(scene.id, status.status)
            except Exception as exc:
                status.status = "failed"
                status.error = str(exc)
                status.needs_retry = True
                status.updated_at = _now()
                if progress_callback:
                    progress_callback(scene.id, "failed")
            save_manifest(_refresh_manifest(manifest, chapters_dir, volume_outline.volume_index, target_total, min_total), man_path)

    result = rebuild_skeleton_from_scene_drafts(
        volume_outline,
        scene_list,
        chapters_dir=chapters_dir,
        target_word_count=target_total,
        min_total_word_count=min_total,
    )
    save_skeleton(result, out_path, volume_outline)
    manifest = _refresh_manifest(manifest, chapters_dir, volume_outline.volume_index, target_total, min_total)
    manifest.skeleton_path = str(out_path)
    save_manifest(manifest, man_path)
    result.manifest_path = str(man_path)
    result.complete = manifest.complete
    result.target_word_count = target_total
    return result


def rebuild_skeleton_from_scene_drafts(
    volume_outline: VolumeOutline,
    scenes: Sequence[Scene],
    *,
    chapters_dir: Path,
    target_word_count: int,
    min_total_word_count: int,
) -> SkeletonResult:
    parts: list[str] = []
    scene_ids: list[str] = []
    for scene in scenes:
        path = scene_draft_path(chapters_dir, volume_outline.volume_index, scene.id)
        if not path.exists():
            continue
        text = _strip_front_matter(path.read_text(encoding="utf-8")).strip()
        ok, _ = validate_scene_draft(text, scene.id, min_words=1)
        if not ok:
            continue
        parts.append(text)
        scene_ids.append(scene.id)
    skeleton_text = "\n\n".join(parts).strip()
    offsets = _extract_scene_offsets(skeleton_text, scene_ids)
    word_count = len(skeleton_text)
    complete = len(scene_ids) == len(scenes) and word_count >= min_total_word_count
    return SkeletonResult(
        volume_index=volume_outline.volume_index,
        skeleton_text=skeleton_text,
        scene_ids=[s.id for s in scenes],
        scene_offsets=offsets,
        word_count=word_count,
        target_chapter_count=max(1, round(max(target_word_count, word_count) / 2000)),
        complete=complete,
        target_word_count=target_word_count,
    )


def save_skeleton(result: SkeletonResult, out_path: Path, volume_outline: VolumeOutline) -> None:
    """保存兼容卷级细节粗稿 Markdown，含 YAML front-matter 元信息。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    front_matter = {
        "volume_index": result.volume_index,
        "source_scene_ids": result.scene_ids,
        "target_chapter_count_estimate": result.target_chapter_count,
        "word_count": result.word_count,
        "target_word_count": result.target_word_count,
        "complete": result.complete,
        "manifest_path": result.manifest_path,
        "outline_path": str(getattr(volume_outline, "outline_path", "") or ""),
        "scene_offsets": result.scene_offsets,
    }
    yaml_block = yaml.dump(front_matter, allow_unicode=True, sort_keys=False, default_flow_style=False)
    out_path.write_text(f"---\n{yaml_block}---\n\n{result.skeleton_text}", encoding="utf-8")


def _draft_scene(
    *,
    client,
    model: str,
    volume_outline: VolumeOutline,
    scene: Scene,
    events_by_id: dict[str, TaggedEvent],
    state: StoryState,
    worldview: Worldview,
    pc_facts: dict[str, list[str]],
    story_name: str,
    target_words: int,
    previous_scene_tail: str,
    retrieved: list["RetrievedChunk"],
) -> str:
    feed = build_feed([scene], events_by_id)
    narrative_text = feed_to_text(feed, include_roll_outcomes=True)
    min_words = _min_scene_words(scene, target_words)
    relevant_beats = _relevant_beats(volume_outline.key_beats, scene.id)
    system_prompt = _env.get_template("scene_skeleton_system.j2").render(
        story_name=story_name,
        worldview=worldview,
        pc_facts=pc_facts,
        volume_title=volume_outline.working_title,
        theme_summary=volume_outline.theme_summary,
        ending_strategy=volume_outline.ending_strategy,
        relevant_beats=relevant_beats,
        previous_scene_tail=previous_scene_tail,
        scene_id=scene.id,
        target_words=target_words,
        min_words=min_words,
        characters=state.characters,
        retrieved=retrieved,
    )
    user_prompt = _env.get_template("scene_skeleton_user.j2").render(
        scene_id=scene.id,
        session_id=scene.session_id,
        scene_kind=scene.kind,
        event_count=scene.event_count,
        target_words=target_words,
        narrative_text=narrative_text,
    )
    return chat(
        client,
        model,
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=float(os.environ.get("LLM_SCENE_DRAFT_TEMPERATURE", "0.85")),
        max_tokens=int(os.environ.get("LLM_SCENE_DRAFT_MAX_TOKENS", "6000")),
    )


def _extract_scene_offsets(text: str, expected_scene_ids: list[str]) -> list[dict]:
    matches: list[tuple[str, int]] = []
    for m in BOUNDARY_RE.finditer(text):
        sid = m.group(1)
        if not expected_scene_ids or sid in expected_scene_ids:
            matches.append((sid, m.start()))
    offsets: list[dict] = []
    for i, (sid, pos) in enumerate(matches):
        next_pos = matches[i + 1][1] if i + 1 < len(matches) else len(text)
        offsets.append({"scene_id": sid, "char_start": pos, "char_end": next_pos})
    return offsets


def _init_manifest(
    volume_index: int,
    scene_ids: list[str],
    target_total: int,
    min_total: int,
    out_path: Path,
    drafts_dir: Path,
    target_words_per_scene: int,
    events_by_id: dict[str, TaggedEvent],
    scenes: Sequence[Scene],
) -> VolumeDraftManifest:
    statuses = []
    for scene in scenes:
        statuses.append(SceneDraftStatus(
            scene_id=scene.id,
            target_words=target_words_per_scene,
            min_words=_min_scene_words(scene, target_words_per_scene),
            event_count=scene.event_count,
            input_chars=sum(len(events_by_id[eid].body) for eid in scene.event_ids if eid in events_by_id),
        ))
    return VolumeDraftManifest(
        volume_index=volume_index,
        scene_ids=scene_ids,
        target_word_count=target_total,
        min_total_word_count=min_total,
        skeleton_path=str(out_path),
        scene_drafts_dir=str(drafts_dir),
        target_chapter_count_estimate=max(1, round(target_total / 2000)),
        scenes=statuses,
    )


def _merge_manifest(base: VolumeDraftManifest, existing: VolumeDraftManifest) -> None:
    old = {s.scene_id: s for s in existing.scenes}
    for status in base.scenes:
        if status.scene_id in old:
            prev = old[status.scene_id]
            status.status = prev.status
            status.path = prev.path
            status.word_count = prev.word_count
            status.error = prev.error
            status.updated_at = prev.updated_at
            status.needs_retry = prev.needs_retry


def _refresh_manifest(
    manifest: VolumeDraftManifest,
    chapters_dir: Path,
    volume_index: int,
    target_total: int,
    min_total: int,
) -> VolumeDraftManifest:
    total = 0
    complete_count = 0
    for status in manifest.scenes:
        path = scene_draft_path(chapters_dir, volume_index, status.scene_id)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            ok, err = validate_scene_draft(text, status.scene_id, min_words=status.min_words or 1)
            status.path = str(path)
            status.word_count = len(_strip_front_matter(text).strip())
            status.status = "complete" if ok else "failed"
            status.error = "" if ok else err
            status.needs_retry = not ok
            if ok:
                total += status.word_count
                complete_count += 1
        elif status.status != "failed":
            status.status = "pending"
            status.needs_retry = True
    manifest.total_word_count = total
    manifest.complete = complete_count == len(manifest.scenes) and total >= min_total
    manifest.target_word_count = target_total
    manifest.min_total_word_count = min_total
    manifest.target_chapter_count_estimate = max(1, round(max(target_total, total) / 2000))
    manifest.updated_at = _now()
    return manifest


def _status_for(manifest: VolumeDraftManifest, scene_id: str) -> SceneDraftStatus:
    for status in manifest.scenes:
        if status.scene_id == scene_id:
            return status
    status = SceneDraftStatus(scene_id=scene_id)
    manifest.scenes.append(status)
    return status


def _query_world_kb(kb: "KnowledgeBase | None", volume_outline: VolumeOutline) -> list["RetrievedChunk"]:
    if kb is None:
        return []
    try:
        query = " ".join([volume_outline.working_title, volume_outline.theme_summary])[:500]
        return kb.query(query)
    except Exception:
        return []


def _relevant_beats(beats: Sequence[KeyBeat], scene_id: str) -> list[KeyBeat]:
    exact = [b for b in beats if b.anchor_scene_id == scene_id]
    return exact or list(beats[:3])


def _min_scene_words(scene: Scene, target_words: int) -> int:
    if scene.event_count >= 80:
        return min(900, round(target_words * 0.45))
    if scene.event_count >= 30:
        return min(700, round(target_words * 0.35))
    return min(500, round(target_words * 0.25))


def _tail(text: str, max_chars: int = 300) -> str:
    body = _strip_front_matter(text).strip()
    return body[-max_chars:] if body else ""


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
    return text


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

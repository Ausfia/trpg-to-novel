"""Chapter anchor sidecars for preserving table-play detail."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from trpg2novel.chapterize.schema import ChapterCut
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene

ANCHOR_KEYS = ("actions", "dialogues", "choices", "emotions", "discarded_noise")

_CHOICE_RE = re.compile(
    r"(决定|选择|试图|尝试|打算|准备|要求|拒绝|同意|询问|追问|走向|冲向|攻击|施放|保护|阻止|检查|寻找|交涉|威胁)"
)
_EMOTION_RE = re.compile(
    r"(愤怒|恐惧|害怕|紧张|犹豫|沉默|惊讶|震惊|痛苦|疲惫|安心|怀疑|信任|敌意|歉意|感激|悲伤|压抑|尴尬|动摇|决心|关系)"
)
_NOISE_KINDS = {
    "pc_ooc",
    "roll_cmd",
    "turn_marker",
    "bot_state",
    "initiative_list",
    "initiative_clear",
    "record_meta",
    "control_cmd",
    "image",
    "image_meta",
    "unknown",
    "unmarked_warning",
}


def anchor_path_for_chapter(chapter_path: Path) -> Path:
    """Return the sidecar path for ``chNN_draft.md``."""
    return chapter_path.with_name(chapter_path.stem.replace("_draft", "") + "_anchors.json")


def load_anchor_file(path: Path) -> dict[str, Any]:
    """Load an anchor sidecar, returning an empty editable payload if missing."""
    if not path.exists():
        return empty_anchor_payload()
    data = json.loads(path.read_text(encoding="utf-8"))
    return normalize_anchor_payload(data)


def save_anchor_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_anchor_payload(payload)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def empty_anchor_payload(**meta: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "chapter": meta.get("chapter", ""),
        "volume_index": meta.get("volume_index", 0),
        "source_scene_ids": list(meta.get("source_scene_ids") or []),
        "char_range": list(meta.get("char_range") or []),
        "actions": [],
        "dialogues": [],
        "choices": [],
        "emotions": [],
        "discarded_noise": [],
    }
    return payload


def normalize_anchor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = empty_anchor_payload(
        chapter=payload.get("chapter", ""),
        volume_index=payload.get("volume_index", 0),
        source_scene_ids=payload.get("source_scene_ids") or [],
        char_range=payload.get("char_range") or [],
    )
    normalized["version"] = int(payload.get("version") or 1)
    for key in ANCHOR_KEYS:
        normalized[key] = _normalize_items(payload.get(key) or [])
    return normalized


def anchors_to_prompt_text(payload: dict[str, Any]) -> str:
    """Render anchors as compact Chinese instructions for polish prompts."""
    payload = normalize_anchor_payload(payload)
    labels = {
        "actions": "关键动作",
        "dialogues": "关键台词",
        "choices": "角色选择",
        "emotions": "情绪/关系变化",
        "discarded_noise": "应舍弃噪音",
    }
    parts: list[str] = []
    for key in ANCHOR_KEYS:
        items = payload.get(key) or []
        if not items:
            continue
        lines = [f"## {labels[key]}"]
        for item in items:
            text = item.get("text") if isinstance(item, dict) else str(item)
            scene_id = item.get("scene_id", "") if isinstance(item, dict) else ""
            speaker = item.get("speaker", "") if isinstance(item, dict) else ""
            prefix = " / ".join(x for x in (scene_id, speaker) if x)
            lines.append(f"- {prefix + ': ' if prefix else ''}{text}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def build_chapter_anchor_payload(
    *,
    chapter_name: str,
    volume_index: int,
    cut: ChapterCut,
    scenes_by_id: dict[str, Scene],
    events_by_id: dict[str, TaggedEvent],
    max_items_per_category: int = 24,
) -> dict[str, Any]:
    """Extract editable anchors from the original events covered by a chapter cut."""
    payload = empty_anchor_payload(
        chapter=chapter_name,
        volume_index=volume_index,
        source_scene_ids=cut.scene_ids_covered,
        char_range=cut.char_range,
    )
    seen: dict[str, set[str]] = {key: set() for key in ANCHOR_KEYS}

    for scene_id in cut.scene_ids_covered:
        scene = scenes_by_id.get(scene_id)
        if scene is None:
            continue
        for event_id in scene.event_ids:
            ev = events_by_id.get(event_id)
            if ev is None:
                continue
            for seg in ev.segments:
                kind = _seg_value(seg, "kind")
                text = _clean_text(_seg_value(seg, "text"))
                if not text:
                    continue
                base = {
                    "scene_id": scene_id,
                    "event_id": event_id,
                    "speaker": ev.speaker,
                    "text": text,
                }
                if kind == "pc_action":
                    _append_anchor(payload, seen, "actions", base, max_items_per_category)
                    if _CHOICE_RE.search(text):
                        _append_anchor(payload, seen, "choices", base, max_items_per_category)
                elif kind == "pc_dialogue":
                    item = dict(base)
                    item["text"] = f"「{text}」"
                    _append_anchor(payload, seen, "dialogues", item, max_items_per_category)
                    if _CHOICE_RE.search(text):
                        _append_anchor(payload, seen, "choices", item, max_items_per_category)
                    if _EMOTION_RE.search(text):
                        _append_anchor(payload, seen, "emotions", item, max_items_per_category)
                elif kind == "dm_narration" and _EMOTION_RE.search(text):
                    _append_anchor(payload, seen, "emotions", base, max_items_per_category)
                elif kind == "roll_result":
                    if _CHOICE_RE.search(text):
                        _append_anchor(payload, seen, "choices", base, max_items_per_category)
                elif kind in _NOISE_KINDS:
                    _append_anchor(payload, seen, "discarded_noise", base, max_items_per_category)

    return payload


def _normalize_items(items: Iterable[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            normalized.append({
                "scene_id": str(item.get("scene_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "speaker": str(item.get("speaker") or ""),
                "text": text,
            })
        else:
            text = str(item).strip()
            if text:
                normalized.append({"scene_id": "", "event_id": "", "speaker": "", "text": text})
    return normalized


def _append_anchor(
    payload: dict[str, Any],
    seen: dict[str, set[str]],
    key: str,
    item: dict[str, str],
    max_items: int,
) -> None:
    if len(payload[key]) >= max_items:
        return
    signature = f"{item.get('scene_id')}|{item.get('speaker')}|{item.get('text')}"
    if signature in seen[key]:
        return
    seen[key].add(signature)
    payload[key].append(item)


def _seg_value(seg: Any, key: str) -> str:
    if isinstance(seg, dict):
        return str(seg.get(key) or "")
    return str(getattr(seg, key, "") or "")


def _clean_text(text: str, *, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text

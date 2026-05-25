"""场景一句话摘要：用于 volumes-propose 阶段的轻量上下文与 UI 时间线。

输出缓存到 ``<campaign>/parsed/scene_summaries.json``，结构：
    {
      "<scene_id>": {
        "summary": "<25–40 字>",
        "generated_at": "<ISO timestamp>",
        "model": "<model name>"
      },
      ...
    }

`get_or_generate_scene_summary` 命中缓存时不再调用 LLM。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.config import StageLLMConfig
from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.narrate.narrative_feed import build_feed, feed_to_text
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_env = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))

# 单场景送给 LLM 的 narrative_excerpt 最大字符数（控制成本）
_MAX_EXCERPT_CHARS = 4000


@dataclass
class SceneSummary:
    summary: str
    generated_at: str
    model: str
    needs_review: bool = False  # 长度异常等校验失败时打标

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SceneSummary":
        return cls(
            summary=data.get("summary", ""),
            generated_at=data.get("generated_at", ""),
            model=data.get("model", ""),
            needs_review=bool(data.get("needs_review", False)),
        )


SceneSummaryCache = dict[str, SceneSummary]


def cache_path(campaign_parsed_dir: Path) -> Path:
    return campaign_parsed_dir / "scene_summaries.json"


def load_summary_cache(campaign_parsed_dir: Path) -> SceneSummaryCache:
    p = cache_path(campaign_parsed_dir)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: SceneSummaryCache = {}
    for scene_id, entry in raw.items():
        if isinstance(entry, str):
            # 兼容历史：纯字符串形式
            out[scene_id] = SceneSummary(summary=entry, generated_at="", model="")
        elif isinstance(entry, dict):
            out[scene_id] = SceneSummary.from_dict(entry)
    return out


def save_summary_cache(cache: SceneSummaryCache, campaign_parsed_dir: Path) -> None:
    p = cache_path(campaign_parsed_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    serialized = {sid: s.to_dict() for sid, s in cache.items()}
    p.write_text(
        json.dumps(serialized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_excerpt(scene: Scene, events_by_id: dict[str, TaggedEvent]) -> str:
    """从 scene 的 events 抽出一段 narrative excerpt（去骰子结果，降噪）。"""
    feed = build_feed([scene], events_by_id)
    text = feed_to_text(feed, include_roll_outcomes=False)
    if len(text) > _MAX_EXCERPT_CHARS:
        # 头尾各取一半，中间用省略标记
        half = _MAX_EXCERPT_CHARS // 2
        text = text[: half - 20] + "\n……（中间省略）……\n" + text[-(half - 20):]
    return text


def _validate_summary(text: str) -> bool:
    """简单长度/关键词校验。返回 True 表示通过；False 表示需要 review。"""
    stripped = text.strip()
    if not stripped:
        return False
    # 25–40 字是软目标，给一些容差：低于 12 字明显过短，高于 80 字明显过长
    n = len(stripped)
    if n < 12 or n > 80:
        return False
    return True


def generate_scene_summary(
    scene: Scene,
    events_by_id: dict[str, TaggedEvent],
    *,
    model_cfg: StageLLMConfig,
) -> SceneSummary:
    """调 LLM 生成单场景摘要（不读缓存）。"""
    excerpt = _build_excerpt(scene, events_by_id)

    system_prompt = _env.get_template("scene_summary_system.j2").render()
    user_prompt = _env.get_template("scene_summary_user.j2").render(
        scene=scene,
        narrative_excerpt=excerpt,
    )

    client = make_client(model_cfg.api_key, model_cfg.base_url)
    raw = chat_json(
        client,
        model_cfg.model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )

    text = (raw.get("summary") or "").strip()
    return SceneSummary(
        summary=text,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        model=model_cfg.model,
        needs_review=not _validate_summary(text),
    )


def get_or_generate_scene_summary(
    scene: Scene,
    events_by_id: dict[str, TaggedEvent],
    *,
    campaign_parsed_dir: Path,
    model_cfg: StageLLMConfig,
    cache: SceneSummaryCache | None = None,
    force: bool = False,
) -> SceneSummary:
    """命中缓存直接返回；否则生成并写回缓存。

    Args:
        cache: 若调用方已持有内存中的缓存（批量场景下），传入避免反复读盘；
               函数会就地更新它，调用方负责调用 ``save_summary_cache`` 持久化。
        force: True 时无视缓存重新生成。
    """
    owns_cache = cache is None
    if cache is None:
        cache = load_summary_cache(campaign_parsed_dir)

    if not force:
        existing = cache.get(scene.id)
        if existing and existing.summary:
            return existing

    summary = generate_scene_summary(scene, events_by_id, model_cfg=model_cfg)
    cache[scene.id] = summary

    if owns_cache:
        save_summary_cache(cache, campaign_parsed_dir)
    return summary


def batch_generate_summaries(
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    *,
    campaign_parsed_dir: Path,
    model_cfg: StageLLMConfig,
    force: bool = False,
    progress: callable | None = None,
) -> SceneSummaryCache:
    """批量为多个 scene 生成摘要。每生成一条立刻写盘（断点恢复友好）。

    Args:
        progress: 可选回调 ``progress(idx, total, scene, summary)``，UI 进度条用。
    """
    cache = load_summary_cache(campaign_parsed_dir)
    total = len(scenes)
    for idx, scene in enumerate(scenes, start=1):
        if not force and scene.id in cache and cache[scene.id].summary:
            if progress:
                progress(idx, total, scene, cache[scene.id])
            continue
        summary = generate_scene_summary(scene, events_by_id, model_cfg=model_cfg)
        cache[scene.id] = summary
        save_summary_cache(cache, campaign_parsed_dir)
        if progress:
            progress(idx, total, scene, summary)
    return cache


def list_missing_summaries(
    scenes: Iterable[Scene],
    *,
    campaign_parsed_dir: Path,
) -> list[str]:
    """返回缓存中缺失或标记 needs_review 的 scene_id 列表。"""
    cache = load_summary_cache(campaign_parsed_dir)
    missing: list[str] = []
    for scene in scenes:
        s = cache.get(scene.id)
        if s is None or not s.summary or s.needs_review:
            missing.append(scene.id)
    return missing

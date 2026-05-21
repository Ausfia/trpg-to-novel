"""[7] 章节段落 ↔ 原始事件对齐（Align 阶段）。

用便宜模型把 draft 段落映射到事件 ID，产出 chXX_align.json。
UI 据此在左侧标注"被删除"事件，右键段落时高亮源事件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from jinja2 import Environment, FileSystemLoader

from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.narrate.narrative_feed import build_feed, feed_to_text
from trpg2novel.parse.classify import TaggedEvent
from trpg2novel.segment.scene import Scene

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_jenv = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


@dataclass
class ParagraphMapping:
    index: int
    text: str
    source_event_ids: list[str] = field(default_factory=list)


@dataclass
class AlignmentResult:
    paragraphs: list[ParagraphMapping]
    unmapped_event_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "paragraphs": [
                {"index": p.index, "text": p.text, "source_event_ids": p.source_event_ids}
                for p in self.paragraphs
            ],
            "unmapped_event_ids": self.unmapped_event_ids,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AlignmentResult":
        paras = [
            ParagraphMapping(
                index=p["index"],
                text=p.get("text", ""),
                source_event_ids=list(p.get("source_event_ids", [])),
            )
            for p in d.get("paragraphs", [])
        ]
        return cls(
            paragraphs=paras,
            unmapped_event_ids=list(d.get("unmapped_event_ids", [])),
        )


def align_paragraphs_to_events(
    draft_text: str,
    scenes: Sequence[Scene],
    events_by_id: dict[str, TaggedEvent],
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> AlignmentResult:
    """用 LLM 把 draft 段落对齐到原始事件 ID。"""
    feed = build_feed(scenes, events_by_id)
    feed_text = feed_to_text(feed, include_roll_outcomes=True, include_event_ids=True)

    # 把 draft 按空行分段
    raw_paragraphs = [p.strip() for p in draft_text.split("\n\n") if p.strip()]

    system_prompt = (_PROMPTS_DIR / "align_system.txt").read_text(encoding="utf-8")
    user_tmpl = _jenv.get_template("align_user.j2")
    user_prompt = user_tmpl.render(
        paragraph_count=len(raw_paragraphs),
        draft_text="\n\n".join(
            f"[{i}]\n{p}" for i, p in enumerate(raw_paragraphs)
        ),
        feed_text_with_ids=feed_text,
    )

    # feed 长度限制：LLM 上下文可能有限，截断素材到 8000 字符
    if len(feed_text) > 8000:
        feed_text = feed_text[:8000] + "\n[...已截断，仅取前 8000 字...]"

    client = make_client(api_key, base_url)
    raw = chat_json(client, model, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    # 解析 LLM 输出
    para_maps: list[ParagraphMapping] = []
    for item in raw.get("paragraphs", []):
        idx = item.get("index", 0)
        text = raw_paragraphs[idx] if idx < len(raw_paragraphs) else ""
        para_maps.append(ParagraphMapping(
            index=idx,
            text=text,
            source_event_ids=list(item.get("source_event_ids", [])),
        ))
    # 补齐没有映射的段落
    mapped_indices = {p.index for p in para_maps}
    for i, para in enumerate(raw_paragraphs):
        if i not in mapped_indices:
            para_maps.append(ParagraphMapping(index=i, text=para, source_event_ids=[]))
    para_maps.sort(key=lambda p: p.index)

    unmapped = list(raw.get("unmapped_event_ids", []))

    return AlignmentResult(paragraphs=para_maps, unmapped_event_ids=unmapped)


def save_alignment(result: AlignmentResult, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_alignment(align_path: Path) -> AlignmentResult | None:
    if not align_path.exists():
        return None
    d = json.loads(align_path.read_text(encoding="utf-8"))
    return AlignmentResult.from_dict(d)

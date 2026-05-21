"""Worldview — 系统模板 + 团自定义文本 的组合视图。

每个 Campaign 都有一个 ``system``（如 ``dnd5e``），系统级模板存于
``data/systems/<system>/worldview_template.yaml``，定义禁词、骰子演绎规则、
风格基调。团目录下的 ``worldview.md`` 是自由文本（背景、势力、地点），
作为 ``custom_lore`` 注入到起草 prompt。

未来扩展（CoC / Cyberpunk）只需新增 ``data/systems/<system>/worldview_template.yaml``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from trpg2novel.config import SYSTEMS_DIR


@dataclass
class Worldview:
    """合并后的世界观视图：系统模板 + 团自定义。"""

    system: str
    display_name: str
    banned_words: list[str] = field(default_factory=list)
    roll_translation_rules: dict[str, str] = field(default_factory=dict)
    prose_style: dict[str, Any] = field(default_factory=dict)
    card_injection_principle: str = ""
    custom_lore: str = ""

    @property
    def reference_authors(self) -> list[str]:
        return list(self.prose_style.get("reference_authors") or [])


def _load_system_template(system: str) -> dict[str, Any]:
    path = SYSTEMS_DIR / system / "worldview_template.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"找不到系统模板：{path}。请确认 data/systems/{system}/ 目录存在。"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merge_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """浅合并：overrides 覆盖 base 同名 key；嵌套 dict 做一层 merge。"""
    out = dict(base)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def load_worldview(
    system: str,
    *,
    custom_lore_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Worldview:
    """加载 worldview。

    Args:
        system: 系统标识（如 ``dnd5e``）。
        custom_lore_path: 团目录下的 ``worldview.md``（可选）。
        overrides: campaign.yaml 里的 ``worldview_overrides`` 字段（可选）。
    """
    data = _load_system_template(system)
    if overrides:
        data = _merge_overrides(data, overrides)

    custom_lore = ""
    if custom_lore_path and custom_lore_path.exists():
        custom_lore = custom_lore_path.read_text(encoding="utf-8").strip()

    return Worldview(
        system=data.get("system", system),
        display_name=data.get("display_name", system),
        banned_words=list(data.get("banned_words") or []),
        roll_translation_rules=dict(data.get("roll_translation_rules") or {}),
        prose_style=dict(data.get("prose_style") or {}),
        card_injection_principle=data.get("card_injection_principle", ""),
        custom_lore=custom_lore,
    )


def load_worldview_for_campaign(campaign) -> Worldview:
    """便捷入口：基于 ``Campaign`` 对象组装 Worldview。"""
    overrides: dict[str, Any] = {}
    if campaign.campaign_yaml.exists():
        raw = yaml.safe_load(campaign.campaign_yaml.read_text(encoding="utf-8")) or {}
        overrides = raw.get("worldview_overrides") or {}
    return load_worldview(
        campaign.system,
        custom_lore_path=campaign.worldview_md,
        overrides=overrides,
    )

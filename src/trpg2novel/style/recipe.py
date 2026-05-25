"""Style recipe loading for literary polish."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from trpg2novel.config import DATA_DIR


STYLE_RECIPES_DIR = DATA_DIR / "style_recipes"


@dataclass
class StyleRecipe:
    name: str = "default"
    description: str = ""
    narrative: dict[str, Any] = field(default_factory=dict)
    prose_style: dict[str, Any] = field(default_factory=dict)
    themes: list[str] = field(default_factory=list)
    tropes_to_embrace: list[str] = field(default_factory=list)
    tropes_to_avoid: list[str] = field(default_factory=list)
    dialogue_style: dict[str, Any] = field(default_factory=dict)
    combat_style: dict[str, Any] = field(default_factory=dict)
    forbidden: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StyleRecipe":
        return cls(
            name=str(data.get("name") or "default"),
            description=str(data.get("description") or ""),
            narrative=dict(data.get("narrative") or {}),
            prose_style=dict(data.get("prose_style") or {}),
            themes=list(data.get("themes") or []),
            tropes_to_embrace=list(data.get("tropes_to_embrace") or []),
            tropes_to_avoid=list(data.get("tropes_to_avoid") or []),
            dialogue_style=dict(data.get("dialogue_style") or {}),
            combat_style=dict(data.get("combat_style") or {}),
            forbidden=list(data.get("forbidden") or []),
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "narrative": self.narrative,
            "prose_style": self.prose_style,
            "themes": self.themes,
            "tropes_to_embrace": self.tropes_to_embrace,
            "tropes_to_avoid": self.tropes_to_avoid,
            "dialogue_style": self.dialogue_style,
            "combat_style": self.combat_style,
            "forbidden": self.forbidden,
        }


def list_style_recipes(*, campaign=None) -> list[Path]:
    paths: list[Path] = []
    if STYLE_RECIPES_DIR.exists():
        paths.extend(sorted(STYLE_RECIPES_DIR.glob("*.yaml")))
        paths.extend(sorted(STYLE_RECIPES_DIR.glob("*.yml")))
    if campaign is not None and getattr(campaign, "style_recipe_yaml", None):
        if campaign.style_recipe_yaml.exists():
            paths.append(campaign.style_recipe_yaml)
    return paths


def _resolve_recipe_path(name_or_path: str | Path | None, *, campaign=None) -> Path | None:
    if name_or_path:
        raw = Path(str(name_or_path))
        if raw.exists():
            return raw
        candidates = [STYLE_RECIPES_DIR / str(name_or_path)]
        if raw.suffix not in {".yaml", ".yml"}:
            candidates.extend([
                STYLE_RECIPES_DIR / f"{name_or_path}.yaml",
                STYLE_RECIPES_DIR / f"{name_or_path}.yml",
            ])
        if campaign is not None:
            candidates.extend([
                campaign.root / str(name_or_path),
                campaign.root / f"{name_or_path}.yaml",
                campaign.root / f"{name_or_path}.yml",
            ])
        for candidate in candidates:
            if candidate.exists():
                return candidate

    if campaign is not None:
        if getattr(campaign, "style_recipe_yaml", None) and campaign.style_recipe_yaml.exists():
            return campaign.style_recipe_yaml
        if campaign.campaign_yaml.exists():
            data = yaml.safe_load(campaign.campaign_yaml.read_text(encoding="utf-8")) or {}
            recipe_name = (data.get("style") or {}).get("recipe") or data.get("style_recipe")
            if recipe_name:
                return _resolve_recipe_path(str(recipe_name), campaign=campaign)
    return None


def load_style_recipe(name_or_path: str | Path | None = None, *, campaign=None) -> StyleRecipe:
    path = _resolve_recipe_path(name_or_path, campaign=campaign)
    if path is None:
        return StyleRecipe()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StyleRecipe.from_dict(data)

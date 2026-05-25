"""Style profile support for user-editable literary polish settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from trpg2novel.style.recipe import StyleRecipe, load_style_recipe


@dataclass
class StyleProfile:
    id: str = "default"
    name: str = "默认风格方案"
    description: str = ""
    user_brief: str = ""
    style_summary: str = ""
    pov_summary: str = ""
    prose_summary: str = ""
    dialogue_summary: str = ""
    action_summary: str = ""
    avoid_summary: str = ""
    narrative: dict[str, Any] = field(default_factory=dict)
    prose_style: dict[str, Any] = field(default_factory=dict)
    themes: list[str] = field(default_factory=list)
    tropes_to_embrace: list[str] = field(default_factory=list)
    tropes_to_avoid: list[str] = field(default_factory=list)
    dialogue_style: dict[str, Any] = field(default_factory=dict)
    combat_style: dict[str, Any] = field(default_factory=dict)
    forbidden: list[str] = field(default_factory=list)
    style_kb: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StyleProfile":
        return cls(
            id=_slug(str(data.get("id") or data.get("name") or "default")),
            name=str(data.get("name") or "默认风格方案"),
            description=str(data.get("description") or ""),
            user_brief=str(data.get("user_brief") or ""),
            style_summary=str(data.get("style_summary") or ""),
            pov_summary=str(data.get("pov_summary") or ""),
            prose_summary=str(data.get("prose_summary") or ""),
            dialogue_summary=str(data.get("dialogue_summary") or ""),
            action_summary=str(data.get("action_summary") or ""),
            avoid_summary=str(data.get("avoid_summary") or ""),
            narrative=dict(data.get("narrative") or {}),
            prose_style=dict(data.get("prose_style") or {}),
            themes=list(data.get("themes") or []),
            tropes_to_embrace=list(data.get("tropes_to_embrace") or []),
            tropes_to_avoid=list(data.get("tropes_to_avoid") or []),
            dialogue_style=dict(data.get("dialogue_style") or {}),
            combat_style=dict(data.get("combat_style") or {}),
            forbidden=list(data.get("forbidden") or []),
            style_kb=dict(data.get("style_kb") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "user_brief": self.user_brief,
            "style_summary": self.style_summary,
            "pov_summary": self.pov_summary,
            "prose_summary": self.prose_summary,
            "dialogue_summary": self.dialogue_summary,
            "action_summary": self.action_summary,
            "avoid_summary": self.avoid_summary,
            "narrative": self.narrative,
            "prose_style": self.prose_style,
            "themes": self.themes,
            "tropes_to_embrace": self.tropes_to_embrace,
            "tropes_to_avoid": self.tropes_to_avoid,
            "dialogue_style": self.dialogue_style,
            "combat_style": self.combat_style,
            "forbidden": self.forbidden,
            "style_kb": self.style_kb,
        }

    def to_prompt_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @property
    def use_style_kb(self) -> bool:
        return bool(self.style_kb.get("enabled", True))

    @property
    def style_kb_top_k(self) -> int | None:
        value = self.style_kb.get("top_k")
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None


def _summary_from_mapping(value: dict[str, Any]) -> str:
    if not value:
        return ""
    return "；".join(f"{k}: {v}" for k, v in value.items())


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "default"


def profile_from_recipe(recipe: StyleRecipe, *, profile_id: str | None = None) -> StyleProfile:
    return StyleProfile(
        id=_slug(profile_id or recipe.name or "default"),
        name=recipe.name or "默认风格方案",
        description=recipe.description,
        user_brief=recipe.description,
        style_summary=recipe.description or f"{recipe.name} 风格方案",
        pov_summary=_summary_from_mapping(recipe.narrative),
        prose_summary=_summary_from_mapping(recipe.prose_style),
        dialogue_summary=_summary_from_mapping(recipe.dialogue_style),
        action_summary=_summary_from_mapping(recipe.combat_style),
        avoid_summary="；".join(recipe.forbidden),
        narrative=dict(recipe.narrative),
        prose_style=dict(recipe.prose_style),
        themes=list(recipe.themes),
        tropes_to_embrace=list(recipe.tropes_to_embrace),
        tropes_to_avoid=list(recipe.tropes_to_avoid),
        dialogue_style=dict(recipe.dialogue_style),
        combat_style=dict(recipe.combat_style),
        forbidden=list(recipe.forbidden),
        style_kb={"enabled": True, "top_k": None},
    )


def profile_to_prompt_dict(profile: StyleProfile | None) -> dict[str, Any]:
    return profile.to_prompt_dict() if profile is not None else {}


def ensure_default_style_profile(campaign) -> Path:
    campaign.style_profiles_dir.mkdir(parents=True, exist_ok=True)
    default_path = campaign.default_style_profile_yaml
    if not default_path.exists():
        recipe = load_style_recipe("heroic_fantasy_drama", campaign=campaign)
        profile = profile_from_recipe(recipe, profile_id="default")
        profile.id = "default"
        if profile.name == "heroic_fantasy_drama":
            profile.name = "英雄奇幻剧情向"
        save_style_profile(profile, campaign=campaign)
    return default_path


def list_style_profiles(campaign) -> list[Path]:
    ensure_default_style_profile(campaign)
    paths = sorted(campaign.style_profiles_dir.glob("*.yaml")) + sorted(campaign.style_profiles_dir.glob("*.yml"))
    return sorted(paths, key=lambda p: (p.stem != "default", p.stem))


def _resolve_profile_path(name_or_path: str | Path | None, *, campaign) -> Path | None:
    ensure_default_style_profile(campaign)
    if name_or_path:
        raw = Path(str(name_or_path))
        if raw.exists():
            return raw
        candidates = [campaign.style_profiles_dir / str(name_or_path)]
        if raw.suffix not in {".yaml", ".yml"}:
            candidates.extend([
                campaign.style_profiles_dir / f"{name_or_path}.yaml",
                campaign.style_profiles_dir / f"{name_or_path}.yml",
            ])
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return campaign.default_style_profile_yaml if campaign.default_style_profile_yaml.exists() else None


def load_style_profile(name_or_path: str | Path | None = None, *, campaign) -> StyleProfile:
    path = _resolve_profile_path(name_or_path, campaign=campaign)
    if path is None:
        return StyleProfile()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile = StyleProfile.from_dict(data)
    if not profile.id:
        profile.id = _slug(path.stem)
    return profile


def save_style_profile(profile: StyleProfile, *, campaign) -> Path:
    campaign.style_profiles_dir.mkdir(parents=True, exist_ok=True)
    profile.id = _slug(profile.id or profile.name)
    path = campaign.style_profiles_dir / f"{profile.id}.yaml"
    path.write_text(
        yaml.safe_dump(profile.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path

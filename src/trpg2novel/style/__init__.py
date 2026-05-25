"""Style support for literary rewrite stages."""

from trpg2novel.style.profile import (
    StyleProfile,
    ensure_default_style_profile,
    list_style_profiles,
    load_style_profile,
    profile_from_recipe,
    profile_to_prompt_dict,
    save_style_profile,
)
from trpg2novel.style.recipe import StyleRecipe, list_style_recipes, load_style_recipe

__all__ = [
    "StyleProfile",
    "StyleRecipe",
    "ensure_default_style_profile",
    "generate_style_profile_draft",
    "list_style_profiles",
    "list_style_recipes",
    "load_style_profile",
    "load_style_recipe",
    "profile_from_recipe",
    "profile_to_prompt_dict",
    "save_style_profile",
]


def __getattr__(name: str):
    if name == "generate_style_profile_draft":
        from trpg2novel.style.generator import generate_style_profile_draft
        return generate_style_profile_draft
    raise AttributeError(name)

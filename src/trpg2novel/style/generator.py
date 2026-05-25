"""Generate user-editable style profiles with an LLM."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from trpg2novel.config import StageLLMConfig
from trpg2novel.llm.client import chat_json, make_client
from trpg2novel.style.profile import StyleProfile

if TYPE_CHECKING:
    from trpg2novel.rag.store import RetrievedChunk

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "llm" / "prompts"
_jenv = Environment(loader=FileSystemLoader(str(_PROMPTS_DIR)))


def generate_style_profile_draft(
    *,
    profile: StyleProfile,
    user_brief: str,
    style_references: list["RetrievedChunk"] | None,
    model_cfg: StageLLMConfig,
) -> StyleProfile:
    system_tmpl = _jenv.get_template("style_profile_system.j2")
    user_tmpl = _jenv.get_template("style_profile_user.j2")
    system_prompt = system_tmpl.render()
    user_prompt = user_tmpl.render(
        profile=profile,
        user_brief=user_brief,
        style_references=style_references or [],
    )
    client = make_client(model_cfg.api_key, model_cfg.base_url)
    data = chat_json(
        client,
        model_cfg.model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=6000,
    )
    return apply_generated_style_profile(profile, data, user_brief=user_brief)


def apply_generated_style_profile(
    profile: StyleProfile,
    data: dict[str, Any],
    *,
    user_brief: str | None = None,
) -> StyleProfile:
    merged = profile.to_dict()
    if user_brief is not None:
        merged["user_brief"] = user_brief
    for key in (
        "style_summary",
        "pov_summary",
        "prose_summary",
        "dialogue_summary",
        "action_summary",
        "avoid_summary",
    ):
        if data.get(key):
            merged[key] = str(data[key])
    for key in (
        "narrative",
        "prose_style",
        "dialogue_style",
        "combat_style",
    ):
        if isinstance(data.get(key), dict):
            merged[key] = data[key]
    for key in ("themes", "forbidden", "tropes_to_embrace", "tropes_to_avoid"):
        if isinstance(data.get(key), list):
            merged[key] = data[key]
    return StyleProfile.from_dict(merged)

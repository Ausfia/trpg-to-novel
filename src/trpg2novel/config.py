"""统一配置：从环境变量读取 LLM 设置，以及项目层路径。

v2 变更：
- LLM 配置改为 4 阶段独立（detect / draft / polish / review），
  每阶段各自一份 api_key + base_url + model。
- 团相关路径（raw_logs / parsed / chapters / ...）改由 ``trpg2novel.campaign.Campaign`` 提供。
  下面的 ``RAW_LOG_DIR`` 等常量仍存在，但已 **弃用**：
  它们指向迁移后的默认团 ``jl_zheng_zheng``，仅用于让旧调用路径暂时不破。
  新代码请直接持有 ``Campaign`` 对象并用 ``camp.raw_logs_dir`` 等。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 项目层路径
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
CAMPAIGNS_DIR = DATA_DIR / "campaigns"
SYSTEMS_DIR = DATA_DIR / "systems"

# ---- 弃用：团相关常量（指向默认团，便于旧调用兼容）----
_DEFAULT_CAMPAIGN_ID = os.environ.get("DEFAULT_CAMPAIGN_ID", "jl_zheng_zheng")
_DEFAULT_CAMPAIGN_ROOT = CAMPAIGNS_DIR / _DEFAULT_CAMPAIGN_ID

RAW_LOG_DIR = _DEFAULT_CAMPAIGN_ROOT / "raw_logs"
CHARACTER_CARD_DIR = _DEFAULT_CAMPAIGN_ROOT / "character_cards"
PARSED_DIR = _DEFAULT_CAMPAIGN_ROOT / "parsed"
PENDING_DIR = _DEFAULT_CAMPAIGN_ROOT / "pending"
CHAPTERS_DIR = _DEFAULT_CAMPAIGN_ROOT / "chapters"
# META_DIR 在 v1 是 data/meta/，v2 直接放在 campaign 根：以根目录代之
META_DIR = _DEFAULT_CAMPAIGN_ROOT


# ---------------------------------------------------------------------------
# LLM 配置：5 阶段独立
# ---------------------------------------------------------------------------

STAGE_NAMES = ("detect", "draft", "polish", "review")

# 默认值（按阶段推荐：检测/审稿用便宜模型；起草/润色用强模型）
_STAGE_DEFAULTS = {
    "detect": "deepseek-chat",
    "draft": "deepseek-reasoner",
    "polish": "deepseek-reasoner",
    "review": "deepseek-chat",
}

_LEGACY_MODEL_ENV = {
    "detect": "STAGE_MODEL_CHAPTER_DETECT",
    "draft": "STAGE_MODEL_DRAFT",
    "polish": "STAGE_MODEL_POLISH",
    "review": "STAGE_MODEL_REVIEW",
}


@dataclass(frozen=True)
class StageLLMConfig:
    """单一阶段的 LLM 配置。"""

    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class PolishLLMSettings:
    """Polish 内部分步骤 LLM 配置。"""

    rewrite: StageLLMConfig
    check: StageLLMConfig


@dataclass(frozen=True)
class LLMSettings:
    """4 阶段独立的 LLM 设置。"""

    detect: StageLLMConfig
    draft: StageLLMConfig
    polish: StageLLMConfig
    review: StageLLMConfig
    polish_workflow: PolishLLMSettings

    def for_stage(self, stage: str) -> StageLLMConfig:
        if stage not in STAGE_NAMES:
            raise ValueError(f"未知阶段：{stage}，应为 {STAGE_NAMES}")
        return getattr(self, stage)

    # ---- 向后兼容（旧代码读 .api_key / .base_url / .model_xxx）----
    @property
    def api_key(self) -> str:
        return self.draft.api_key

    @property
    def base_url(self) -> str:
        return self.draft.base_url

    @property
    def model_chapter_detect(self) -> str:
        return self.detect.model

    @property
    def model_draft(self) -> str:
        return self.draft.model

    @property
    def model_review(self) -> str:
        return self.review.model

    @property
    def model_segment(self) -> str:
        # v1 兼容：segment 阶段已不再用 LLM
        return os.environ.get("STAGE_MODEL_SEGMENT", "deepseek-chat")


def _stage_env(stage: str) -> StageLLMConfig:
    """读单个阶段的配置：优先 LLM_<STAGE>_*，回退到全局 OPENAI_*。"""
    upper = stage.upper()
    api_key = os.environ.get(f"LLM_{upper}_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base_url = (
        os.environ.get(f"LLM_{upper}_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.deepseek.com/v1"
    )
    model = (
        os.environ.get(f"LLM_{upper}_MODEL")
        or os.environ.get(_LEGACY_MODEL_ENV[stage], "")
        or _STAGE_DEFAULTS[stage]
    )
    return StageLLMConfig(api_key=api_key, base_url=base_url, model=model)


def _polish_substage_env(name: str, fallback: StageLLMConfig) -> StageLLMConfig:
    """读 polish 子步骤配置；未设置时继承 LLM_POLISH_*。"""
    upper = f"POLISH_{name.upper()}"
    api_key = os.environ.get(f"LLM_{upper}_API_KEY") or fallback.api_key
    base_url = os.environ.get(f"LLM_{upper}_BASE_URL") or fallback.base_url
    model = os.environ.get(f"LLM_{upper}_MODEL") or fallback.model
    return StageLLMConfig(api_key=api_key, base_url=base_url, model=model)


def load_llm_settings() -> LLMSettings:
    polish = _stage_env("polish")
    review = _stage_env("review")
    polish_workflow = PolishLLMSettings(
        rewrite=_polish_substage_env("rewrite", polish),
        check=_polish_substage_env("check", review),
    )
    return LLMSettings(
        detect=_stage_env("detect"),
        draft=_stage_env("draft"),
        polish=polish,
        review=review,
        polish_workflow=polish_workflow,
    )


# ---------------------------------------------------------------------------
# 其它运行时常量
# ---------------------------------------------------------------------------

TARGET_EXPANSION_RATIO = float(os.environ.get("TARGET_EXPANSION_RATIO", "2.5"))
SCENE_GAP_MINUTES = int(os.environ.get("SCENE_GAP_MINUTES", "5"))

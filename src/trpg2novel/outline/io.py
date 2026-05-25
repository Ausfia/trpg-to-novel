"""Outline IO — campaign / volume YAML 读写 + 历史快照。

目录约定：
    data/campaigns/<id>/outline/
    ├── campaign.yaml
    └── volumes/
        ├── vol01.yaml                # confirmed 后的稳定版
        ├── vol01.draft.yaml          # 用户编辑中的草稿
        └── vol01.history/<ts>.yaml   # 每次保存前的快照
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import yaml

from trpg2novel.campaign import Campaign
from trpg2novel.outline.schema import CampaignOutline, VolumeOutline


# ---------------------------------------------------------------------------
# 路径计算
# ---------------------------------------------------------------------------


def outline_dir(camp: Campaign) -> Path:
    return camp.root / "outline"


def campaign_outline_yaml(camp: Campaign) -> Path:
    return outline_dir(camp) / "campaign.yaml"


def volumes_dir(camp: Campaign) -> Path:
    return outline_dir(camp) / "volumes"


def volume_yaml_path(camp: Campaign, volume_index: int, *, draft: bool = False) -> Path:
    suffix = ".draft.yaml" if draft else ".yaml"
    return volumes_dir(camp) / f"vol{volume_index:02d}{suffix}"


def volume_history_dir(camp: Campaign, volume_index: int) -> Path:
    return volumes_dir(camp) / f"vol{volume_index:02d}.history"


def ensure_outline_dirs(camp: Campaign) -> None:
    outline_dir(camp).mkdir(parents=True, exist_ok=True)
    volumes_dir(camp).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Campaign 大纲
# ---------------------------------------------------------------------------


def load_campaign_outline(camp: Campaign) -> CampaignOutline | None:
    """文件不存在返回 None，调用方决定是否要触发 generate。"""
    p = campaign_outline_yaml(camp)
    if not p.exists():
        return None
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return CampaignOutline.from_dict(raw)


def save_campaign_outline(
    camp: Campaign,
    outline: CampaignOutline,
    *,
    snapshot: bool = True,
) -> Path:
    """写盘前自动 stamp last_updated_at，并把旧版本归档到 history。"""
    ensure_outline_dirs(camp)
    p = campaign_outline_yaml(camp)

    if snapshot and p.exists():
        history_dir = outline_dir(camp) / "campaign.history"
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(p, history_dir / f"{ts}.yaml")

    outline.last_updated_at = datetime.now().isoformat(timespec="seconds")
    p.write_text(
        yaml.dump(outline.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Volume 大纲
# ---------------------------------------------------------------------------


def load_volume_outline(
    camp: Campaign,
    volume_index: int,
    *,
    prefer_draft: bool = False,
) -> VolumeOutline | None:
    """优先级：
    - prefer_draft=True：先 .draft.yaml，再 .yaml
    - prefer_draft=False：先 .yaml，再 .draft.yaml

    都不存在返回 None。
    """
    final_p = volume_yaml_path(camp, volume_index, draft=False)
    draft_p = volume_yaml_path(camp, volume_index, draft=True)

    candidates = [draft_p, final_p] if prefer_draft else [final_p, draft_p]
    for p in candidates:
        if p.exists():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return VolumeOutline.from_dict(raw)
    return None


def save_volume_outline(
    camp: Campaign,
    outline: VolumeOutline,
    *,
    as_draft: bool,
    snapshot: bool = True,
) -> Path:
    """保存某卷大纲。

    Args:
        as_draft: True → 写 ``vol{NN}.draft.yaml``；False → 写 ``vol{NN}.yaml``。
        snapshot: 写盘前若同名文件存在，先复制到 ``vol{NN}.history/<ts>.yaml``。
    """
    ensure_outline_dirs(camp)
    p = volume_yaml_path(camp, outline.volume_index, draft=as_draft)

    if snapshot and p.exists():
        history = volume_history_dir(camp, outline.volume_index)
        history.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = "draft" if as_draft else "final"
        shutil.copy2(p, history / f"{ts}_{suffix}.yaml")

    outline.last_updated_at = datetime.now().isoformat(timespec="seconds")
    p.write_text(
        yaml.dump(outline.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return p


def list_volumes(camp: Campaign) -> list[int]:
    """返回所有有 .yaml 或 .draft.yaml 的卷号（去重）。"""
    d = volumes_dir(camp)
    if not d.exists():
        return []
    indices: set[int] = set()
    for p in d.glob("vol*.yaml"):
        # vol01.yaml / vol01.draft.yaml
        stem = p.stem.replace(".draft", "")  # vol01.draft -> vol01
        if stem.startswith("vol"):
            try:
                indices.add(int(stem[3:]))
            except ValueError:
                continue
    return sorted(indices)


def promote_draft_to_final(camp: Campaign, volume_index: int) -> Path:
    """把 ``vol{NN}.draft.yaml`` 升级为 ``vol{NN}.yaml``（confirm 时调用）。

    会创建一份 history 快照；draft 文件保留（便于继续编辑），但 status 应由
    调用方在调用前设为 ``confirmed``。
    """
    draft_p = volume_yaml_path(camp, volume_index, draft=True)
    if not draft_p.exists():
        raise FileNotFoundError(f"找不到草稿：{draft_p}")
    draft = load_volume_outline(camp, volume_index, prefer_draft=True)
    assert draft is not None
    return save_volume_outline(camp, draft, as_draft=False, snapshot=True)

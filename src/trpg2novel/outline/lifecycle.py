"""Outline lifecycle — VolumeRecord / PendingScenePool 状态机与 scene 归属管理。

scene 归属四象限：
| scene 状态                              | 来源                                      |
| --------------------------------------- | ----------------------------------------- |
| state.processed_scene_ids               | 已 confirm（含 closed）卷的 scenes        |
| state.volumes[].scene_ids（未 confirm） | propose 后未 confirm 的卷                 |
| state.pending_pool.scene_ids            | LLM 判定不足成卷                          |
| 未提议                                   | 新上传，下次 propose 一并评估             |

PR1b 仅暴露：
- ``classify_scenes``：把全量 scene 列表按四象限分组
- ``register_volume_draft / promote_to_confirmed / mark_drafting / mark_closed``：
  推进 ``VolumeRecord.status`` 并维护 state 引用一致性
- ``set_pending_pool / clear_pending_pool``：维护 pending 池
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from trpg2novel.state.story_state import (
    PendingScenePool,
    StoryState,
    VolumeRecord,
)

# 卷生命周期合法状态
_VOLUME_STATES = ("proposed", "draft", "confirmed", "drafting", "closed")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# scene 四象限分类
# ---------------------------------------------------------------------------


def classify_scenes(
    all_scene_ids: Iterable[str],
    state: StoryState,
) -> dict[str, list[str]]:
    """返回四象限分类。

    输出 keys：
        ``processed`` / ``in_draft_volumes`` / ``pending`` / ``unproposed``
    """
    all_ids = list(all_scene_ids)
    processed = set(state.processed_scene_ids or [])
    in_drafts: set[str] = set()
    for v in state.volumes or []:
        # confirm 之后 scene 应该已经在 processed_scene_ids；这里只看仍是 proposed/draft 的
        if v.status in ("proposed", "draft"):
            in_drafts.update(v.scene_ids or [])
    pending = set(state.pending_pool.scene_ids if state.pending_pool else [])

    out = {
        "processed": [],
        "in_draft_volumes": [],
        "pending": [],
        "unproposed": [],
    }
    for sid in all_ids:
        if sid in processed:
            out["processed"].append(sid)
        elif sid in in_drafts:
            out["in_draft_volumes"].append(sid)
        elif sid in pending:
            out["pending"].append(sid)
        else:
            out["unproposed"].append(sid)
    return out


def remaining_scene_ids_for_next_volume(
    all_scene_ids: Iterable[str],
    state: StoryState,
) -> list[str]:
    """``all - processed - scenes_in_any_uncomitted_volume``。"""
    classification = classify_scenes(all_scene_ids, state)
    # pending 也可作为下一卷的候选（用户主动 propose 时一起评估）
    return classification["pending"] + classification["unproposed"]


# ---------------------------------------------------------------------------
# VolumeRecord 推进
# ---------------------------------------------------------------------------


def find_volume(state: StoryState, volume_index: int) -> VolumeRecord | None:
    for v in state.volumes:
        if v.volume_index == volume_index:
            return v
    return None


def next_volume_index(state: StoryState) -> int:
    """返回下一个可用的卷号（max+1，从 1 开始）。"""
    if not state.volumes:
        return 1
    return max(v.volume_index for v in state.volumes) + 1


def register_volume_draft(
    state: StoryState,
    *,
    volume_index: int,
    scene_ids: list[str],
    outline_path: str,
    proposal_reasoning: str = "",
    status: str = "draft",
) -> VolumeRecord:
    """新建或更新一个 VolumeRecord（status = proposed/draft）。

    若已存在同号卷且 status 为 confirmed/drafting/closed，拒绝覆盖（避免误改）。
    """
    if status not in _VOLUME_STATES:
        raise ValueError(f"非法状态：{status}")

    rec = find_volume(state, volume_index)
    if rec is not None:
        if rec.status in ("confirmed", "drafting", "closed"):
            raise RuntimeError(
                f"卷 {volume_index} 当前 status={rec.status}，禁止回退到 {status}"
            )
        rec.scene_ids = list(scene_ids)
        rec.outline_path = outline_path
        rec.proposal_reasoning = proposal_reasoning or rec.proposal_reasoning
        rec.status = status
        return rec

    rec = VolumeRecord(
        volume_index=volume_index,
        status=status,
        outline_path=outline_path,
        skeleton_path=None,
        scene_ids=list(scene_ids),
        chapter_indices=[],
        word_count=None,
        confirmed_at=None,
        closed_at=None,
        proposal_reasoning=proposal_reasoning,
    )
    state.volumes.append(rec)
    state.volumes.sort(key=lambda v: v.volume_index)
    return rec


def promote_to_confirmed(
    state: StoryState,
    volume_index: int,
) -> VolumeRecord:
    """把卷从 draft → confirmed，并把卷 scenes 写入 ``processed_scene_ids``。

    幂等：若已经 confirmed/drafting/closed，直接返回（不重复写 processed）。
    """
    rec = find_volume(state, volume_index)
    if rec is None:
        raise FileNotFoundError(f"找不到卷 {volume_index}")
    if rec.status in ("confirmed", "drafting", "closed"):
        return rec
    if rec.status not in ("proposed", "draft"):
        raise RuntimeError(f"非法状态转移：{rec.status} → confirmed")

    # 校验：本卷 scene 不能已经在 processed 中（否则有跨卷重叠）
    overlap = set(rec.scene_ids) & set(state.processed_scene_ids or [])
    if overlap:
        raise RuntimeError(
            f"卷 {volume_index} 的 scenes 与 processed_scene_ids 冲突：{sorted(overlap)}"
        )

    rec.status = "confirmed"
    rec.confirmed_at = _now()
    for sid in rec.scene_ids:
        if sid not in state.processed_scene_ids:
            state.processed_scene_ids.append(sid)
    # 确认后，pending 池里若仍含这些 scene，应清掉
    if state.pending_pool:
        state.pending_pool.scene_ids = [
            s for s in state.pending_pool.scene_ids if s not in set(rec.scene_ids)
        ]
        if not state.pending_pool.scene_ids:
            state.pending_pool = None
    state.current_volume_index = max(state.current_volume_index, volume_index)
    return rec


def mark_drafting(
    state: StoryState,
    volume_index: int,
    *,
    skeleton_path: str,
) -> VolumeRecord:
    rec = find_volume(state, volume_index)
    if rec is None:
        raise FileNotFoundError(f"找不到卷 {volume_index}")
    if rec.status not in ("confirmed", "drafting"):
        raise RuntimeError(f"非法状态转移：{rec.status} → drafting")
    rec.status = "drafting"
    rec.skeleton_path = skeleton_path
    return rec


def mark_closed(
    state: StoryState,
    volume_index: int,
    *,
    chapter_indices: list[int],
    word_count: int | None = None,
) -> VolumeRecord:
    rec = find_volume(state, volume_index)
    if rec is None:
        raise FileNotFoundError(f"找不到卷 {volume_index}")
    if rec.status not in ("drafting", "closed"):
        raise RuntimeError(f"非法状态转移：{rec.status} → closed")
    rec.status = "closed"
    rec.chapter_indices = list(chapter_indices)
    rec.word_count = word_count
    rec.closed_at = _now()
    return rec


def remove_uncommitted_volume(
    state: StoryState,
    volume_index: int,
) -> bool:
    """删除 status=proposed/draft 的卷记录（discard 提议时用）。

    confirmed 及更后状态拒绝删除。返回是否真的删除了。
    """
    rec = find_volume(state, volume_index)
    if rec is None:
        return False
    if rec.status not in ("proposed", "draft"):
        raise RuntimeError(f"卷 {volume_index} 状态为 {rec.status}，禁止删除")
    state.volumes = [v for v in state.volumes if v.volume_index != volume_index]
    return True


# ---------------------------------------------------------------------------
# pending pool
# ---------------------------------------------------------------------------


def set_pending_pool(
    state: StoryState,
    scene_ids: list[str],
    reason: str,
) -> None:
    """覆盖式设置 pending 池。空列表则清空。"""
    if not scene_ids:
        state.pending_pool = None
        return
    state.pending_pool = PendingScenePool(
        scene_ids=list(scene_ids),
        reason=reason,
        last_proposed_at=_now(),
    )


def clear_pending_pool(state: StoryState) -> None:
    state.pending_pool = None

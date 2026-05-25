"""Roster — 人物卡 + session 配置 → 卷阵容快照 / campaign 大纲同步建议。

两个核心函数：

- ``compute_volume_roster``：根据卷的 session 范围 + 人物卡 + session 配置，
  生成 ``VolumeRoster``（active_in_volume / absent_sessions / retiring_in_volume / joining_in_volume）。
  在 ``generate_volume_outline`` 内部调用。

- ``sync_key_characters_from_cards``：扫描人物卡并比对 ``CampaignOutline.key_characters``，
  返回变更建议（不直接改写大纲）。供 ``revise`` 流程消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from trpg2novel.character.card_loader import CharacterCard
from trpg2novel.outline.schema import (
    CampaignOutline,
    JoiningCharacter,
    KeyCharacter,
    RetiringCharacter,
    VolumeRoster,
)
from trpg2novel.parse.classify import SessionConfig


# ---------------------------------------------------------------------------
# session 顺序工具
# ---------------------------------------------------------------------------


def _session_index(sid: str, ordered_sessions: list[str]) -> int | None:
    """返回 sid 在 ordered_sessions 中的下标；找不到返回 None。"""
    try:
        return ordered_sessions.index(sid)
    except ValueError:
        return None


def _is_left_strictly_before(card: CharacterCard, sid: str, ordered_sessions: list[str]) -> bool:
    """角色是否在 sid 之前严格已退团（sid 当场不在）。"""
    if not card.left_after_session:
        return False
    left_idx = _session_index(card.left_after_session, ordered_sessions)
    here_idx = _session_index(sid, ordered_sessions)
    if left_idx is None or here_idx is None:
        return False
    return left_idx < here_idx


def _absent_reason(session_cfg: SessionConfig, name: str) -> str | None:
    """从 SessionConfig.absent_players 中提取角色缺席原因（如带括号注记）。

    格式约定：``"诺菲雅（在神殿闭关）"`` 或纯名 ``"诺菲雅"``。
    """
    for entry in session_cfg.absent_players:
        if not entry:
            continue
        bare = entry.split("（")[0].split("(")[0].strip()
        if bare == name:
            return entry
    return None


def _first_appearance_session(
    name: str,
    cards: dict[str, CharacterCard],
    session_cfgs: dict[str, SessionConfig],
    ordered_sessions: list[str],
) -> str | None:
    """根据 session 配置推断角色第一次入场的 session_id。"""
    if name not in cards:
        return None
    if not session_cfgs:
        return None
    for sid in ordered_sessions:
        cfg = session_cfgs.get(sid)
        if cfg is None:
            continue
        if _absent_reason(cfg, name) is None:
            return sid
    return ordered_sessions[0] if ordered_sessions else None



# ---------------------------------------------------------------------------
# compute_volume_roster
# ---------------------------------------------------------------------------


def compute_volume_roster(
    session_ids: list[str],
    cards: dict[str, CharacterCard],
    session_configs: dict[str, SessionConfig],
    *,
    all_session_ids_ordered: list[str] | None = None,
    campaign_outline: CampaignOutline | None = None,
) -> VolumeRoster:
    """根据卷覆盖的 session 范围，计算卷阵容快照。"""
    ordered_all = list(all_session_ids_ordered or session_ids)
    vol_sids = list(session_ids)

    active: list[str] = []
    for name, card in cards.items():
        any_active_in_vol = False
        for sid in vol_sids:
            if not _is_left_strictly_before(card, sid, ordered_all):
                any_active_in_vol = True
                break
        if any_active_in_vol:
            active.append(name)

    absent_sessions: dict[str, list[str]] = {}
    for sid in vol_sids:
        cfg = session_configs.get(sid)
        if cfg is None:
            continue
        for name in active:
            reason = _absent_reason(cfg, name)
            if reason is None:
                continue
            absent_sessions.setdefault(name, []).append(reason)

    retiring: list[RetiringCharacter] = []
    vol_sid_set = set(vol_sids)
    for name, card in cards.items():
        if card.left_after_session and card.left_after_session in vol_sid_set:
            retiring.append(
                RetiringCharacter(
                    name=name,
                    retire_after_session=card.left_after_session,
                    exit_story=card.exit_story or "",
                )
            )

    joining_first: dict[str, str] = {}
    if campaign_outline is not None:
        for kc in campaign_outline.key_characters:
            if kc.first_appearance_session and kc.first_appearance_session in vol_sid_set:
                joining_first[kc.name] = kc.first_appearance_session
    # 同时在人物卡中查找直接设置了 first_appearance_session 的角色
    for name, card in cards.items():
        if name in joining_first:
            continue
        if card.first_appearance_session and card.first_appearance_session in vol_sid_set:
            joining_first[name] = card.first_appearance_session
    if not joining_first and campaign_outline is None:
        for name in active:
            first_sid = _first_appearance_session(name, cards, session_configs, ordered_all)
            if first_sid and first_sid in vol_sid_set:
                joining_first[name] = first_sid

    joining = [
        JoiningCharacter(name=n, first_appearance_session=s)
        for n, s in joining_first.items()
    ]

    return VolumeRoster(
        active_in_volume=sorted(active),
        absent_sessions=absent_sessions,
        retiring_in_volume=retiring,
        joining_in_volume=joining,
    )


# ---------------------------------------------------------------------------
# sync_key_characters_from_cards
# ---------------------------------------------------------------------------


@dataclass
class RosterChange:
    """一条阵容变更建议。供 revise 流程展示给用户勾选。"""

    name: str
    change_type: str          # added / status_changed / exit_story_changed / first_appearance_set
    before: dict | None = None
    after: dict | None = None
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "change_type": self.change_type,
            "before": self.before,
            "after": self.after,
            "description": self.description,
        }


def sync_key_characters_from_cards(
    outline: CampaignOutline,
    cards: dict[str, CharacterCard],
    all_session_ids: list[str],
) -> list[RosterChange]:
    """对比人物卡与 outline.key_characters，返回变更建议（不改写 outline）。"""
    changes: list[RosterChange] = []
    by_name = {kc.name: kc for kc in outline.key_characters}

    for name, card in cards.items():
        existing = by_name.get(name)

        if existing is None:
            new_status = "retired" if card.left_after_session else "active"
            changes.append(
                RosterChange(
                    name=name,
                    change_type="added",
                    after={
                        "name": name,
                        "status": new_status,
                        "retired_after_session": card.left_after_session,
                        "exit_story": card.exit_story or "",
                        "first_appearance_session": card.first_appearance_session or _first_appearance_session(name, cards, {}, all_session_ids),
                    },
                    description=(
                        f"人物卡新增：{name}"
                        + (f"（已退团于 {card.left_after_session}）" if card.left_after_session else "")
                    ),
                )
            )
            continue

        if (existing.retired_after_session or None) != (card.left_after_session or None):
            new_status = "retired" if card.left_after_session else "active"
            changes.append(
                RosterChange(
                    name=name,
                    change_type="status_changed",
                    before={
                        "status": existing.status,
                        "retired_after_session": existing.retired_after_session,
                    },
                    after={
                        "status": new_status,
                        "retired_after_session": card.left_after_session,
                    },
                    description=(
                        f"{name} 退团节点变化："
                        f"{existing.retired_after_session or '在团'} → "
                        f"{card.left_after_session or '在团'}"
                    ),
                )
            )

        if (existing.exit_story or "") != (card.exit_story or ""):
            changes.append(
                RosterChange(
                    name=name,
                    change_type="exit_story_changed",
                    before={"exit_story": existing.exit_story or ""},
                    after={"exit_story": card.exit_story or ""},
                    description=f"{name} 离场故事更新",
                )
            )

        # 优先取人物卡的 first_appearance_session，其次从 session config 推断
        card_first = card.first_appearance_session
        inferred_first = card_first or _first_appearance_session(name, cards, {}, all_session_ids)
        if inferred_first and existing.first_appearance_session != inferred_first:
            changes.append(
                RosterChange(
                    name=name,
                    change_type="first_appearance_set",
                    before={"first_appearance_session": existing.first_appearance_session},
                    after={"first_appearance_session": inferred_first},
                    description=f"{name} 首次入场节点推断为 {inferred_first}",
                )
            )

    return changes


def apply_key_character_changes(
    outline: CampaignOutline,
    changes: Iterable[RosterChange],
) -> CampaignOutline:
    """把用户接受的 changes 合并进 outline.key_characters（不改 outline 时间戳，由调用方处理）。"""
    by_name = {kc.name: kc for kc in outline.key_characters}
    for ch in changes:
        if ch.change_type == "added":
            data = ch.after or {}
            outline.key_characters.append(
                KeyCharacter(
                    name=ch.name,
                    status=data.get("status", "active"),
                    retired_after_session=data.get("retired_after_session"),
                    exit_story=data.get("exit_story") or "",
                    first_appearance_session=data.get("first_appearance_session"),
                )
            )
            by_name[ch.name] = outline.key_characters[-1]
        elif ch.change_type == "status_changed":
            kc = by_name.get(ch.name)
            if kc is None:
                continue
            after = ch.after or {}
            kc.status = after.get("status", kc.status)
            kc.retired_after_session = after.get("retired_after_session")
        elif ch.change_type == "exit_story_changed":
            kc = by_name.get(ch.name)
            if kc is None:
                continue
            after = ch.after or {}
            kc.exit_story = after.get("exit_story", kc.exit_story)
        elif ch.change_type == "first_appearance_set":
            kc = by_name.get(ch.name)
            if kc is None:
                continue
            after = ch.after or {}
            kc.first_appearance_session = after.get("first_appearance_session", kc.first_appearance_session)
    return outline

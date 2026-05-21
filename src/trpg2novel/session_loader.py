"""会话与全局玩家配置的 YAML 加载。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from trpg2novel.parse.classify import SessionConfig


@dataclass
class PlayerInfo:
    name: str
    role: str = "pc"
    aliases: list[str] = field(default_factory=list)
    user_is: bool = False


@dataclass
class PlayersConfig:
    players: list[PlayerInfo]
    dm_handle: str
    known_bots: list[str]

    @property
    def pc_names(self) -> list[str]:
        return [p.name for p in self.players if p.role == "pc"]


def load_players(path: Path) -> PlayersConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    players = [PlayerInfo(**p) for p in data.get("players", [])]
    return PlayersConfig(
        players=players,
        dm_handle=data.get("dm", {}).get("handle", ""),
        known_bots=data.get("known_bots", []),
    )


def load_session(path: Path, players_cfg: PlayersConfig | None = None) -> SessionConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # session.player_handles 可以省略——若省则用全局 PC 名单
    player_handles = data.get("player_handles")
    if player_handles is None:
        if players_cfg is None:
            raise ValueError(
                f"{path} 未提供 player_handles，需要 players.yaml 兜底"
            )
        player_handles = players_cfg.pc_names
    return SessionConfig(
        session_id=data["session_id"],
        dm_handle=data["dm_handle"],
        bot_handles=list(data["bot_handles"]),
        player_handles=list(player_handles),
        absent_players=list(data.get("absent_players", [])),
        date=data.get("date"),
    )

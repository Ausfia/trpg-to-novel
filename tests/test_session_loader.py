"""测试 session.yaml / players.yaml 加载。"""

from __future__ import annotations

from pathlib import Path

from trpg2novel.session_loader import load_players, load_session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ROOT = PROJECT_ROOT / "data" / "campaigns" / "jl_zheng_zheng"


def test_load_players():
    cfg = load_players(CAMPAIGN_ROOT / "players.yaml")
    assert cfg.dm_handle == "狗dm"
    assert "诺菲雅" in cfg.pc_names
    assert len(cfg.pc_names) == 6
    nofiyad = next(p for p in cfg.players if p.name == "诺菲雅")
    assert nofiyad.user_is is True
    assert "二阶堂希罗（请看标签❗" in cfg.known_bots


def test_load_session_s01():
    sess = load_session(CAMPAIGN_ROOT / "raw_logs" / "s01.yaml")
    assert sess.session_id == "s01"
    assert sess.dm_handle == "狗dm"
    assert sess.bot_handles == ["二阶堂希罗（请看标签❗"]
    assert "诺菲雅" in sess.player_handles


def test_load_session_s02():
    sess = load_session(CAMPAIGN_ROOT / "raw_logs" / "s02.yaml")
    assert sess.bot_handles == ["青叶摩卡"]


def test_session_source_dispatch():
    sess = load_session(CAMPAIGN_ROOT / "raw_logs" / "s01.yaml")
    assert sess.source_of("狗dm") == "dm"
    assert sess.source_of("二阶堂希罗（请看标签❗") == "bot"
    assert sess.source_of("诺菲雅") == "pc"
    assert sess.source_of("陌生人") == "unknown"

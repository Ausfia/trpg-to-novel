"""测试 card_loader：字段提取 + 原子事实切分。"""

from __future__ import annotations

import pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CARD_PATH = PROJECT_ROOT / "诺菲雅.xlsx"


@pytest.mark.skipif(not CARD_PATH.exists(), reason="xlsx not present")
def test_load_card_basic_fields():
    from trpg2novel.character import load_card
    card = load_card(CARD_PATH)
    assert card.name == "诺菲雅"
    assert card.age == "345"
    assert card.gender == "女"
    assert card.homeland == "星界"
    assert len(card.background_story) > 100
    assert "星界" in card.background_story


@pytest.mark.skipif(not CARD_PATH.exists(), reason="xlsx not present")
def test_load_card_narrative_fields():
    from trpg2novel.character import load_card
    card = load_card(CARD_PATH)
    assert "好奇" in card.personality
    assert "虚无" in card.ideal
    assert "着迷" in card.bond
    assert "语言" in card.flaw or "交流" in card.flaw
    assert "星光" in card.appearance or "皮甲" in card.appearance


@pytest.mark.skipif(not CARD_PATH.exists(), reason="xlsx not present")
def test_atomic_facts_coverage():
    from trpg2novel.character import load_card
    card = load_card(CARD_PATH)
    combined = " ".join(card.atomic_facts)
    assert "星界精灵" in combined
    assert "345" in combined
    assert "皮甲" in combined or "长剑" in combined
    assert "观察者" in combined
    assert "厌倦" in combined or "永恒" in combined
    assertions_count = len(card.atomic_facts)
    assert assertions_count >= 6, f"Expected ≥6 atomic facts, got {assertions_count}"

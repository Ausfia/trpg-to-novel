"""测试 [4] Story State：加载、保存、patch 合并。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from trpg2novel.state import CharacterStatus, StoryState, apply_patch, load_state, save_state


def test_load_empty_state_when_file_missing():
    state = load_state(Path("/nonexistent/path/state.yaml"))
    assert isinstance(state, StoryState)
    assert state.characters == {}
    assert state.lore_unlocked == []


def test_save_and_reload():
    state = StoryState()
    state.characters["雷恩"] = CharacterStatus(alive=True, level=1)
    state.lore_unlocked.append("凡人会死后起死回生")
    state.session_log.append("s01")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.yaml"
        save_state(state, path)
        loaded = load_state(path)

    assert loaded.characters["雷恩"].alive is True
    assert loaded.characters["雷恩"].level == 1
    assert "凡人会死后起死回生" in loaded.lore_unlocked
    assert "s01" in loaded.session_log


def test_apply_patch_character_update():
    state = StoryState()
    state.characters["雷恩"] = CharacterStatus(alive=True, level=1)

    patched = apply_patch(state, {
        "characters": {
            "雷恩": {"alive": False, "level": 2, "conditions": ["力竭-2"]},
        }
    })
    assert patched.characters["雷恩"].alive is False
    assert patched.characters["雷恩"].level == 2
    assert "力竭-2" in patched.characters["雷恩"].conditions
    # original unmodified
    assert state.characters["雷恩"].alive is True


def test_apply_patch_new_character():
    state = StoryState()
    patched = apply_patch(state, {
        "characters": {"比阿特丽丝": {"level": 2}}
    })
    assert "比阿特丽丝" in patched.characters
    assert patched.characters["比阿特丽丝"].level == 2


def test_apply_patch_lore_deduplication():
    state = StoryState(lore_unlocked=["A", "B"])
    patched = apply_patch(state, {"lore_unlocked": ["B", "C"]})
    assert patched.lore_unlocked.count("B") == 1
    assert "C" in patched.lore_unlocked


def test_apply_patch_world():
    state = StoryState()
    patched = apply_patch(state, {
        "world": {
            "locations": {"至绿镇": "被袭击后基本完整"},
            "factions": {"龙巫教": "已撤退"},
        }
    })
    assert patched.world.locations["至绿镇"] == "被袭击后基本完整"
    assert patched.world.factions["龙巫教"] == "已撤退"


def test_cross_session_continuity_scenario():
    """验证核心场景：s01 雷恩死亡 → s02 复活 + 升级。"""
    state = StoryState()
    # s01 结尾：雷恩战死
    state = apply_patch(state, {
        "characters": {"雷恩": {"alive": False, "level": 1, "conditions": []}},
        "session_log": ["s01"],
    })
    assert state.characters["雷恩"].alive is False

    # s02 结尾：雷恩复活，全员升二级，带力竭
    state = apply_patch(state, {
        "characters": {
            "雷恩": {"alive": True, "level": 2, "conditions": ["力竭-2（每长休减1）"]},
            "诺菲雅": {"level": 2},
            "泰洛尔": {"level": 2},
        },
        "lore_unlocked": ["凡人会死后起死回生（牧师复活术）"],
        "session_log": ["s02"],
    })
    assert state.characters["雷恩"].alive is True
    assert state.characters["雷恩"].level == 2
    assert "力竭-2（每长休减1）" in state.characters["雷恩"].conditions
    assert state.characters["诺菲雅"].level == 2
    assert "s01" in state.session_log
    assert "s02" in state.session_log
    assert "凡人会死后起死回生（牧师复活术）" in state.lore_unlocked

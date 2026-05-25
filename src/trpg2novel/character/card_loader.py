"""人物卡 loader — 支持 YAML 表单填写（主路径）与 xlsx 一次性导入。

v2 数据源：WebUI 表单填写，存为 ``<campaign>/character_cards/<name>.yaml``。
v1 兼容：提供 ``import_from_xlsx()`` 将旧 xlsx 转成 YAML 兼容 dict。

公开接口：
    load_card(path)                   → CharacterCard（自动识别 .yaml / .xlsx）
    load_card_yaml(yaml_path)         → CharacterCard
    load_all_cards(cards_dir)         → dict[name, CharacterCard]
    derive_atomic_facts(card)         → list[str]
    import_from_xlsx(xlsx_path)       → dict（可直接 yaml.dump 成卡文件）
    card_to_dict(card)                → dict（CardResult → YAML 序列化）
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class CharacterCard:
    name: str
    # 通用字段（system-agnostic）
    aliases: list[str] = field(default_factory=list)  # 别名（群昵称等），解析时识别
    age: str = ""
    gender: str = ""
    homeland: str = ""
    appearance: str = ""
    personality: str = ""
    ideal: str = ""
    bond: str = ""
    flaw: str = ""
    background_story: str = ""
    # DnD 5e 字段（optional，其他系统可留空）
    race: str = ""
    subrace: str = ""               # 亚种（如：高等精灵 / 木精灵）
    class_name: str = ""            # "class" 是 Python 保留字
    subclass: str = ""              # 子职（如：誓约骑士 / 变形德鲁伊）
    background_class: str = ""      # 背景（遮荫者/远行者 etc.）
    background_feature: str = ""
    languages: list[str] = field(default_factory=list)
    # WebUI 直接填写的叙事关键词（优先用于 prompt 注入）
    key_traits: list[str] = field(default_factory=list)
    voice_examples: list[str] = field(default_factory=list)
    # DM 给予玩家的额外自定义背景（可选，注入 prompt）
    special_background: str = ""
    # AI 识图生成的外貌描述（不在 WebUI 编辑区显示，Draft 时注入 prompt）
    appearance_ai: str = ""
    # 退出跑团标注
    left_after_session: str | None = None
    exit_story: str | None = None
    # 入场标注
    first_appearance_session: str | None = None
    # 派生（load 时自动填充）
    atomic_facts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML 读取（主路径）
# ---------------------------------------------------------------------------


def load_card_yaml(yaml_path: Path) -> CharacterCard:
    """从 YAML 文件加载人物卡（WebUI 表单填写的标准格式）。"""
    data: dict = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    # 向后兼容：旧的 player_handle 字段转换为 aliases
    aliases = list(data.get("aliases") or [])
    if not aliases and data.get("player_handle"):
        aliases = [data["player_handle"]]

    card = CharacterCard(
        name=data.get("name", yaml_path.stem),
        aliases=aliases,
        age=str(data.get("age", "")),
        gender=data.get("gender", ""),
        homeland=data.get("homeland", ""),
        appearance=data.get("appearance", ""),
        personality=data.get("personality", ""),
        ideal=data.get("ideal", ""),
        bond=data.get("bond", ""),
        flaw=data.get("flaw", ""),
        background_story=data.get("background_story", ""),
        race=data.get("race", ""),
        subrace=data.get("subrace", ""),
        class_name=data.get("class", data.get("class_name", "")),
        subclass=data.get("subclass", ""),
        background_class=data.get("background_class", ""),
        background_feature=data.get("background_feature", ""),
        languages=list(data.get("languages") or []),
        key_traits=list(data.get("key_traits") or []),
        voice_examples=list(data.get("voice_examples") or []),
        special_background=data.get("special_background") or "",
        appearance_ai=data.get("appearance_ai") or "",
        left_after_session=data.get("left_after_session") or None,
        exit_story=data.get("exit_story") or None,
        first_appearance_session=data.get("first_appearance_session") or None,
    )
    card.atomic_facts = derive_atomic_facts(card)
    return card


def load_all_cards(cards_dir: Path) -> dict[str, "CharacterCard"]:
    """扫描目录，加载所有 .yaml 人物卡；跳过 .xlsx 和其他文件。
    返回 {card.name: card}。
    """
    out: dict[str, CharacterCard] = {}
    if not cards_dir.exists():
        return out
    for path in sorted(cards_dir.glob("*.yaml")):
        try:
            card = load_card_yaml(path)
            out[card.name] = card
        except Exception:
            continue
    return out


def card_to_dict(card: CharacterCard) -> dict:
    """把 CharacterCard 转成可序列化为 YAML 的 dict（写文件时用）。"""
    return {
        "name": card.name,
        "aliases": list(card.aliases),
        "race": card.race,
        **({"subrace": card.subrace} if card.subrace else {}),
        "class": card.class_name,
        **({"subclass": card.subclass} if card.subclass else {}),
        "age": card.age,
        "gender": card.gender,
        "homeland": card.homeland,
        "appearance": card.appearance,
        "personality": card.personality,
        "ideal": card.ideal,
        "bond": card.bond,
        "flaw": card.flaw,
        "background_story": card.background_story,
        "background_class": card.background_class,
        "key_traits": list(card.key_traits),
        "voice_examples": list(card.voice_examples),
        **({"special_background": card.special_background} if card.special_background else {}),
        **({"appearance_ai": card.appearance_ai} if card.appearance_ai else {}),
        **({"left_after_session": card.left_after_session} if card.left_after_session else {}),
        **({"exit_story": card.exit_story} if card.exit_story else {}),
        **({"first_appearance_session": card.first_appearance_session} if card.first_appearance_session else {}),
    }


# ---------------------------------------------------------------------------
# 原子事实生成（prompt 注入用）
# ---------------------------------------------------------------------------


def derive_atomic_facts(card: CharacterCard) -> list[str]:
    """把人物卡关键字段切成若干条原子叙事事实。

    优先使用用户在 WebUI 填写的 ``key_traits``。
    若未填写，回退到结构化字段的关键词提取（dnd5e 旧 xlsx 导入路径的占位逻辑）。
    """
    # 用户手写的最高优先
    if card.key_traits:
        facts = list(card.key_traits)
        if card.voice_examples:
            facts.append("说话风格样例：" + "；".join(f'「{v}」' for v in card.voice_examples))
        if card.special_background:
            facts.append("特殊背景：" + card.special_background)
        if card.appearance_ai:
            facts.append("外貌细节（识图补充）：" + card.appearance_ai)
        return facts

    # 回退：从结构化字段推导（与 v1 逻辑保持一致，dnd5e 特化）
    facts: list[str] = []

    if card.race:
        facts.append(f"种族：{card.race}")
    elif card.homeland and "星界" in card.homeland:
        facts.append("星界精灵（Astral Elf），来自星光位面，非凡人种族")

    if card.class_name:
        facts.append(f"职业：{card.class_name}")

    if card.homeland and "星界" not in card.homeland:
        facts.append(f"故乡：{card.homeland}")

    if card.age:
        facts.append(f"年龄：{card.age}岁")

    if card.appearance:
        a = card.appearance
        if "银蓝" in a or "瞳" in a:
            facts.append("眼底闪烁细碎星光，情绪平静时如静谧星空，激动时微光流转；银蓝瞳色")
        if "皮甲" in a or "长剑" in a:
            facts.append("平日穿白色轻型皮甲，腰挂长剑与腰包")
        if "马尾" in a:
            facts.append("淡灰色发，常束高马尾")

    if card.personality:
        facts.append(f"个性：{card.personality.strip()}")
    if card.ideal:
        facts.append(f"理念：{card.ideal.strip()}")
    if card.bond:
        facts.append(f"羁绊：{card.bond.strip()}")
    if card.flaw:
        facts.append(f"缺陷：{card.flaw.strip()}")

    if card.background_story:
        bs = card.background_story
        if "厌倦" in bs:
            facts.append('厌倦永恒不老的星界生活，主动踏上费伦旅途寻求"时间流逝"的感受')
        if "观察者" in bs:
            facts.append('将自己定位为"观察者"——记录短暂而炽热的瞬间，不愿卷入琐碎纷争')
        if "友谊" in bs or "羁绊" in bs:
            facts.append("内心深处渴望在费伦找到真正的友谊与羁绊")

    if card.appearance_ai:
        facts.append("外貌细节（识图补充）：" + card.appearance_ai)

    return facts


# ---------------------------------------------------------------------------
# xlsx 一次性导入工具（v1 迁移专用）
# ---------------------------------------------------------------------------

_LABEL_MAP = {
    "角色名": "name",
    "年龄": "age",
    "性别": "gender",
    "故乡": "homeland",
    "背景故事": "background_story",
    "背景特性": "background_feature",
    "背景": "background_class",
    "人物衣装\n外貌描述": "appearance",
    "外貌描述": "appearance",
    "个性": "personality",
    "理念": "ideal",
    "羁绊": "bond",
    "缺陷": "flaw",
}


def _find_value_right(cells: dict, row: int, label_col: int) -> str:
    for offset in range(1, 20):
        v = cells.get((row, label_col + offset), "")
        if v:
            return v
    return ""


def _load_card_xlsx(xlsx_path: Path) -> CharacterCard:
    """从 xlsx 的「背景」sheet 读取字段（仅 dnd5e）。内部用。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import openpyxl
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)

    ws = wb["背景"]
    cells: dict[tuple[int, int], str] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                cells[(cell.row, cell.column)] = str(cell.value).strip()

    card = CharacterCard(name=xlsx_path.stem)
    for (r, c), v in cells.items():
        matched = next((attr for label, attr in _LABEL_MAP.items() if label in v), None)
        if matched is None:
            continue
        if len(v) > 30:
            if matched == "appearance" and not card.appearance:
                card.appearance = v
            elif matched == "background_story" and not card.background_story:
                card.background_story = v
            elif matched == "background_feature" and not card.background_feature:
                card.background_feature = v
            continue
        val = _find_value_right(cells, r, c)
        if val:
            setattr(card, matched, val)

    card.atomic_facts = derive_atomic_facts(card)
    return card


def import_from_xlsx(xlsx_path: Path, system: str = "dnd5e") -> dict:
    """将 xlsx 人物卡转成可直接写入 YAML 的 dict。

    ``key_traits`` 用 ``derive_atomic_facts`` 的结果预填，用户可在 WebUI 中修改。
    """
    card = _load_card_xlsx(xlsx_path)
    d = card_to_dict(card)
    # 用推导结果作为 key_traits 初值（用户在 WebUI 中审阅/修改）
    d["key_traits"] = list(card.atomic_facts)
    d["name"] = card.name if card.name else xlsx_path.stem
    return d


# ---------------------------------------------------------------------------
# 向后兼容入口
# ---------------------------------------------------------------------------


def load_card(path: Path) -> CharacterCard:
    """自动识别 .yaml / .xlsx，统一返回 CharacterCard。"""
    if path.suffix.lower() == ".yaml":
        return load_card_yaml(path)
    return _load_card_xlsx(path)

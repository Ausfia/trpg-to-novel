# -*- coding: utf-8 -*-
"""人物卡关键特征自动生成（LLM）。

公开接口：
    generate_key_traits(card_data, api_key, base_url, model, kb=None) -> list[str]
"""

from __future__ import annotations

from trpg2novel.llm.client import chat_json, make_client

_SYSTEM = (
    "你是一位专业的奇幻小说角色档案整理专家，"
    "擅长从人物卡原始信息中提炼简洁、有特色的叙事关键事实。"
)

_FIELDS = [
    ("name", "姓名"),
    ("race", "种族"),
    ("subrace", "亚种"),
    ("class", "职业"),
    ("subclass", "子职"),
    ("age", "年龄"),
    ("gender", "性别"),
    ("homeland", "故乡"),
    ("appearance", "外貌"),
    ("appearance_ai", "外貌补充（识图）"),
    ("personality", "个性"),
    ("ideal", "理念"),
    ("bond", "羁绊"),
    ("flaw", "缺陷"),
    ("background_story", "背景故事"),
    ("special_background", "DM 给予的特殊背景"),
]


def _build_prompt(card_data: dict, kb=None) -> str:
    lines: list[str] = [
        "请根据以下角色信息，提炼 6-10 条简短的关键叙事事实，"
        "用于在小说创作 prompt 中快速召回该角色的核心特质。\n",
    ]
    for key, label in _FIELDS:
        val = card_data.get(key) or card_data.get("class_name" if key == "class" else key, "")
        if val:
            lines.append(f"{label}：{val}")
    ve = card_data.get("voice_examples") or []
    if ve:
        lines.append("说话风格样例：" + "；".join(f'「{v}」' for v in ve))

    # 从知识库检索相关世界观背景
    if kb is not None:
        query_parts = [
            card_data.get("name", ""),
            card_data.get("homeland", ""),
            card_data.get("race", ""),
            card_data.get("class", ""),
            card_data.get("special_background", "")[:100],
        ]
        query = " ".join(p for p in query_parts if p)
        if query.strip():
            try:
                retrieved = kb.query(query, top_k=3)
                if retrieved:
                    lines.append("\n世界观背景参考（来自知识库，提炼事实时可参考）：")
                    for r in retrieved:
                        lines.append(f"[{r.source}]\n{r.text[:300]}")
            except Exception:
                pass

    lines += [
        "\n要求：",
        "- 每条不超过 25 字，具体有特色，绝不使用泛化表达",
        "- 涵盖：外貌辨识度、种族职业特性、性格核心矛盾、背景关键节点、说话风格",
        "- 如有 DM 特殊背景，须提炼为 1-2 条核心事实",
        "- 不重复角色名本身，用第三人称事实陈述",
        '- 返回 JSON 格式：{"key_traits": ["事实1", "事实2", ...]}',
    ]
    return "\n".join(lines)


def generate_key_traits(
    card_data: dict,
    api_key: str,
    base_url: str,
    model: str,
    kb=None,
) -> list[str]:
    """调用 LLM 为人物卡生成 key_traits 列表。

    Args:
        kb: 可选知识库（KnowledgeBase 实例），若提供则查询角色相关世界观背景注入 prompt。
    """
    client = make_client(api_key, base_url)
    prompt = _build_prompt(card_data, kb)
    result = chat_json(client, model, [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ], temperature=0.6, max_tokens=800)
    traits = result.get("key_traits") or []
    if not isinstance(traits, list):
        return []
    return [str(t).strip() for t in traits if str(t).strip()]

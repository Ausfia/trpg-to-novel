# trpg-to-novel

把 TRPG（DnD 5e）跑团日志整理成连贯小说的多环节 pipeline——脚本 + 多 LLM + 人在参与。

## 为什么

跑团日志是对话式记录，有 DM 叙述、玩家 IC 发言、玩家行动、局外吐槽、骰娘的判定结算……信息密度高、机制味重、且**跨多场跑团连续展开同一故事**。市面没有合适工具能把这种素材整理成"小说"（多数是基于设定生成的工具）。本项目针对自己的跑团做。

## 设计要点

- **8 阶段 pipeline**：Parse → Classify & Pair → Scene Segment → Story State → Chapter Boundary Detector → Draft → Review → HITL UI。每阶段产物落盘成 JSON / Markdown / YAML，方便单步重跑、Git diff、人工编辑。
- **跨场次状态延续是核心**：`data/meta/story_state.yaml` 是单一事实源，跟踪每个角色的状态、知识、关系与世界进展。
- **章节边界自动判断**：脱离"一场一章"束缚，由系统判断起承转合的真实切点；不足一章的累积场景留在 `data/pending/<arc_id>/` 等下一场。
- **掷骰演绎**：骰命令-结果配对后，叙事层禁止出现"判定/D20/AC/HP"等机制词，全转为环境/动作因果。
- **人物卡注入"补足而非覆盖"**：玩家已经演绎出的角色气质不要让 LLM 抢戏。

## 输入约定

- 跑团日志放 `data/raw_logs/<session_id>.md`，UTF-8。每条消息后跟空行，发言人用 `\<...\>` 转义包围。
- 每场配一个 `data/raw_logs/<session_id>.yaml` 标注日期、骰娘 handle、DM handle、缺席玩家。
- 人物卡放 `data/character_cards/<pc_name>.xlsx`，只读「背景」sheet。

**PC 输入的三标记规范**（玩家在跑团时要遵守）：

| 标记 | 含义 |
|---|---|
| `"..."` | IC 发言 |
| `#` / `＃` 开头 | 行动/动作 |
| `（...）` / `(...)` | OOC 局外吐槽 |

裸文本会被记为 warning + 默认按 OOC 处理。

## 使用

```bash
# 安装
pip install -e ".[dev]"

# 配置 LLM
cp .env.example .env
# 编辑 .env 填入 API key 和模型

# 跑解析阶段
trpg2novel parse data/raw_logs/s01.md

# 自动章节断点检测 + 章节生成
trpg2novel draft --auto-detect

# 起 UI
streamlit run ui/app.py
```

## 状态

MVP 开发中。完整方案见 `C:\Users\qq161\.claude\plans\md-optimized-elephant.md`（本地）。

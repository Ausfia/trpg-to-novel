# DESIGN.md — TRPG-to-Novel 详细架构设计

> 本文档描述项目的完整架构、数据流、各阶段职责与关键机制。
> 阅读顺序建议：第 1 节（总览）→ 第 2 节（数据流）→ 第 3-6 节（各阶段细节）→ 第 7 节（横切机制）

---

## 目录

1. [设计哲学](#1-设计哲学)
2. [总体架构与数据流](#2-总体架构与数据流)
3. [Workflow 0：输入预处理](#3-workflow-0输入预处理)
4. [Workflow 1：结构化分析](#4-workflow-1结构化分析)
5. [Workflow 2：大纲生成](#5-workflow-2大纲生成)
6. [Workflow 3：正文生成](#6-workflow-3正文生成)
7. [横切机制](#7-横切机制)
8. [文件系统约定](#8-文件系统约定)
9. [错误处理与断点续传](#9-错误处理与断点续传)

---

## 1. 设计哲学

### 1.1 核心原则

1. **线性流水线，不做循环编排**
   - 整个系统是 W0 → W1 → W2 → W3 的单向流水线
   - 不做"自我反思""多 agent 辩论"等复杂模式
   - 复杂的决策让人来做，AI 负责执行

2. **结构化中间产物**
   - 每个阶段的输出都是**结构化的 JSON**（人类可读、可编辑）
   - 阶段之间**只通过文件传递**，不共享内存
   - 任何中间产物都可以被人工修改后重新进入流水线

3. **人在回路（Human-in-the-loop）**
   - 阶段之间是**强制 review 点**
   - 用户可以检查、修改、批准后进入下一阶段
   - 不追求"一键生成"，追求"高质量产出"

4. **可重入与断点续传**
   - 任何阶段都可以单独重跑
   - 已完成的工作不会被覆盖（除非显式 `--force`）
   - 部分失败不影响其他部分

5. **Prompt 与代码分离**
   - 所有 prompt 在 `prompts/*.j2` 文件中
   - 调 prompt 不需要改代码
   - prompt 的修改可以被 git 追踪

### 1.2 不做什么

- ❌ 不做实时生成（用户不会等着看流式输出）
- ❌ 不做多用户、多会话支持
- ⏸ **Phase 1 不做 Web UI**，但架构保证未来可低成本叠加（见第 7.6 节）
- ❌ 不做"自动优化""自动评分"等元层功能
- ❌ 不做 RAG/向量数据库（场景池和大纲足够作为上下文）

---

## 2. 总体架构与数据流

### 2.1 高层数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                          input/                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ 主线日志 │  │  人物卡  │  │ 单人剧情 │  │ 世界观   │         │
│  │ logs/    │  │ chars/   │  │ solos/   │  │ lore/    │         │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
└───────┼─────────────┼─────────────┼─────────────┼───────────────┘
        │             │             │             │
        ▼             ▼             ▼             ▼
   ┌────────────────────────────────────────────────────┐
   │ Workflow 0：输入预处理                              │
   │ - 解析人物卡 → CharacterCard[]                      │
   │ - 切分日志 → LogChunk[]                             │
   │ - 标注单人剧情归属 → SoloArc[]                      │
   │ - 加载世界观 → LoreBook                             │
   └────────────────┬───────────────────────────────────┘
                    ▼
        workspace/project_package.json
                    │
                    ▼
   ┌────────────────────────────────────────────────────┐
   │ Workflow 1：结构化分析                              │
   │ - 场景切分 → Scene[]                                │
   │ - 场景分类（IC/OOC/单人/闪回候选）                  │
   │ - 事件抽取 → Event[]                                │
   │ - 伏笔识别 → Foreshadowing[]                        │
   │ - 人物弧线分析 → CharacterArc[]                     │
   └────────────────┬───────────────────────────────────┘
                    ▼
        workspace/story_analysis.json
                    │
                    ▼
   ┌────────────────────────────────────────────────────┐
   │ Workflow 2：大纲生成                                │
   │ - 章节划分（基于场景池）                            │
   │ - 单人剧情嵌入决策（独立成章 or 闪回）              │
   │ - 伏笔分布规划                                      │
   │ - 每章主题与情绪曲线                                │
   │ - 每章风格指令                                      │
   └────────────────┬───────────────────────────────────┘
                    ▼
        workspace/novel_outline.json
                    │
                    ▼
   ┌────────────────────────────────────────────────────┐
   │ Workflow 3：正文生成                                │
   │ - 逐场景生成 → SceneDraft                           │
   │ - 章节缝合 → ChapterDraft                           │
   │ - 风格一致性检查                                    │
   │ - 输出最终 .md                                      │
   └────────────────┬───────────────────────────────────┘
                    ▼
              output/chapters/*.md
```

### 2.2 阶段间的 Review 点

每个阶段结束后，用户应当 review 中间产物再继续：

| Review 点 | 用户应检查 | 修改方式 |
|---|---|---|
| W0 后 | 人物卡是否解析正确、日志切分是否合理 | 直接编辑 `project_package.json` |
| W1 后 | 场景划分是否合理、事件是否抓住要点 | 编辑 `story_analysis.json` |
| W2 后 | 章节划分是否合理、每章主题是否符合预期 | 编辑 `novel_outline.json` |
| W3 后 | 正文质量、风格一致性 | 直接编辑 `output/chapters/*.md` |

---

## 3. Workflow 0：输入预处理

### 3.1 目标

将用户提供的**原始、异构、非结构化**材料，转换为**统一、结构化、可被后续阶段消费**的项目包。

### 3.2 输入

```
input/
├── logs/                       # 主线日志（必需）
│   ├── session_01.txt
│   ├── session_02.txt
│   └── ...
├── characters/                 # 人物卡（必需）
│   ├── pc_alice.md
│   ├── pc_bob.md
│   └── npc_villain.md
├── solos/                      # 单人剧情（可选）
│   ├── alice_backstory.txt
│   └── bob_solo_session_03.txt
└── lore/                       # 世界观、设定（可选）
    ├── world.md
    └── factions.md
```

**文件命名约定**（用户需遵守）：
- 人物卡：`pc_*.md`（玩家角色）、`npc_*.md`（NPC）
- 单人剧情：文件名包含 PC 名字，用于自动归属
- 主线日志：按时间顺序命名（`session_01`, `session_02`...）

### 3.3 处理步骤

#### Step 0.1：人物卡解析

对每个 `characters/*.md` 文件：
- 调用 LLM，按 `CharacterCard` schema 提取
- 关键字段：姓名、是否 PC、外貌、性格、背景、**秘密**、能力、说话风格
- "秘密"字段标记 `is_secret: true`，用于后续伏笔规划

**输出**：`CharacterCard[]`

#### Step 0.2：日志切分（chunking）

对每个 `logs/session_*.txt`：
- 按 token 数粗切（每块约 4000 tokens，重叠 200）
- 保留原始文本和 session 归属
- **不在此阶段做语义切分**，仅做物理切分

**输出**：`LogChunk[]`

> 注：精细的场景切分在 W1 完成，W0 只做粗切以便 W1 处理。

#### Step 0.3：单人剧情归属

对每个 `solos/*.txt`：
- 根据文件名匹配 PC
- 如果匹配失败，调用 LLM 从内容判断归属
- 标记单人剧情的时间锚点（如果文件名或内容中有 `session_XX`，说明发生在该 session 前后）

**输出**：`SoloArc[]`

#### Step 0.4：世界观加载

- 简单拼接所有 `lore/*.md`
- 不做结构化，作为后续阶段的"参考资料"

**输出**：`LoreBook`（字符串）

#### Step 0.5：项目元信息

要求用户在 `config/project.yaml` 中提供：
- 团本名称
- 跑团系统（COC / DnD / 自定义）
- 目标小说风格（引用 `style_recipe.yaml`）
- 目标章节数（可选，否则由 W2 决定）
- 视角偏好（第一人称 / 第三人称限定 / 第三人称全知）

### 3.4 输出

`workspace/project_package.json`：

```json
{
  "meta": {
    "campaign_name": "...",
    "system": "COC",
    "style_recipe": "shonen_royal_road",
    "narrative_pov": "third_limited",
    "target_chapter_count": null
  },
  "characters": [CharacterCard, ...],
  "log_chunks": [LogChunk, ...],
  "solo_arcs": [SoloArc, ...],
  "lore_book": "..."
}
```

### 3.5 Review 点 0

用户应检查：
- 人物卡的"秘密"字段是否标注正确（影响伏笔规划）
- 单人剧情归属是否正确
- 项目元信息是否准确

---

## 4. Workflow 1：结构化分析

### 4.1 目标

将日志从"流水账"变为"结构化的叙事素材库"。

**核心产出**：**场景池（Scene Pool）**——一个有序的场景列表，每个场景带有完整的语义标签。

### 4.2 输入

`workspace/project_package.json`

### 4.3 处理步骤

#### Step 1.1：场景切分

遍历 `LogChunk[]`，调用 LLM 进行**语义级场景切分**：

- 一个场景的定义：**时空连续 + 焦点连续**
- 场景切分的信号：时间跳跃、地点转换、焦点人物变化、IC/OOC 切换
- 跨 chunk 的场景要正确合并

**关键设计**：场景切分采用**滑动窗口**策略，每次给 LLM 看 1-2 个 chunk，并附带上一次切分的"未完成场景"作为上下文。

**输出**：`Scene[]`（每个场景有 `scene_id`、`raw_text`、`session_ref`）

#### Step 1.2：场景分类

对每个场景，分类为以下类型之一：

| 类型 | 含义 | 处理策略 |
|---|---|---|
| `ic_main` | 主线 IC 剧情 | 进入主线叙事 |
| `ooc_chat` | 玩家闲聊、规则讨论 | 默认丢弃，除非有彩蛋价值 |
| `solo` | 单人剧情场景 | 进入闪回池 |
| `mixed` | 混杂 IC/OOC | 需要清洗 |
| `meta` | KP 的旁白、骰点描述 | 转化为叙事性描述 |

**输出**：每个 `Scene` 增加 `scene_type` 字段

#### Step 1.3：事件抽取

对每个 `ic_main` 场景，抽取：
- **核心事件**（1-3 个）：发生了什么
- **参与角色**：谁在场、谁是焦点
- **场景情绪**：紧张 / 轻松 / 悲伤 / ...
- **关键信息揭示**：本场景透露了哪些设定/线索
- **遗留悬念**：场景结束时未解决的问题

**输出**：每个 `Scene` 增加 `events`、`participants`、`mood`、`revelations`、`hooks` 字段

#### Step 1.4：伏笔识别

**这是 W1 最复杂的部分**。

伏笔分两类：
1. **回收型伏笔**：日志中已经完成"埋设→回收"的伏笔
2. **未回收型伏笔**：日志中只埋设、未回收的（如人物秘密）

对于**人物秘密**（来自人物卡 `is_secret: true` 的字段）：
- 在场景池中搜索"可能暗示该秘密"的场景
- 如果完全没有，W2 阶段需要主动设计伏笔点
- 如果有，标记这些场景为"伏笔候选场景"

**输出**：`Foreshadowing[]`（独立于 Scene，但通过 `scene_refs` 关联）

#### Step 1.5：人物弧线分析

对每个 PC，分析：
- 出场密度（哪些场景是该 PC 的高光）
- 关系网络（与其他角色的互动密度）
- 弧线轨迹（性格、目标、心境的变化）
- **未在主线中展开的秘密/背景**（来自人物卡）

**输出**：`CharacterArc[]`

### 4.4 输出

`workspace/story_analysis.json`：

```json
{
  "scenes": [Scene, ...],
  "foreshadowings": [Foreshadowing, ...],
  "character_arcs": [CharacterArc, ...],
  "statistics": {
    "total_scenes": 87,
    "ic_main_scenes": 62,
    "solo_scenes": 8,
    "discarded_scenes": 17
  }
}
```

### 4.5 Review 点 1

用户应检查：
- 场景切分是否合理（过细 / 过粗）
- 是否有重要场景被误判为 OOC
- 伏笔识别是否抓住了关键
- 人物弧线是否反映了真实的角色成长

用户可直接编辑 JSON 调整。

---

## 5. Workflow 2：大纲生成

### 5.1 目标

将"场景池"组织为"小说大纲"——决定章节划分、节奏、单人剧情嵌入方式、伏笔分布、每章风格。

### 5.2 输入

- `workspace/project_package.json`
- `workspace/story_analysis.json`
- `config/style_recipe.yaml`

### 5.3 处理步骤

#### Step 2.1：章节划分

将场景池组织为章节，遵循以下原则：

- 每章应有**独立的叙事弧**（小高潮 + 缓和）
- 章节长度大致均衡（按场景数或预估字数）
- 重大节点（如关键揭示、角色转折）应靠近章节末尾
- 默认章节数由 LLM 决定，用户可在 `project.yaml` 中限定范围

**关键决策**：单人剧情的处理方式

| 决策 | 适用情况 |
|---|---|
| **独立成章** | 单人剧情自成一个完整弧线、篇幅较长 |
| **闪回嵌入** | 单人剧情较短，且能在主线某个情绪点回响 |
| **拆分嵌入** | 单人剧情很长但分多个情绪点，可拆为多个闪回 |
| **降级为参考资料** | 单人剧情主要是设定补充，不适合直接呈现 |

#### Step 2.2：闪回点规划

对于决定用"闪回"方式处理的单人剧情：
- 选择主线中的**情绪触发点**（mood 匹配、主题共鸣）
- 标记"在 Chapter X 的 Scene Y 之后插入闪回 Z"
- 闪回的写法在 W3 阶段处理

#### Step 2.3：伏笔分布规划

对于人物秘密类伏笔：
- 在大纲中明确**埋设章节**和**回收章节**
- 如果原日志没有自然的埋设点，规划"额外补写的伏笔片段"，作为章节中的小场景插入
- 伏笔强度递进：**模糊暗示 → 反常细节 → 关键揭示**

#### Step 2.4：每章主题与情绪曲线

对每章定义：
- **主题**：本章在讲什么（一句话）
- **核心冲突**：内在 / 外在的冲突焦点
- **情绪曲线**：开头 → 中段 → 结尾的情绪走向
- **本章重点角色**：焦点 PC、出场 NPC
- **承前启后**：本章解决了什么、留下了什么

#### Step 2.5：每章风格指令

基于 `style_recipe.yaml`，为每章生成定制化的风格指令：

- **共性指令**：来自风格配方的基础设定（叙述节奏、对白比例、修辞密度）
- **个性指令**：根据本章主题调整（如战斗章节强调动作描写，日常章节强调氛围）
- **禁忌清单**：本章绝对不要做的事（如不要在悲伤场景用幽默旁白）

### 5.4 输出

`workspace/novel_outline.json`：

```json
{
  "global_style": {
    "recipe_ref": "shonen_royal_road",
    "narrative_pov": "third_limited",
    "tense": "past"
  },
  "chapters": [
    {
      "chapter_id": 1,
      "title": "...",
      "theme": "...",
      "scene_refs": ["scene_001", "scene_002", ...],
      "flashback_inserts": [...],
      "foreshadowing_plants": [...],
      "foreshadowing_payoffs": [],
      "mood_curve": ["calm", "tense", "tragic"],
      "focal_characters": ["pc_alice"],
      "style_directives": {
        "narrative_pace": "...",
        "dialogue_ratio": "high",
        "imagery_density": "medium",
        "forbidden": ["..."]
      }
    },
    ...
  ]
}
```

### 5.5 Review 点 2

用户应检查（**这是最关键的 review 点**）：
- 章节划分是否符合预期的节奏
- 单人剧情的处理方式是否合理
- 每章主题是否抓住了原团本的精髓
- 伏笔分布是否自然

**强烈建议**用户在此处认真打磨，因为 W3 的产出质量很大程度取决于大纲质量。

---

## 6. Workflow 3：正文生成

### 6.1 目标

按章节生成最终小说正文。

### 6.2 输入

- `workspace/project_package.json`
- `workspace/story_analysis.json`
- `workspace/novel_outline.json`
- `config/style_recipe.yaml`

### 6.3 处理策略：双层生成

为兼顾**局部精度**和**全局连贯**，采用两步法：

```
Step 1：逐场景生成 → SceneDraft[]
Step 2：章节缝合 → ChapterDraft → output/chapters/chXX.md
```

#### Step 3.1：逐场景生成

对每个章节的每个场景：

**输入到 LLM 的上下文**（按优先级）：
1. 风格配方（精简版）
2. 本章风格指令
3. 本章主题与情绪曲线
4. 本场景的结构化信息（events、mood、participants）
5. 本场景的原始日志文本（清洗后）
6. 参与角色的人物卡（精简版）
7. **前一场景的结尾段落**（保证衔接）
8. **承担的伏笔任务**（埋设 / 回收）

**输出**：`SceneDraft`（Markdown 文本）

**关键约束**：
- 不要逐字翻译日志，要做**文学化重构**
- 保留原日志的关键信息和情绪
- 严格遵守视角和时态
- 长度自适应（重要场景写长、过渡场景写短）

#### Step 3.2：闪回插入

对于规划了闪回的章节：
- 闪回单独作为一个 SceneDraft 生成
- 在生成时明确指令"这是回忆/闪回"，要求文体上有所区分（如斜体、过去完成时、感官细节加重）

#### Step 3.3：章节缝合

将一个章节的所有 SceneDraft 拼接为 ChapterDraft：

- 添加章节标题
- 调整场景间过渡（如果需要，插入过渡段）
- 全章风格一致性快速检查（可选，由 LLM 做轻量校对）
- 输出为 Markdown

**输出**：`output/chapters/ch01.md`、`ch02.md`、...

#### Step 3.4：全书装订（可选）

将所有章节合并为单一文件：
- `output/full_novel.md`
- 可选导出为其他格式（用户自行用 pandoc 转换）

### 6.4 Review 点 3

用户应检查：
- 风格一致性
- 是否准确传达了原团本的精髓
- 是否有逻辑错误或人物 OOC
- 用户可直接编辑 `.md` 文件

---

## 7. 横切机制

### 7.1 风格配方（Style Recipe）

`config/style_recipe.yaml` 定义全局风格。示例（王道少年漫）：

```yaml
name: shonen_royal_road
description: 王道少年漫风格

narrative:
  pov_default: third_limited
  tense: past
  pacing: dynamic   # 战斗与日常张弛有度

prose_style:
  sentence_length: medium_to_long
  dialogue_ratio: high
  imagery_density: medium
  internal_monologue: frequent

themes:
  - friendship
  - growth
  - never_give_up
  - found_family

tropes_to_embrace:
  - power_of_bonds
  - underdog_victory
  - rival_to_friend
  - hidden_potential

tropes_to_avoid:
  - excessive_grimdark
  - nihilism
  - explicit_violence_gratuitous

dialogue_style:
  passionate: true
  declarations_of_resolve: encouraged
  banter_between_allies: encouraged

forbidden:
  - omniscient_narrator_intrusion
  - meta_commentary
```

**关键设计**：风格配方是**全局基线**，每章的 `style_directives` 是**局部调整**，最终生效的是两者的合成。

### 7.2 模型配置

**设计目标**：每个 LLM 调用环节都可以单独配置使用的模型、API key、API 端点，且配置全部在 YAML 中，**用户无需理解代码**。

#### 7.2.1 配置文件结构

`config/models.yaml` 采用"default + 覆盖"的简单模式：

```yaml
# 默认配置：所有未单独配置的环节都使用这套
default:
  model: gpt-4o
  api_key_env: OPENAI_API_KEY              # 从环境变量读取 API key
  base_url: https://api.openai.com/v1
  temperature: 0.7
  max_tokens: 4096
  timeout: 120                              # 秒

# 各环节的配置：只填要覆盖 default 的字段，未填的字段继承 default
workflows:
  w0_preprocess:
    parse_character:
      model: gpt-4o-mini                   # 简单任务用便宜模型
    classify_solo:
      model: gpt-4o-mini

  w1_analyze:
    scene_segmentation:
      model: claude-sonnet-4-5             # 长文本理解用 Claude
      api_key_env: ANTHROPIC_API_KEY
      base_url: https://api.anthropic.com/v1
    event_extraction: {}                   # 完全使用 default
    foreshadowing:
      model: claude-sonnet-4-5
      api_key_env: ANTHROPIC_API_KEY
      base_url: https://api.anthropic.com/v1

  w2_outline:
    chapter_planning:                      # 最关键阶段用最强模型
      model: claude-sonnet-4-5
      api_key_env: ANTHROPIC_API_KEY
      base_url: https://api.anthropic.com/v1
      temperature: 0.8

  w3_generate:
    scene_generation: {}                   # 用 default
    chapter_stitching:
      model: gpt-4o-mini
      temperature: 0.3                     # 缝合用低温度，保持稳定
```

#### 7.2.2 字段说明

| 字段 | 必需 | 含义 |
|---|---|---|
| `model` | 是 | 模型名（任何 LiteLLM 支持的模型名） |
| `api_key_env` | 是 | 环境变量名（不是 key 本身），如 `OPENAI_API_KEY` |
| `base_url` | 否 | API 端点 URL；不填用 provider 默认值 |
| `temperature` | 否 | 默认 0.7 |
| `max_tokens` | 否 | 默认 4096 |
| `timeout` | 否 | 默认 120 秒 |

#### 7.2.3 兼容性

只要服务商**兼容 OpenAI API 协议**，都可以接入：
- OpenAI 官方
- Anthropic Claude（LiteLLM 自动适配）
- Google Gemini（LiteLLM 自动适配）
- DeepSeek：`base_url: https://api.deepseek.com/v1`
- Moonshot：`base_url: https://api.moonshot.cn/v1`
- 智谱 GLM：`base_url: https://open.bigmodel.cn/api/paas/v4`
- 本地 Ollama：`base_url: http://localhost:11434/v1`
- 本地 LM Studio：`base_url: http://localhost:1234/v1`
- 任何 vLLM / SGLang 部署的服务

#### 7.2.4 配置加载与合并逻辑

`llm_client.py` 在调用时执行以下逻辑：
1. 读取 `workflows.<workflow>.<step>` 的配置
2. 若该 step 的某字段缺失，从 `default` 继承
3. 从 `api_key_env` 指定的环境变量中读取 API key
4. 组装为 LiteLLM 调用参数

**伪代码**：

```python
def resolve_model_config(workflow: str, step: str) -> dict:
    cfg = load_yaml("config/models.yaml")
    default = cfg["default"]
    step_cfg = cfg.get("workflows", {}).get(workflow, {}).get(step, {})
    merged = {**default, **step_cfg}
    merged["api_key"] = os.getenv(merged["api_key_env"])
    if not merged["api_key"]:
        raise ConfigError(f"环境变量 {merged['api_key_env']} 未设置")
    return merged
```

#### 7.2.5 `.env` 文件

API key 通过 `.env` 文件管理，**不进 git**：

```bash
# .env.example（提交到 git，作为模板）
OPENAI_API_KEY=sk-your-key-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
DEEPSEEK_API_KEY=your-key-here
# 本地服务无需真实 key，但仍需占位
OLLAMA_API_KEY=ollama
```

用户复制为 `.env` 后填入真实 key。

### 7.3 Prompt 模板管理

所有 prompt 在 `prompts/workflow_X/*.j2`：

- 命名约定：`<step_name>.j2`，与 Python 中的函数名对应
- 每个模板顶部用注释说明：用途、变量、期望输出
- 公共片段（如人物卡格式化）抽取为 `prompts/_partials/*.j2`，用 Jinja2 的 `{% include %}` 复用

### 7.4 LLM 调用规范

所有调用通过 `src/trpg_to_novel/llm_client.py`：

```python
def call_llm(
    prompt_template: str,
    variables: dict,
    response_model: Type[BaseModel] | None = None,
    workflow: str = "default",
    step: str = "default",
    max_retries: int = 3,
) -> BaseModel | str:
    """
    workflow / step 用于从 models.yaml 查找配置。
    例如 workflow='w2_outline', step='chapter_planning'。
    """
    ...
```

- 结构化输出用 `instructor` + Pydantic 模型
- 自由文本输出返回字符串
- 失败重试用 `tenacity`
- 调用日志用 `rich` 输出（包含 token 用量、使用的模型名）

### 7.5 缓存机制

可选用 `diskcache` 缓存 LLM 调用：

- key = hash(prompt_template + variables + model + temperature)
- 默认开启，避免重复消耗 token
- 用户可用 `--no-cache` 或 `--force` 跳过缓存
- 注意：换了模型后缓存自动失效（因为 key 包含模型名）

### 7.6 WebUI 预留接口

**Phase 1 不实现 WebUI**，但当前架构必须保证未来可以低成本叠加。具体约束：

#### 7.6.1 架构约束

1. **所有 workflow 函数必须是纯函数**
   - 输入：配置对象、输入文件路径
   - 输出：输出文件路径（或写入后的状态）
   - 不依赖任何 CLI 特有的全局状态
   - 不在函数体内直接调用 `rich.print`，而是通过回调

2. **进度上报通过回调函数（callback）**
   - 每个 workflow 接受一个 `progress_callback: Callable[[ProgressEvent], None]` 参数
   - CLI 实现：用 `rich.progress` 接收回调
   - 未来 WebUI 实现：用 Gradio 组件接收回调
   - `ProgressEvent` 是一个 Pydantic 模型，包含：阶段、当前任务、进度百分比、消息

3. **状态全部在文件系统**
   - 任何"运行进度""中间结果"都通过 `workspace/` 持久化
   - 重启后可以从任意阶段继续
   - WebUI 可以通过读取这些文件展示状态

4. **业务逻辑与界面层完全解耦**
   - `cli.py` 只做"命令解析 + 调用 orchestrator + 显示进度"
   - 未来 `webui.py` 只做"界面渲染 + 调用 orchestrator + 显示进度"
   - 两者共享同一份 orchestrator 和 workflow

#### 7.6.2 未来 WebUI 的功能清单（Phase 2）

- 项目管理：新建、打开、删除项目
- 输入材料上传：拖拽上传日志、人物卡
- 分阶段运行：每个阶段一个按钮 + 进度条
- 中间产物可视化：JSON 树形展示、Markdown 预览
- 中间产物在线编辑：JSON 编辑器、Markdown 编辑器
- 配置管理：在界面上修改 `models.yaml` 和 `style_recipe.yaml`
- 成品下载：导出 Markdown / EPUB

**预计工作量**：核心 CLI 完成后，叠加 WebUI 约 2-3 天工作量。

---

## 8. 文件系统约定

### 8.1 路径规则

```
workspace/
├── project_package.json          # W0 产出
├── story_analysis.json           # W1 产出
├── novel_outline.json            # W2 产出
├── scenes/                       # W3 中间产物
│   ├── ch01_scene_001.md
│   └── ...
├── cache/                        # LLM 调用缓存
└── logs/                         # 运行日志
    └── run_2025_01_15_103000.log

output/
├── chapters/
│   ├── ch01.md
│   ├── ch02.md
│   └── ...
└── full_novel.md
```

### 8.2 .gitignore 规则

```
input/
workspace/
output/
.env
__pycache__/
*.pyc
.venv/
```

### 8.3 JSON 文件规范

- 所有 JSON 用 UTF-8 编码
- 缩进 2 空格
- 中文不转义（`ensure_ascii=False`）
- 字段顺序与 Pydantic 模型定义一致

---

## 9. 错误处理与断点续传

### 9.1 阶段级断点

每个 workflow 完成后写入完整的输出文件。重跑时：

- 默认检查输出文件是否存在，存在则跳过
- `--force` 强制重跑
- `--from-stage N` 从指定阶段开始

### 9.2 子任务级断点（W3 特别重要）

W3 逐场景生成时间长，需细粒度断点：

- 每个场景生成后写入 `workspace/scenes/chXX_scene_YYY.md`
- 重跑时检查文件存在则跳过
- `--rerun-chapter N` 重跑指定章节
- `--rerun-scene chXX_scene_YYY` 重跑指定场景

### 9.3 错误恢复

- LLM 调用失败：自动重试 3 次
- 结构化输出解析失败：重试时在 prompt 中追加错误信息
- 不可恢复错误：写入日志，跳过当前任务（不中断整个流水线），最后报告

### 9.4 日志

- 每次运行生成 `workspace/logs/run_<timestamp>.log`
- 记录：时间、阶段、调用模型、token 用量、错误
- 终端实时输出用 `rich`，日志文件用纯文本

---

## 附录：CLI 设计草案

```bash
# 初始化项目
trpg-novel init <project_dir>

# 检查输入材料
trpg-novel check

# 运行各阶段
trpg-novel run --stage 0
trpg-novel run --stage 1
trpg-novel run --stage 2
trpg-novel run --stage 3
trpg-novel run --all                  # 一口气跑完（不推荐）

# 重跑
trpg-novel run --stage 3 --force
trpg-novel run --stage 3 --rerun-chapter 5
trpg-novel run --stage 3 --rerun-scene ch05_scene_003

# 检视中间产物
trpg-novel review --stage 1           # 打开 story_analysis.json
trpg-novel stats                      # 统计信息

# 导出
trpg-novel export --format markdown
trpg-novel export --format epub       # 需要 pandoc
```

---

## 文档结束

本设计文档定义了系统的**完整骨架**。具体的数据模型字段、prompt 模板内容、各函数的实现细节，将在编码阶段逐步细化，并补充到 `docs/DATA_MODELS.md` 和 `docs/PROMPTS_GUIDE.md` 中。

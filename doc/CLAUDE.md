# CLAUDE.md

> 本文档供 Claude Code 阅读，帮助你快速理解本项目的设计、约束与开发风格。
> 在开始任何编码工作前，请先完整阅读本文档以及 `docs/` 目录下的全部文档。

---

## 1. 项目一句话定义

**TRPG-to-Novel** 是一个将跑团（TRPG）日志转换为长篇小说的本地化 AI Agent 工具。它接收团本日志、人物卡、单人剧情等材料，输出一部具有"王道少年漫"风格的完整小说。

---

## 2. 当前阶段

- ✅ 需求分析完成
- ✅ 架构设计完成
- ✅ 关键决策已确定（见 `docs/DECISIONS.md`）
- ⏳ **代码尚未开始实现**（你将协助完成实现）
- ⏳ 数据模型、prompt 模板、各 workflow 模块均待编写

---

## 3. 核心设计：四阶段流水线

整个系统是一个**线性的四阶段流水线**，每阶段产出结构化的中间文件，作为下一阶段的输入：

```
原始材料 → [W0 预处理] → [W1 结构化分析] → [W2 大纲生成] → [W3 正文生成] → 小说成品
            ↓              ↓                ↓               ↓
         project       story            novel          chapters/
         _package      _analysis        _outline       *.md
         .json         .json            .json
```

- **W0 输入预处理**：解析原始材料（日志、人物卡），输出标准化项目包
- **W1 结构化分析**：将日志切分为场景，提取事件、伏笔、人物弧线
- **W2 大纲生成**：将场景组织为章节，制定每章的主题与风格指令
- **W3 正文生成**：按章节生成最终小说正文

**关键原则**：
- 每个阶段独立运行，结果持久化到 `workspace/`
- 阶段之间是**用户 review 点**，允许人工检查和干预
- 任何阶段都可以单独重跑，**支持断点续传**

详细设计请见 `docs/DESIGN.md`。

---

## 4. 技术栈（已确定，请勿擅自更换）

| 用途 | 选型 | 备注 |
|---|---|---|
| 开发语言 | Python 3.11+ | |
| LLM 调用 | `litellm` | 统一封装 OpenAI/Claude/Gemini |
| 结构化输出 | `instructor` | 配合 Pydantic 让 LLM 返回结构化数据 |
| 数据模型 | `pydantic` v2 | 所有中间产物都用 Pydantic 模型定义 |
| Prompt 模板 | `jinja2` | prompt 与代码分离 |
| 配置文件 | `pyyaml` | 用户配置、风格配方、模型配置 |
| 命令行 | `typer` | CLI 接口 |
| 终端美化 | `rich` | 日志、进度条、表格 |
| 重试 | `tenacity` | LLM 调用失败重试 |
| 缓存 | `diskcache` | 可选，缓存 LLM 调用结果 |

**不要使用 LangChain、LlamaIndex 等重型框架**——本项目刻意避免过度抽象，保持代码直观。

---

## 5. 运行环境

- **本地运行**：Windows 11
- 不需要服务器部署
- 用户通过命令行交互
- 中间产物全部存为本地文件（JSON / Markdown）

---

## 6. 项目目录结构

```
trpg-to-novel/
├── CLAUDE.md                  # 本文档
├── README.md                  # 面向使用者
├── LICENSE                    # MIT
├── pyproject.toml
├── .gitignore
├── .env.example               # API key 模板
│
├── docs/                      # 所有设计文档
│   ├── DESIGN.md              # 详细架构
│   ├── DECISIONS.md           # 决策记录
│   ├── CONVERSATION_LOG.md    # 设计阶段对话精华
│   ├── DATA_MODELS.md         # 数据模型说明（实现后补充）
│   ├── PROMPTS_GUIDE.md       # prompt 设计原则（实现后补充）
│   └── PROGRESS.md            # 开发进度
│
├── config/                    # 用户可编辑的配置
│   ├── style_recipe.yaml      # 风格配方
│   ├── models.yaml            # 各阶段使用的模型
│   └── project.yaml.example   # 项目配置模板
│
├── prompts/                   # Jinja2 prompt 模板
│   ├── workflow_0/
│   ├── workflow_1/
│   ├── workflow_2/
│   └── workflow_3/
│
├── src/
│   └── trpg_to_novel/
│       ├── __init__.py
│       ├── models.py          # Pydantic 数据模型（核心骨架）
│       ├── llm_client.py      # LLM 调用封装
│       ├── prompt_loader.py   # Jinja2 加载器
│       ├── orchestrator.py    # 流水线调度
│       ├── cli.py             # 命令行入口
│       └── workflows/
│           ├── w0_preprocess.py
│           ├── w1_analyze.py
│           ├── w2_outline.py
│           └── w3_generate.py
│
├── examples/                  # 脱敏示例（用于测试与演示）
│   └── sample_campaign/
│
├── tests/                     # 单元测试
│
├── input/                     # 用户的真实材料（.gitignore）
├── workspace/                 # 中间产物（.gitignore）
└── output/                    # 最终成品（.gitignore）
```

---

## 7. 关键概念词汇表

为避免歧义，请在代码、注释、文档中**统一使用以下术语**：

| 术语 | 含义 |
|---|---|
| **团本 / Campaign** | 一次完整的跑团活动，是本项目处理的基本单位 |
| **日志 / Log** | 跑团过程的文字记录，可能包含 IC（角色扮演）和 OOC（玩家闲聊） |
| **PC** | Player Character，玩家扮演的角色 |
| **NPC** | Non-Player Character，KP/DM 扮演的角色 |
| **KP / DM** | Keeper / Dungeon Master，主持人 |
| **场景 / Scene** | 日志中具有独立时空与焦点的最小叙事单元 |
| **场景池 / Scene Pool** | W1 产出的所有场景的有序集合，是 W2 的输入 |
| **单人剧情 / Solo Arc** | 主线之外，某个 PC 单独经历的剧情（来自单人日志） |
| **闪回 / Flashback** | 将单人剧情以回忆形式嵌入主线章节的手法 |
| **伏笔 / Foreshadowing** | 早期埋下、后期回收的叙事元素，特别用于处理"人物秘密" |
| **风格配方 / Style Recipe** | 定义小说风格（如"王道少年漫"）的结构化配置 |
| **章节 / Chapter** | 最终小说的章节单位，W2 产出的组织单位 |

---

## 8. 开发风格约定

### 8.1 代码风格

- **简洁优先**：能用 50 行解决的问题不要写 200 行
- **中等工程化**：核心逻辑清晰 + 基本日志和错误处理 + CLI，**不做过度抽象**
- 不要为了"未来扩展性"添加当前用不到的抽象层
- 类型注解（type hints）**必须写全**
- 函数级 docstring 必须写（用中文）

### 8.2 文件组织

- **一个 workflow 一个模块**，不要把所有 workflow 塞进一个文件
- **prompt 与代码分离**：所有 prompt 都放在 `prompts/` 目录的 `.j2` 文件中，不要硬编码在 Python 里
- **配置与代码分离**：所有可调参数都放在 `config/` 的 YAML 里

### 8.3 数据流

- 阶段之间的传递**只通过文件**（JSON / Markdown），不要用全局变量或内存对象
- 所有中间产物都要可被人类阅读和编辑
- 任何破坏性操作前要确认（如覆盖已生成的章节）

### 8.4 LLM 调用

- 通过 `llm_client.py` 统一调用，**不要在业务代码中直接调 OpenAI SDK**
- 重要的 LLM 调用要带 retry（用 `tenacity`）
- 长 prompt 必须用 Jinja2 模板，**不要用 f-string 拼接**
- 模型选择通过 `config/models.yaml` 配置，**不要硬编码模型名**

### 8.5 日志与输出

- 用 `rich` 输出，**不要用 print**
- 关键步骤要有进度提示
- 错误信息要清晰，告诉用户"出了什么问题、可以怎么办"

---

## 9. 重要的"不要做"清单

❌ **不要**重新讨论已决策事项（见 `docs/DECISIONS.md`），如需变更请显式提出  
❌ **不要**引入 Dify、LangChain、AutoGen 等框架（已评估并放弃）  
❌ **不要**把 prompt 硬编码在 Python 代码里  
❌ **不要**为单一用例做"通用框架"，本项目是垂直工具  
❌ **不要**把真实团本材料提交到 git（用户的 input/ 已在 .gitignore 中）  
❌ **不要**在没有 review 点的情况下让流水线一口气跑到底，必须保留中间停顿  
❌ **不要**写"等以后再实现"的占位函数，要么完整实现要么明确标 TODO  

---

## 10. 协作流程建议

当你（Claude Code）开始工作时，建议遵循以下流程：

1. **先读全部文档**：`CLAUDE.md` + `docs/` 下所有 `.md`
2. **复述你的理解**：用 3-5 段话总结项目，让用户确认你理解正确
3. **从数据模型开始**：实现的第一步永远是 `src/trpg_to_novel/models.py`（Pydantic 模型），它是整个系统的骨架
4. **每完成一个模块**：更新 `docs/PROGRESS.md`
5. **遇到设计上的模糊点**：先问用户，不要擅自决定
6. **遇到与现有决策冲突的想法**：明确指出冲突点，由用户裁决

---

## 11. 用户偏好

- 用户希望**深度参与设计决策**，不希望 AI 擅自做架构选择
- 用户接受**中等工程化**，反感过度抽象和过度优化
- 用户**有技术背景**，可以直接阅读代码和讨论实现细节
- 用户的母语是中文，**所有沟通用中文**
- 用户在 Windows 11 上开发，注意路径分隔符等平台差异

---

## 12. 项目隐私与开源

- 本项目以 **MIT 协议** 开源在 GitHub
- 仓库名：`trpg-to-novel`
- 用户的真实团本材料**不会上传**，仅在本地使用
- `examples/sample_campaign/` 应放置**虚构的脱敏样本**用于演示

---

## 13. 联系上下文

如果你对设计有任何疑问，**首先查阅以下文档**：

- 整体架构 → `docs/DESIGN.md`
- 为什么这样设计 → `docs/DECISIONS.md`
- 设计阶段的讨论过程 → `docs/CONVERSATION_LOG.md`
- 当前进度 → `docs/PROGRESS.md`

如果文档中没有答案，**请向用户提问**，不要自行假设。

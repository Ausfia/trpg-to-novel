# PROGRESS.md — 项目进度跟踪

> 本文档是项目的**实时进度记录**。每完成一个任务，勾选对应条目，并在底部"近期更新"中追加一行说明。
> 
> **Claude Code 的工作流约定**：
> - 开始任何编码前，先查看本文档了解项目当前状态
> - 完成一个任务后，立即更新本文档（勾选 + 追加日志）
> - 遇到阻塞或决策需要时，在"待决策项"中记录
> - 不要删除已完成的条目（保留作为历史记录）

---

## 当前状态

**当前阶段**：Phase 0 - 项目骨架搭建

**最近一次更新**：（待填写）

**下一步预定任务**：搭建 Python 项目结构、配置依赖管理

---

## Roadmap 总览

### Phase 0：项目初始化（当前）
建立项目骨架、配置文件、基础工具链。**目标：能运行 `trpg-novel --help` 看到 CLI**。

### Phase 1：核心流水线
依次实现 W0 → W1 → W2 → W3，每个 workflow 跑通后再进入下一个。**目标：能完整跑出第一部小说**。

### Phase 2：WebUI（暂缓）
基于 Gradio 叠加可视化界面。**前提：Phase 1 跑出过至少一部满意作品**。

### Phase 3：增强功能（远期）
多团本批量、风格 A/B 对比、EPUB 导出等。

---

## Phase 0：项目初始化

### 0.1 项目骨架

- [ ] 创建目录结构（按 CLAUDE.md 第 4 节）
- [ ] 编写 `pyproject.toml`（使用 uv 管理依赖）
- [ ] 编写 `.gitignore`（排除 `input/`, `workspace/`, `output/`, `.env`）
- [ ] 编写 `.env.example`（列出所有可能用到的 API key 环境变量）
- [ ] 编写 `README.md`（项目简介、快速开始）

**完成定义**：`uv sync` 能装好所有依赖，`python -m trpg_to_novel --help` 能跑。

### 0.2 配置文件模板

- [ ] `config/project.yaml.example`（项目元信息模板）
- [ ] `config/models.yaml.example`（模型配置模板，含详细注释）
- [ ] `config/style_recipes/shonen_royal_road.yaml`（首个风格配方：王道少年漫）
- [ ] `config/style_recipes/literary_drama.yaml`（备选：文学剧情向）

**完成定义**：所有 `.example` 文件能被复制为正式配置后直接使用。

### 0.3 核心基础设施

- [ ] `src/trpg_to_novel/config_loader.py`：读取 YAML、合并 default + 覆盖、读取 .env
- [ ] `src/trpg_to_novel/llm_client.py`：基于 LiteLLM 的统一调用封装
  - [ ] `call_llm(workflow, step, prompt_template, variables, response_model)` 函数
  - [ ] 集成 instructor 做结构化输出
  - [ ] 集成 tenacity 做重试
  - [ ] 集成 diskcache 做调用缓存
- [ ] `src/trpg_to_novel/prompt_loader.py`：Jinja2 模板加载与渲染
- [ ] `src/trpg_to_novel/progress.py`：定义 `ProgressEvent` 和 callback 接口
- [ ] `src/trpg_to_novel/data_models.py`：Pydantic 数据模型（见下方 Phase 1 详细列表）

**完成定义**：能在 Python REPL 中调用 `call_llm(...)` 成功获得结构化返回。

### 0.4 CLI 框架

- [ ] `src/trpg_to_novel/cli.py`：用 typer 搭基础命令
  - [ ] `init` 命令（初始化项目目录）
  - [ ] `check` 命令（检查输入材料完整性）
  - [ ] `run --stage N` 命令（占位，未实现具体 workflow）
  - [ ] 全局 `--config-dir`、`--workspace-dir` 选项
- [ ] 集成 rich 做进度展示
- [ ] CLI 注册为 entry point（`trpg-novel` 命令）

**完成定义**：`trpg-novel init test_project` 能创建出正确的目录结构。

---

## Phase 1：核心流水线

### 1.0 数据模型（前置）

在开始任何 workflow 之前，先定义所有 Pydantic 模型：

- [ ] `CharacterCard`（人物卡）
- [ ] `LogChunk`（日志粗切块）
- [ ] `SoloArc`（单人剧情）
- [ ] `ProjectPackage`（W0 总输出）
- [ ] `Scene`（场景）
- [ ] `Event`（事件）
- [ ] `Foreshadowing`（伏笔）
- [ ] `CharacterArc`（人物弧线）
- [ ] `StoryAnalysis`（W1 总输出）
- [ ] `ChapterPlan`（章节规划）
- [ ] `FlashbackInsert`（闪回插入）
- [ ] `NovelOutline`（W2 总输出）
- [ ] `SceneDraft`（场景草稿）
- [ ] `ChapterDraft`（章节草稿）

**完成定义**：所有模型有完整的类型注解和 docstring；在 `docs/DATA_MODELS.md` 中可视化展示。

### 1.1 Workflow 0：输入预处理

- [ ] `workflows/w0_preprocess/parse_character.py`
  - [ ] Prompt 模板 `prompts/w0_preprocess/parse_character.j2`
  - [ ] 单元测试：用一个 sample 人物卡验证
- [ ] `workflows/w0_preprocess/chunk_logs.py`
  - [ ] 按 token 数切分（使用 tiktoken）
  - [ ] 不调用 LLM，纯本地处理
- [ ] `workflows/w0_preprocess/classify_solo.py`
  - [ ] 文件名匹配优先，匹配失败再用 LLM
  - [ ] Prompt 模板 `prompts/w0_preprocess/classify_solo.j2`
- [ ] `workflows/w0_preprocess/load_lore.py`
  - [ ] 简单拼接，无 LLM 调用
- [ ] `workflows/w0_preprocess/orchestrate.py`
  - [ ] 汇总各步骤，输出 `project_package.json`
- [ ] 集成到 CLI：`run --stage 0` 实际执行

**完成定义**：用 sample 输入跑通，产出合法的 `project_package.json`。

### 1.2 Workflow 1：结构化分析

- [ ] `workflows/w1_analyze/scene_segmentation.py`
  - [ ] 滑动窗口处理 LogChunk
  - [ ] Prompt 模板（含跨块场景合并指令）
- [ ] `workflows/w1_analyze/scene_classification.py`
  - [ ] 分类为 ic_main / ooc_chat / solo / mixed / meta
- [ ] `workflows/w1_analyze/event_extraction.py`
  - [ ] 仅对 ic_main 场景抽取事件
- [ ] `workflows/w1_analyze/foreshadowing.py`
  - [ ] 识别日志中已有的回收型伏笔
  - [ ] 扫描人物秘密的潜在埋设点
- [ ] `workflows/w1_analyze/character_arc.py`
  - [ ] 统计出场密度、关系网络、弧线轨迹
- [ ] `workflows/w1_analyze/orchestrate.py`
  - [ ] 汇总，输出 `story_analysis.json`
- [ ] 集成到 CLI：`run --stage 1`

**完成定义**：用真实跑团日志（或合成的 sample）跑通，场景池有意义。

### 1.3 Workflow 2：大纲生成

- [ ] `workflows/w2_outline/chapter_planning.py`
  - [ ] 把场景池组织为章节
  - [ ] 决策单人剧情处理方式
- [ ] `workflows/w2_outline/flashback_planning.py`
  - [ ] 为闪回选择主线插入点
- [ ] `workflows/w2_outline/foreshadowing_distribution.py`
  - [ ] 规划伏笔的埋设/强化/回收章节
  - [ ] 必要时设计补写片段
- [ ] `workflows/w2_outline/chapter_directives.py`
  - [ ] 为每章生成主题、情绪曲线、风格指令
- [ ] `workflows/w2_outline/orchestrate.py`
  - [ ] 汇总，输出 `novel_outline.json`
- [ ] 集成到 CLI：`run --stage 2`

**完成定义**：大纲合理、章节均衡、伏笔分布有戏剧张力。

### 1.4 Workflow 3：正文生成

- [ ] `workflows/w3_generate/context_builder.py`
  - [ ] 为每个场景组装 LLM 上下文
- [ ] `workflows/w3_generate/scene_generation.py`
  - [ ] 逐场景生成 SceneDraft
  - [ ] 写入 `workspace/scenes/chXX_scene_YYY.md`
- [ ] `workflows/w3_generate/flashback_generation.py`
  - [ ] 闪回片段单独生成（文体上区分）
- [ ] `workflows/w3_generate/chapter_stitching.py`
  - [ ] 拼接场景 + 章节标题 + 过渡调整
  - [ ] 输出 `output/chapters/chXX.md`
- [ ] `workflows/w3_generate/orchestrate.py`
  - [ ] 支持 `--rerun-chapter` 和 `--rerun-scene`
- [ ] 集成到 CLI：`run --stage 3`

**完成定义**：能产出完整 Markdown 小说，风格符合配方，读起来像小说而非日志。

### 1.5 端到端验证

- [ ] 准备一个完整的 sample 团本（可以是合成的简短示例）
- [ ] 从 `init` 到 `run --stage 3` 全流程跑通
- [ ] 评估产出质量
- [ ] 根据问题反向调优 prompt

**完成定义**：能向第三方展示"输入 → 输出"的完整链路。

---

## Phase 2：WebUI（暂缓）

> 仅在 Phase 1 完成且产出过至少一部满意作品后再启动。

### 2.1 Gradio 基础壳

- [ ] `src/trpg_to_novel/webui.py`：基础 Gradio app
- [ ] 项目管理界面（新建、打开）
- [ ] 输入材料上传界面
- [ ] 各阶段运行按钮 + 进度条

### 2.2 中间产物可视化

- [ ] JSON 树形展示组件
- [ ] Markdown 预览组件
- [ ] 在线编辑（JSON 编辑器）

### 2.3 配置管理界面

- [ ] `models.yaml` 在线编辑
- [ ] `style_recipe.yaml` 在线编辑

---

## Phase 3：增强功能（远期）

- [ ] 多团本批量处理
- [ ] 同一团本多风格对比
- [ ] EPUB 导出（集成 pandoc）
- [ ] 章节级 A/B 重生成
- [ ] 角色关系图谱可视化

---

## 待决策项

> 实现过程中遇到的需要用户决策的问题，记录在这里。

（暂无）

---

## 已知技术债

> 已经实现但不满意、未来需要重构的部分。

（暂无）

---

## 近期更新日志

> 格式：`YYYY-MM-DD：完成了什么 / 决策了什么 / 遇到了什么`

- **YYYY-MM-DD**：项目启动，完成三份核心文档（CLAUDE.md、DESIGN.md、DECISIONS.md）和本进度文档。

---

## 给 Claude Code 的提示

### 何时更新本文档

- ✅ 完成一个 checkbox 项时 → 立即勾选
- ✅ 遇到需要用户决策的问题 → 写入"待决策项"
- ✅ 实现了一个"凑合能用但不满意"的方案 → 写入"已知技术债"
- ✅ 每次 session 结束前 → 在"近期更新日志"追加一行

### 如何更新

```markdown
- [x] 已完成的任务
```

```markdown
## 近期更新日志
- **2025-01-20**：完成 W0 的 parse_character 模块，用 sample 人物卡测试通过；
  发现 prompt 对"秘密"字段的识别还不够稳定，记入技术债。
```

### 不要做的事

- ❌ 不要删除已完成的任务（保留作为历史）
- ❌ 不要跳过更新（即使任务很小）
- ❌ 不要在本文档中记录代码细节（那是代码注释的职责）
- ❌ 不要在本文档中记录设计决策（那是 DECISIONS.md 的职责）

### 当 checkbox 全部完成时

如果某个 Phase 的所有 checkbox 都勾选了，在该 Phase 标题下追加一行：

```markdown
**✅ 已完成于 YYYY-MM-DD**
```

然后在文档顶部更新"当前阶段"。

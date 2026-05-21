"""trpg-to-novel v2 — 跑团日志 → 小说 完整操作台

功能：
- 侧边栏：Campaign 选择/新建、团设置（世界观/玩家表）、5 阶段 LLM 配置
- Pipeline 标签页：上传日志（自动 session_id）、Parse/Classify/Segment/Draft
- 审稿标签页：原始场景 / 章节草稿 / 故事状态
- 人物卡标签页：填写/编辑/删除 PC 卡，或从旧 xlsx 导入

启动：
    python -m streamlit run ui/app.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import streamlit as st
import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from trpg2novel.campaign import Campaign
from trpg2novel.character.card_loader import (
    card_to_dict,
    import_from_xlsx,
    load_all_cards,
    load_card_yaml,
)
from trpg2novel.config import CAMPAIGNS_DIR, SYSTEMS_DIR
from trpg2novel.narrate.align import AlignmentResult, load_alignment
from trpg2novel.parse.session_splitter import SessionChunk, split_by_time_gap

# ---------------------------------------------------------------------------
# 显示常量
# ---------------------------------------------------------------------------

_KIND_LABEL = {"narration": "叙事", "battle": "战斗"}

_SEG_COLOR = {
    "dm_narration": "#1a6fba",
    "pc_dialogue": "#2a8a3a",
    "pc_action": "#8a5a00",
    "pc_ooc": "#888",
    "roll_result": "#6a2a8a",
    "unmarked_warning": "#cc0000",
}
_SEG_LABEL = {
    "dm_narration": "DM",
    "pc_dialogue": "台词",
    "pc_action": "行动",
    "pc_ooc": "OOC",
    "roll_cmd": "骰令",
    "roll_result": "骰果",
    "turn_marker": "回合",
    "initiative_list": "先攻",
    "initiative_clear": "清先攻",
    "bot_state": "bot",
    "record_meta": "元",
    "image": "图",
    "unmarked_warning": "⚠",
}

# ---------------------------------------------------------------------------
# .env 读写（5 阶段独立 LLM）
# ---------------------------------------------------------------------------

_ENV_PATH = _ROOT / ".env"

_STAGES: list[tuple[str, str]] = [
    ("detect", "断点检测"),
    ("align", "段落对齐"),
    ("draft", "章节起草"),
    ("polish", "润色"),
    ("review", "一致性审稿"),
]

_STAGE_MODEL_DEFAULTS = {
    "detect": "deepseek-chat",
    "align": "deepseek-chat",
    "draft": "deepseek-reasoner",
    "polish": "deepseek-reasoner",
    "review": "deepseek-chat",
}

_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def _read_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
    return vals


def _stage_value(env: dict[str, str], stage: str, field: str) -> str:
    new_key = f"LLM_{stage.upper()}_{field.upper()}"
    if new_key in env:
        return env[new_key]
    if field == "api_key":
        return env.get("OPENAI_API_KEY", "")
    if field == "base_url":
        return env.get("OPENAI_BASE_URL", _DEFAULT_BASE_URL)
    legacy = {
        "detect": "STAGE_MODEL_CHAPTER_DETECT",
        "align": "STAGE_MODEL_ALIGN",
        "draft": "STAGE_MODEL_DRAFT",
        "polish": "STAGE_MODEL_POLISH",
        "review": "STAGE_MODEL_REVIEW",
    }
    return env.get(legacy[stage], _STAGE_MODEL_DEFAULTS[stage])


def _write_env(env: dict[str, str], stage_cfg: dict[str, dict[str, str]]) -> None:
    out: dict[str, str] = {
        k: v for k, v in env.items()
        if not k.startswith("LLM_") and k not in {
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "STAGE_MODEL_CHAPTER_DETECT", "STAGE_MODEL_ALIGN",
            "STAGE_MODEL_DRAFT", "STAGE_MODEL_POLISH", "STAGE_MODEL_REVIEW",
            "STAGE_MODEL_SEGMENT",
        }
    }
    for stage, cfg in stage_cfg.items():
        upper = stage.upper()
        out[f"LLM_{upper}_API_KEY"] = cfg.get("api_key", "")
        out[f"LLM_{upper}_BASE_URL"] = cfg.get("base_url", _DEFAULT_BASE_URL)
        out[f"LLM_{upper}_MODEL"] = cfg.get("model", _STAGE_MODEL_DEFAULTS.get(stage, ""))
    draft = stage_cfg.get("draft", {})
    if draft.get("api_key"):
        out["OPENAI_API_KEY"] = draft["api_key"]
    if draft.get("base_url"):
        out["OPENAI_BASE_URL"] = draft["base_url"]
    lines = [f"{k}={v}" for k, v in out.items()]
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Campaign 辅助
# ---------------------------------------------------------------------------


def _current_campaign() -> Campaign | None:
    cid = st.session_state.get("selected_campaign_id")
    if not cid:
        return None
    try:
        return Campaign.load(cid)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 数据加载（带缓存，接受 campaign 路径参数）
# ---------------------------------------------------------------------------


@st.cache_data
def _load_scenes(parsed_dir: str, session_id: str) -> list[dict]:
    path = Path(parsed_dir) / f"{session_id}.scenes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@st.cache_data
def _load_tagged(parsed_dir: str, session_id: str) -> dict[str, dict]:
    path = Path(parsed_dir) / f"{session_id}.tagged.json"
    if not path.exists():
        return {}
    return {e["id"]: e for e in json.loads(path.read_text(encoding="utf-8"))}


@st.cache_data
def _load_state(campaign_root: str) -> dict:
    path = Path(campaign_root) / "story_state.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def _list_chapters(camp: Campaign) -> list[Path]:
    return sorted(camp.chapters_dir.glob("ch*_draft.md"))


def _list_sessions(camp: Campaign) -> list[str]:
    return camp.list_sessions()


# ---------------------------------------------------------------------------
# Pipeline 命令执行
# ---------------------------------------------------------------------------


def _run_cmd(args: list[str], placeholder, campaign_id: str | None = None) -> tuple[int, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(_ROOT / "src")}
    if campaign_id:
        env["DEFAULT_CAMPAIGN_ID"] = campaign_id
    cmd = [sys.executable, "-m", "trpg2novel.pipeline"] + args
    placeholder.code("$ " + " ".join(args), language="bash")
    buf = []
    with subprocess.Popen(
        cmd,
        cwd=str(_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        for line in proc.stdout:
            buf.append(line)
            placeholder.code("".join(buf), language="text")
        proc.wait()
    return proc.returncode, "".join(buf)


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------


def _sidebar():
    # ---- Campaign 选择 ----
    st.sidebar.header("团（Campaign）")
    all_camps = Campaign.list_all()
    camp_ids = [c.id for c in all_camps]
    camp_names = {c.id: f"{c.name}（{c.id}）" for c in all_camps}

    if not camp_ids:
        _sidebar_new_campaign()
        return

    # 初始化 session_state
    if "selected_campaign_id" not in st.session_state or st.session_state["selected_campaign_id"] not in camp_ids:
        st.session_state["selected_campaign_id"] = camp_ids[0]

    sel_idx = camp_ids.index(st.session_state["selected_campaign_id"])
    selected = st.sidebar.selectbox(
        "当前团",
        options=camp_ids,
        format_func=lambda cid: camp_names.get(cid, cid),
        index=sel_idx,
        key="selected_campaign_id",
    )

    camp = _current_campaign()

    # ---- 团设置折叠 ----
    with st.sidebar.expander("团设置", expanded=False):
        _sidebar_campaign_settings(camp)

    st.sidebar.divider()

    # ---- 新建团 ----
    _sidebar_new_campaign()

    st.sidebar.divider()

    # ---- LLM 设置 ----
    st.sidebar.subheader("LLM 设置（按阶段独立）")
    env = _read_env()
    new_cfg: dict[str, dict[str, str]] = {}
    for stage, label in _STAGES:
        with st.sidebar.expander(f"{label}（{stage}）", expanded=(stage == "draft")):
            api_key = st.text_input(
                "API Key",
                value=_stage_value(env, stage, "api_key"),
                type="password",
                key=f"cfg_{stage}_api_key",
            )
            base_url = st.text_input(
                "Base URL",
                value=_stage_value(env, stage, "base_url"),
                key=f"cfg_{stage}_base_url",
            )
            model = st.text_input(
                "Model",
                value=_stage_value(env, stage, "model"),
                key=f"cfg_{stage}_model",
            )
            new_cfg[stage] = {"api_key": api_key, "base_url": base_url, "model": model}

    if st.sidebar.button("保存全部 LLM 设置", use_container_width=True):
        _write_env(env, new_cfg)
        st.sidebar.success("已保存到 .env")

    configured = [label for stage, label in _STAGES if new_cfg[stage]["api_key"].strip()]
    if configured:
        st.sidebar.success("已配置：" + "、".join(configured))
    else:
        st.sidebar.warning("尚未配置任何阶段的 API Key")

    st.sidebar.divider()

    # ---- 快速统计 ----
    if camp:
        st.sidebar.markdown(f"**已处理场次（{camp.id}）**")
        sessions = _list_sessions(camp)
        if sessions:
            for s in sessions:
                st.sidebar.markdown(f"- `{s}`")
        else:
            st.sidebar.caption("暂无场次")
        chapters = _list_chapters(camp)
        st.sidebar.markdown(f"**已生成章节** {len(chapters)} 章")


def _sidebar_campaign_settings(camp: Campaign | None):
    if camp is None:
        st.info("请先选择或新建一个团。")
        return

    st.markdown(f"**{camp.name}**  `{camp.id}`  —  {camp.system}")

    # 世界观编辑
    st.markdown("#### worldview.md")
    cur_wv = camp.worldview_md.read_text(encoding="utf-8") if camp.worldview_md.exists() else ""
    new_wv = st.text_area(
        "世界观备忘（自由文本，注入起草 prompt）",
        value=cur_wv,
        height=150,
        key="camp_worldview_text",
    )
    if st.button("保存 worldview.md", key="btn_save_wv"):
        camp.worldview_md.write_text(new_wv, encoding="utf-8")
        st.success("已保存")

    # players.yaml 编辑（表单）
    st.markdown("#### players.yaml")
    _render_players_form(camp)


def _render_players_form(camp: Campaign):
    """结构化表单编辑 players.yaml。

    字段：PC 列表（name / role / aliases / user_is）+ DM handle + known_bots。
    """
    # 加载初值到 session_state（仅首次）
    sk_players = f"players_form_{camp.id}_players"
    sk_dm = f"players_form_{camp.id}_dm"
    sk_bots = f"players_form_{camp.id}_bots"
    sk_loaded = f"players_form_{camp.id}_loaded"

    if not st.session_state.get(sk_loaded, False):
        if camp.players_yaml.exists():
            try:
                data = yaml.safe_load(camp.players_yaml.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
        else:
            data = {}
        st.session_state[sk_players] = [
            {
                "name": p.get("name", ""),
                "role": p.get("role", "pc"),
                "aliases": ", ".join(p.get("aliases", []) or []),
                "user_is": bool(p.get("user_is", False)),
            }
            for p in (data.get("players") or [])
        ]
        st.session_state[sk_dm] = (data.get("dm") or {}).get("handle", "")
        st.session_state[sk_bots] = ", ".join(data.get("known_bots") or [])
        st.session_state[sk_loaded] = True

    # 重新加载按钮（弃用未保存修改）
    if st.button("↻ 从文件重新加载", key=f"btn_reload_players_{camp.id}"):
        st.session_state[sk_loaded] = False
        st.rerun()

    # PC 列表
    st.markdown("**PC 列表**")
    players: list[dict] = st.session_state[sk_players]

    to_delete = -1
    for i, p in enumerate(players):
        cols = st.columns([3, 2, 4, 1, 1])
        p["name"] = cols[0].text_input("姓名", value=p["name"], key=f"pl_name_{camp.id}_{i}", label_visibility="collapsed")
        p["role"] = cols[1].selectbox(
            "角色", ["pc", "npc"],
            index=0 if p["role"] == "pc" else 1,
            key=f"pl_role_{camp.id}_{i}",
            label_visibility="collapsed",
        )
        p["aliases"] = cols[2].text_input("别名（逗号分隔）", value=p["aliases"], key=f"pl_alias_{camp.id}_{i}", label_visibility="collapsed")
        p["user_is"] = cols[3].checkbox("我", value=p["user_is"], key=f"pl_user_{camp.id}_{i}", help="是否是你自己扮演的角色")
        if cols[4].button("✕", key=f"pl_del_{camp.id}_{i}", help="删除这一行"):
            to_delete = i
    if to_delete >= 0:
        players.pop(to_delete)
        st.rerun()

    if st.button("+ 添加 PC", key=f"btn_add_pc_{camp.id}"):
        players.append({"name": "", "role": "pc", "aliases": "", "user_is": False})
        st.rerun()

    # DM handle
    st.markdown("**DM**")
    st.session_state[sk_dm] = st.text_input(
        "DM 在日志中的发言人 handle",
        value=st.session_state[sk_dm],
        key=f"pl_dm_input_{camp.id}",
    )

    # known_bots
    st.markdown("**已知骰娘 handles**（逗号分隔）")
    st.session_state[sk_bots] = st.text_input(
        "known_bots",
        value=st.session_state[sk_bots],
        key=f"pl_bots_input_{camp.id}",
        label_visibility="collapsed",
        help="如 「JCC-Dice、Saki」",
    )

    # 保存
    if st.button("保存 players.yaml", key=f"btn_save_players_form_{camp.id}", type="primary"):
        out_players = []
        for p in players:
            name = (p["name"] or "").strip()
            if not name:
                continue
            aliases = [a.strip() for a in (p["aliases"] or "").split(",") if a.strip()]
            entry = {"name": name, "role": p["role"]}
            if aliases:
                entry["aliases"] = aliases
            if p["user_is"]:
                entry["user_is"] = True
            out_players.append(entry)

        out_bots = [b.strip() for b in (st.session_state[sk_bots] or "").split(",") if b.strip()]
        out = {
            "players": out_players,
            "dm": {"handle": (st.session_state[sk_dm] or "").strip()},
            "known_bots": out_bots,
        }
        camp.players_yaml.write_text(
            yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        st.success(f"已保存：{camp.players_yaml.name}（{len(out_players)} 个角色 / {len(out_bots)} 个骰娘）")


def _sidebar_new_campaign():
    with st.sidebar.expander("新建团", expanded=False):
        with st.form("form_new_campaign"):
            new_id = st.text_input("团 ID（英文/数字/下划线）", placeholder="my_campaign_01")
            new_name = st.text_input("团名称", placeholder="巨龙僭政 2")
            new_system = st.selectbox("规则系统", ["dnd5e"])
            submitted = st.form_submit_button("创建")
        if submitted:
            if not new_id.strip() or not new_name.strip():
                st.error("ID 和名称不能为空")
            else:
                try:
                    Campaign.create(campaign_id=new_id.strip(), name=new_name.strip(), system=new_system)
                    st.session_state["selected_campaign_id"] = new_id.strip()
                    st.success(f"已创建团：{new_name}")
                    st.rerun()
                except Exception as e:
                    st.error(f"创建失败：{e}")


# ---------------------------------------------------------------------------
# 标签页 1：Pipeline
# ---------------------------------------------------------------------------


def _tab_pipeline(camp: Campaign):
    st.subheader("Pipeline 控制台")
    cid = camp.id

    # ---- 上传日志 ----
    st.markdown("#### 第一步：上传日志文件")
    uploaded = st.file_uploader(
        "上传 .md 日志文件",
        type=["md"],
        help="单场或多场融合的 .md 都可。上传后下方会预览自动场次切分。",
        key="pipe_upload",
    )

    if uploaded:
        # 读出文本，做切分预览
        try:
            text = uploaded.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            text = uploaded.getvalue().decode("utf-8", errors="replace")

        cfg_col, _ = st.columns([1, 2])
        with cfg_col:
            gap_hours = st.number_input(
                "场次切分阈值（小时）",
                min_value=0.5,
                max_value=24.0,
                value=8.0,
                step=0.5,
                key="split_gap",
                help="相邻两条日志时间差 ≥ 此值视为换场。跨夜自动 +24h 计算。",
            )

        chunks = split_by_time_gap(text, min_gap_hours=float(gap_hours))

        if len(chunks) == 0:
            st.warning("未检测到任何带时间戳的事件，请确认日志格式。")
        elif len(chunks) == 1:
            st.info(f"检测到单场跑团（{chunks[0].start_ts} → {chunks[0].end_ts}，{chunks[0].line_count} 行）。")
        else:
            st.success(f"自动识别到 **{len(chunks)} 场** 跑团：")
            for c in chunks:
                st.markdown(
                    f"- chunk {c.index + 1}：`{c.start_ts}` → `{c.end_ts}`（{c.line_count} 行）"
                )

        # 落盘按钮
        save_cols = st.columns(2)
        start_n = len(camp.list_raw_logs()) + 1

        with save_cols[0]:
            if len(chunks) > 1:
                label = f"接受切分：写入 s{start_n:02d} ~ s{start_n + len(chunks) - 1:02d}"
            else:
                label = f"作为单场写入 s{start_n:02d}.md"
            if st.button(label, use_container_width=True, key="btn_accept_split"):
                camp.raw_logs_dir.mkdir(parents=True, exist_ok=True)
                written = []
                for c in chunks:
                    sid = f"s{start_n + c.index:02d}"
                    out = camp.raw_logs_dir / f"{sid}.md"
                    out.write_text(c.text, encoding="utf-8")
                    written.append(out.name)
                st.success(f"已写入 {len(written)} 份：{', '.join(written)}")
                st.cache_data.clear()

        with save_cols[1]:
            if len(chunks) > 1:
                if st.button(
                    f"忽略切分：整份作为单场 s{start_n:02d}.md",
                    use_container_width=True,
                    key="btn_force_single",
                ):
                    camp.raw_logs_dir.mkdir(parents=True, exist_ok=True)
                    sid = f"s{start_n:02d}"
                    out = camp.raw_logs_dir / f"{sid}.md"
                    out.write_text(text, encoding="utf-8")
                    st.success(f"已作为单场写入：{out.name}")
                    st.cache_data.clear()

    # ---- 场次选择 ----
    st.markdown("#### 第二步：选择场次并运行阶段")
    existing_mds = sorted(camp.raw_logs_dir.glob("*.md"))
    all_sids_from_md = [p.stem for p in existing_mds]

    if not all_sids_from_md:
        st.warning(f"{camp.id}/raw_logs/ 下暂无 .md 文件，请先上传。")
        return

    selected_sid = st.selectbox("选择场次", all_sids_from_md, key="pipe_sel_sid")
    out_ph = st.empty()

    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        if st.button("▶ 解析 (Parse)", use_container_width=True, key="btn_parse"):
            with st.spinner("解析中…"):
                code, _ = _run_cmd(
                    ["parse", str(camp.raw_logs_dir / f"{selected_sid}.md"), "--session-id", selected_sid],
                    out_ph, cid,
                )
            if code == 0:
                st.success("Parse 完成")
                st.cache_data.clear()
            else:
                st.error("Parse 失败，见上方输出")

    with btn_col2:
        if st.button("▶ 分类 (Classify)", use_container_width=True, key="btn_classify"):
            with st.spinner("分类配对中…"):
                code, _ = _run_cmd(["classify", selected_sid], out_ph, cid)
            if code == 0:
                st.success("Classify 完成")
                st.cache_data.clear()
            else:
                st.error("Classify 失败")

    with btn_col3:
        if st.button("▶ 切分场景 (Segment)", use_container_width=True, key="btn_segment"):
            with st.spinner("切分场景中…"):
                code, _ = _run_cmd(["segment", selected_sid], out_ph, cid)
            if code == 0:
                st.success("Segment 完成")
                st.cache_data.clear()
            else:
                st.error("Segment 失败")

    st.divider()

    # ---- Draft ----
    st.markdown("#### 第三步：生成章节草稿")

    sessions_with_scenes = _list_sessions(camp)
    if not sessions_with_scenes:
        st.info("请先完成至少一个场次的 Segment 阶段。")
        return

    draft_sessions = st.multiselect(
        "纳入本章的场次（按顺序选）",
        sessions_with_scenes,
        default=sessions_with_scenes,
        key="draft_sids",
    )
    draft_mode = st.radio(
        "生成模式",
        ["auto-detect（自动判断章节边界）", "force（强制生成）"],
        key="draft_mode",
        horizontal=True,
    )
    last_summary = st.text_area(
        "上一章结尾摘要（可选，供 LLM 续写参考）",
        height=80,
        key="last_summary",
    )

    env = _read_env()
    if not _stage_value(env, "draft", "api_key").strip():
        st.warning("需要先在左侧侧边栏配置「章节起草」阶段的 API Key 才能运行 Draft。")
        draft_disabled = True
    elif not draft_sessions:
        st.warning("请选择至少一个场次。")
        draft_disabled = True
    else:
        draft_disabled = False

    if st.button("▶ 生成章节草稿 (Draft)", disabled=draft_disabled, use_container_width=True, key="btn_draft"):
        args = ["draft"]
        for s in draft_sessions:
            args += ["--session", s]
        if "force" in draft_mode:
            args.append("--force")
        if last_summary.strip():
            args += ["--last-summary", last_summary.strip()]

        draft_ph = st.empty()
        with st.spinner("正在调用 LLM 生成章节，请稍候（可能需要 1–3 分钟）…"):
            code, output = _run_cmd(args, draft_ph, cid)
        if code == 0:
            st.success("章节草稿已生成！切换到「审稿」标签查看。")
            st.cache_data.clear()
        else:
            st.error("Draft 失败，见上方输出")


# ---------------------------------------------------------------------------
# 标签页 2：审稿
# ---------------------------------------------------------------------------


def _render_scene_log(
    scenes: list[dict],
    tagged: dict[str, dict],
    scene_idx: int,
    unmapped_ids: set[str] | None = None,
):
    if not scenes:
        st.info("未找到场景数据。请先完成 Segment 阶段。")
        return
    scene = scenes[scene_idx]
    kind_lbl = _KIND_LABEL.get(scene["kind"], scene["kind"])
    st.caption(
        f"{kind_lbl}  |  {scene['start_ts']} → {scene['end_ts']}  |  "
        f"触发: {', '.join(scene['triggers']) or '场次开始'}"
    )

    for event_id in scene["event_ids"]:
        ev = tagged.get(event_id)
        if ev is None:
            continue
        segs = ev.get("segments", [])
        visible = [s for s in segs if s["kind"] not in ("pc_ooc", "roll_cmd", "bot_state", "record_meta", "image")]
        if not visible:
            continue

        # 是否被 align 标记为未采用
        is_unmapped = unmapped_ids is not None and event_id in unmapped_ids

        lines = []
        for seg in visible:
            kind = seg["kind"]
            text = seg["text"].strip()
            if not text:
                continue
            color = _SEG_COLOR.get(kind, "#444")
            if kind == "dm_narration":
                lines.append(f"<span style='color:{color}'>{text}</span>")
            elif kind == "pc_dialogue":
                lines.append(f"<span style='color:{color}'>「{text}」</span>")
            elif kind == "pc_action":
                lines.append(f"<span style='color:{color}'>*{text}*</span>")
            elif kind == "roll_result":
                lines.append(f"<span style='color:{color};font-size:0.85em'>[{text}]</span>")
            elif kind == "unmarked_warning":
                lines.append(f"<span style='color:{color}'>⚠ {text}</span>")
            else:
                label = _SEG_LABEL.get(kind, kind)
                lines.append(f"<span style='font-size:0.85em;color:#888'>[{label}] {text}</span>")

        if lines:
            header = f"**{ev['speaker']}** `{ev['timestamp']}`"
            if is_unmapped:
                header += "  <span style='background:#cc0000;color:#fff;padding:1px 6px;border-radius:3px;font-size:0.8em'>未采用</span>"
            border = "border-left:3px solid #cc0000;padding-left:6px;" if is_unmapped else ""
            st.markdown(
                f"<div style='{border}'>{header}  \n" + "  \n".join(lines) + "</div>",
                unsafe_allow_html=True,
            )


def _render_chapter(chapter_path: Path | None, camp: Campaign):
    if chapter_path is None:
        st.info("暂无章节草稿。请先在 Pipeline 标签页运行 Draft。")
        return
    text = chapter_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    body_lines = []
    for line in lines:
        if line.startswith("<!--") and line.endswith("-->"):
            m = re.search(r"scenes: ([^|]+)\|.*events: (\d+)\|.*focus: ([^-\-]+)", line)
            if m:
                st.caption(
                    f"场景: {m.group(1).strip()}  |  事件: {m.group(2).strip()} 条  |  焦点: {m.group(3).strip()}"
                )
        else:
            body_lines.append(line)
    body_text = "\n".join(body_lines)

    # 操作栏
    btn1, btn2, btn3 = st.columns([2, 2, 1])
    with btn1:
        st.download_button(
            "下载草稿 .md",
            data=text.encode("utf-8"),
            file_name=chapter_path.name,
            mime="text/markdown",
            key=f"dl_{chapter_path.name}",
        )

    # 检查是否有 revised 版本
    revised_path = chapter_path.with_name(chapter_path.name.replace("_draft", "_revised"))
    show_revised = revised_path.exists()
    with btn2:
        if show_revised:
            st.caption(f"已有修订版：{revised_path.name}")
            display_text = revised_path.read_text(encoding="utf-8")
        else:
            display_text = body_text
    with btn3:
        # Align 状态
        align_path = chapter_path.with_name(chapter_path.stem.replace("_draft", "") + "_align.json")
        if align_path.exists():
            st.caption("✓ 已对齐")
        else:
            st.caption("未对齐")

    # 可编辑区域
    edited = st.text_area(
        "章节正文（可直接编辑，点「保存修订」落盘）",
        value=display_text,
        height=600,
        key=f"edit_{chapter_path.name}",
    )
    if st.button("保存修订", key=f"save_{chapter_path.name}"):
        revised_path.write_text(edited, encoding="utf-8")
        st.success(f"已保存：{revised_path.name}")


def _render_state(state: dict):
    if not state:
        st.info("未找到 story_state.yaml。")
        return
    st.subheader("角色状态")
    for name, cs in state.get("characters", {}).items():
        alive_str = "存活" if cs.get("alive", True) else "阵亡"
        conds = cs.get("conditions", [])
        cond_str = "、".join(conds) if conds else "无"
        with st.expander(f"{name}  Lv{cs.get('level', '?')}  {alive_str}", expanded=False):
            st.text(f"状态：{cond_str}")
            if cs.get("notes"):
                st.text(f"备注：{cs['notes']}")

    lore = state.get("lore_unlocked", [])
    if lore:
        st.subheader("已解锁设定")
        for item in lore:
            st.markdown(f"- {item}")

    session_log = state.get("session_log", [])
    if session_log:
        st.subheader("已处理场次")
        for s in session_log:
            st.markdown(f"- `{s}`")


def _tab_review(camp: Campaign):
    sessions = _list_sessions(camp)
    chapters = _list_chapters(camp)

    ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 1])
    with ctrl1:
        if sessions:
            sel_session = st.selectbox("场次", sessions, key="rev_session")
        else:
            st.warning("暂无场次数据")
            sel_session = None
    with ctrl2:
        if chapters:
            chapter_names = [p.name for p in chapters]
            sel_chap_name = st.selectbox("章节草稿", chapter_names, key="rev_chap")
            sel_chap = camp.chapters_dir / sel_chap_name
        else:
            st.info("暂无章节草稿")
            sel_chap = None
    with ctrl3:
        if st.button("刷新", use_container_width=True, key="rev_refresh"):
            st.cache_data.clear()
            st.rerun()

    # Align 按钮与状态
    if sel_chap is not None:
        align_path = sel_chap.with_name(sel_chap.stem.replace("_draft", "") + "_align.json")
        align_result = load_alignment(align_path)
        unmapped_ids: set[str] = set(align_result.unmapped_event_ids) if align_result else set()

        align_col1, align_col2 = st.columns([3, 1])
        with align_col1:
            if align_result:
                mapped = sum(1 for p in align_result.paragraphs if p.source_event_ids)
                st.caption(
                    f"对齐状态：{len(align_result.paragraphs)} 段，{mapped} 段有映射，"
                    f"{len(unmapped_ids)} 条事件未被引用（红框标注）"
                )
            else:
                st.caption("尚未对齐。运行 Align 后可标注未被采用的原始事件。")
        with align_col2:
            env = _read_env()
            if st.button("▶ 运行对齐 (Align)", use_container_width=True, key="btn_align"):
                with st.spinner("对齐中（调用 LLM）…"):
                    ph = st.empty()
                    code, _ = _run_cmd(["align", str(sel_chap)], ph, camp.id)
                if code == 0:
                    st.success("对齐完成，刷新页面查看标注。")
                    st.cache_data.clear()
                else:
                    st.error("对齐失败，见上方输出")
    else:
        unmapped_ids = set()

    st.divider()
    left, mid, right = st.columns([2, 3, 2], gap="medium")

    with left:
        st.subheader("原始场景日志")
        if sel_session:
            scenes = _load_scenes(str(camp.parsed_dir), sel_session)
            tagged = _load_tagged(str(camp.parsed_dir), sel_session)
            if scenes:
                labels = [
                    f"{i+1}. {_KIND_LABEL.get(s['kind'], s['kind'])} {s['start_ts']}–{s['end_ts']} ({len(s['event_ids'])}条)"
                    for i, s in enumerate(scenes)
                ]
                idx = st.selectbox("场景", range(len(scenes)), format_func=lambda i: labels[i], key="rev_scene")
                st.markdown("---")
                _render_scene_log(scenes, tagged, idx, unmapped_ids=unmapped_ids)

    with mid:
        st.subheader("章节草稿")
        _render_chapter(sel_chap, camp)

    with right:
        st.subheader("故事状态")
        _render_state(_load_state(str(camp.root)))


# ---------------------------------------------------------------------------
# 标签页 3：人物卡管理
# ---------------------------------------------------------------------------


def _load_card_schema() -> dict:
    """读取 dnd5e card_form_schema.yaml（用于渲染表单）。"""
    path = SYSTEMS_DIR / "dnd5e" / "card_form_schema.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _tab_polish(camp: Campaign):
    st.subheader("润色工作台")
    chapters = _list_chapters(camp)
    if not chapters:
        st.info("暂无章节草稿，请先在 Pipeline 运行 Draft。")
        return

    chap_names = [p.name for p in chapters]
    sel_name = st.selectbox("选择章节", chap_names, key="pol_chap")
    draft_path = camp.chapters_dir / sel_name
    revised_path = draft_path.with_name(draft_path.name.replace("_draft", "_revised"))
    polished_path = draft_path.with_name(draft_path.stem.replace("_draft", "") + "_polished.md")

    source_path = revised_path if revised_path.exists() else draft_path
    source_label = "修订稿" if revised_path.exists() else "草稿"
    source_text = source_path.read_text(encoding="utf-8")

    pol_col1, pol_col2 = st.columns([1, 2])
    with pol_col1:
        env = _read_env()
        has_key = bool(_stage_value(env, "polish", "api_key").strip())
        if not has_key:
            st.warning("请先在侧边栏配置「润色」阶段的 API Key。")

        last_summary = st.text_area("上一章摘要（可选）", height=80, key="pol_summary")

        if st.button("▶ 运行润色 (Polish)", disabled=not has_key, use_container_width=True, key="btn_pol"):
            ph = st.empty()
            with st.spinner("润色中（调用 LLM，可能需要 1-3 分钟）…"):
                args = ["polish", str(draft_path)]
                if last_summary.strip():
                    args += ["--last-summary", last_summary.strip()]
                code, _ = _run_cmd(args, ph, camp.id)
            if code == 0:
                st.success("润色完成！")
                st.rerun()
            else:
                st.error("润色失败，见上方输出")

    with pol_col2:
        left_c, right_c = st.columns(2)
        with left_c:
            st.markdown(f"**{source_label}（{source_path.name}）**")
            st.download_button(
                "下载",
                data=source_text.encode("utf-8"),
                file_name=source_path.name,
                mime="text/markdown",
                key=f"dl_source_{sel_name}",
            )
            st.text_area("原稿", value=source_text, height=700, key=f"pol_src_{sel_name}", disabled=True)

        with right_c:
            if polished_path.exists():
                polished_text = polished_path.read_text(encoding="utf-8")
                st.markdown(f"**润色稿（{polished_path.name}）**")
                st.download_button(
                    "下载",
                    data=polished_text.encode("utf-8"),
                    file_name=polished_path.name,
                    mime="text/markdown",
                    key=f"dl_pol_{sel_name}",
                )
                st.text_area("润色稿", value=polished_text, height=700, key=f"pol_out_{sel_name}", disabled=True)
            else:
                st.info('尚未润色。点击"运行润色"生成润色稿。')


def _tab_cards(camp: Campaign):
    st.subheader(f"人物卡管理 — {camp.name}")
    cards = load_all_cards(camp.character_cards_dir)

    left_col, right_col = st.columns([1, 2], gap="large")

    with left_col:
        st.markdown("#### 已有卡片")
        if not cards:
            st.caption("暂无人物卡（YAML 格式）")
        for pc_name, card in cards.items():
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{pc_name}**" + (f" ({card.race}{card.class_name})" if card.race or card.class_name else ""))
            if c2.button("编辑", key=f"edit_{pc_name}"):
                st.session_state["card_edit_name"] = pc_name
                st.session_state["card_edit_data"] = card_to_dict(card)

        st.markdown("---")
        if st.button("+ 新建人物卡", use_container_width=True, key="btn_new_card"):
            st.session_state["card_edit_name"] = None
            st.session_state["card_edit_data"] = {}

        st.markdown("#### 从旧 xlsx 导入（一次性）")
        xlsx_up = st.file_uploader("上传 .xlsx 人物卡", type=["xlsx"], key="xlsx_import")
        if xlsx_up:
            if st.button("解析并预填表单", key="btn_parse_xlsx"):
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                    tf.write(xlsx_up.read())
                    tmp_path = Path(tf.name)
                try:
                    d = import_from_xlsx(tmp_path)
                    st.session_state["card_edit_name"] = None
                    st.session_state["card_edit_data"] = d
                    st.success("已解析，请在右侧表单中检查并保存。")
                except Exception as e:
                    st.error(f"解析失败：{e}")
                finally:
                    tmp_path.unlink(missing_ok=True)

        # 删除
        if cards:
            st.markdown("#### 删除")
            del_name = st.selectbox("选择要删除的卡", list(cards.keys()), key="del_card_name")
            if st.button("确认删除", key="btn_del_card", type="secondary"):
                yaml_path = camp.character_cards_dir / f"{del_name}.yaml"
                if yaml_path.exists():
                    yaml_path.unlink()
                    if st.session_state.get("card_edit_name") == del_name:
                        st.session_state.pop("card_edit_name", None)
                        st.session_state.pop("card_edit_data", None)
                    st.success(f"已删除：{del_name}")
                    st.rerun()

    with right_col:
        _render_card_form(camp)


def _render_card_form(camp: Campaign):
    """人物卡编辑表单。"""
    if "card_edit_data" not in st.session_state:
        st.info("点击左侧「+ 新建人物卡」或选一张卡编辑。")
        return

    edit_name = st.session_state.get("card_edit_name")
    data: dict = dict(st.session_state.get("card_edit_data", {}))

    title = f"编辑：{edit_name}" if edit_name else "新建人物卡"
    st.markdown(f"#### {title}")

    with st.form("card_form"):
        # 基本信息
        st.markdown("**基本信息**")
        r1, r2 = st.columns(2)
        name = r1.text_input("角色名 *", value=data.get("name", ""))
        player_handle = r2.text_input("玩家 handle（可选）", value=data.get("player_handle", ""))
        r3, r4, r5 = st.columns(3)
        race = r3.text_input("种族", value=data.get("race", ""))
        class_name = r4.text_input("职业", value=data.get("class", data.get("class_name", "")))
        age = r5.text_input("年龄", value=data.get("age", ""))
        r6, r7 = st.columns(2)
        gender = r6.text_input("性别", value=data.get("gender", ""))
        homeland = r7.text_input("故乡", value=data.get("homeland", ""))

        st.markdown("**外貌与气质**")
        appearance = st.text_area("外貌描述", value=data.get("appearance", ""), height=100)
        personality = st.text_area("个性", value=data.get("personality", ""), height=60)
        ideal_val = st.text_input("理念", value=data.get("ideal", ""))
        bond = st.text_input("羁绊", value=data.get("bond", ""))
        flaw = st.text_input("缺陷", value=data.get("flaw", ""))

        st.markdown("**背景故事**")
        background_story = st.text_area("背景故事", value=data.get("background_story", ""), height=150)

        st.markdown("**关键特征 key_traits**（每行一条，这是注入 prompt 的核心）")
        kt_default = "\n".join(data.get("key_traits") or [])
        kt_text = st.text_area(
            "关键特征（每行一条）",
            value=kt_default,
            height=140,
            help="建议 6–10 条简短陈述，描述对叙事最关键的事实",
        )

        st.markdown("**台词样例 voice_examples**（可选，每行一句）")
        ve_default = "\n".join(data.get("voice_examples") or [])
        ve_text = st.text_area("台词样例", value=ve_default, height=80)

        absent_default = st.checkbox("该角色默认缺席（章节起草时自动交代去向）", value=bool(data.get("absent_default", False)))

        submitted = st.form_submit_button("保存人物卡", type="primary")

    if submitted:
        if not name.strip():
            st.error("角色名不能为空")
            return
        key_traits = [ln.strip() for ln in kt_text.splitlines() if ln.strip()]
        voice_examples = [ln.strip() for ln in ve_text.splitlines() if ln.strip()]
        save_dict = {
            "name": name.strip(),
            "player_handle": player_handle.strip(),
            "race": race.strip(),
            "class": class_name.strip(),
            "age": age.strip(),
            "gender": gender.strip(),
            "homeland": homeland.strip(),
            "appearance": appearance.strip(),
            "personality": personality.strip(),
            "ideal": ideal_val.strip(),
            "bond": bond.strip(),
            "flaw": flaw.strip(),
            "background_story": background_story.strip(),
            "key_traits": key_traits,
            "voice_examples": voice_examples,
            "absent_default": absent_default,
        }
        camp.character_cards_dir.mkdir(parents=True, exist_ok=True)
        save_path = camp.character_cards_dir / f"{name.strip()}.yaml"
        # 如果改了角色名（旧文件不同名），删旧文件
        if edit_name and edit_name != name.strip():
            old_path = camp.character_cards_dir / f"{edit_name}.yaml"
            if old_path.exists():
                old_path.unlink()
        save_path.write_text(
            yaml.dump(save_dict, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        st.session_state.pop("card_edit_name", None)
        st.session_state.pop("card_edit_data", None)
        st.success(f"已保存：{save_path.name}")
        st.rerun()


# ---------------------------------------------------------------------------
# 标签页 5：知识库（RAG）
# ---------------------------------------------------------------------------


def _tab_kb(camp: Campaign):
    st.subheader(f"世界观知识库 — {camp.name}")
    st.caption(
        "把世界观资料（种族设定、地理、组织、历史）按主题拆成多个 .md/.txt 放进知识库；"
        "起草章节时会按场景自动检索 top-K 片段注入 prompt，避免长文本撑爆 context。"
    )

    try:
        from trpg2novel.rag import KnowledgeBase, load_kb_config, save_kb_config
        from trpg2novel.rag.config import KBConfig
    except ImportError as e:
        st.error(f"RAG 模块加载失败：{e}。请运行 `pip install sqlite-vec`。")
        return

    # ---- Embedding 配置 ----
    st.markdown("#### Embedding 配置")
    cfg = load_kb_config(camp.kb_config_yaml)
    with st.form("kb_cfg_form"):
        c1, c2 = st.columns(2)
        api_key = c1.text_input("API Key", value=cfg.api_key, type="password")
        base_url = c2.text_input("Base URL", value=cfg.base_url)
        c3, c4, c5 = st.columns(3)
        model = c3.text_input("Embedding 模型", value=cfg.model, help="如 text-embedding-3-small / bge-large-zh")
        dim = c4.number_input("向量维度", value=cfg.dim, min_value=64, max_value=4096)
        top_k = c5.number_input("检索 top-K", value=cfg.top_k, min_value=1, max_value=20)
        c6, c7 = st.columns(2)
        chunk_size = c6.number_input("分块大小（字符）", value=cfg.chunk_size, min_value=100, max_value=2000)
        chunk_overlap = c7.number_input("分块重叠（字符）", value=cfg.chunk_overlap, min_value=0, max_value=500)
        min_score = st.slider("最小相似度阈值", min_value=0.0, max_value=1.0, value=float(cfg.min_score), step=0.05)

        if st.form_submit_button("保存配置", type="primary"):
            new_cfg = KBConfig(
                api_key=api_key.strip(),
                base_url=base_url.strip(),
                model=model.strip(),
                dim=int(dim),
                chunk_size=int(chunk_size),
                chunk_overlap=int(chunk_overlap),
                top_k=int(top_k),
                min_score=float(min_score),
            )
            save_kb_config(new_cfg, camp.kb_config_yaml)
            cfg = new_cfg
            st.success("已保存 kb_config.yaml")

    st.divider()

    # 准备 KB
    kb = KnowledgeBase.open(camp.knowledge_base_dir, cfg)

    # ---- 源文件管理 ----
    st.markdown("#### 知识源文件")
    sources = kb.list_sources()
    if sources:
        st.write(f"当前共 **{len(sources)}** 个源文件，索引中 **{kb.count_chunks()}** 片段：")
        for src in sources:
            cc1, cc2, cc3 = st.columns([4, 1, 1])
            size_kb = src.stat().st_size / 1024
            cc1.markdown(f"`{src.name}` — {size_kb:.1f} KB")
            if cc2.button("查看", key=f"kb_view_{src.name}"):
                st.session_state["kb_view_file"] = src.name
            if cc3.button("删除", key=f"kb_del_{src.name}"):
                src.unlink()
                st.success(f"已删除：{src.name}")
                st.rerun()
        if st.session_state.get("kb_view_file"):
            fname = st.session_state["kb_view_file"]
            src = kb.sources_dir / fname
            if src.exists():
                with st.expander(f"📄 {fname}", expanded=True):
                    st.text_area("内容", value=src.read_text(encoding="utf-8"), height=300, disabled=True, key=f"kb_view_area_{fname}")
                    if st.button("关闭", key=f"kb_view_close_{fname}"):
                        st.session_state.pop("kb_view_file", None)
                        st.rerun()
    else:
        st.info("知识库为空。在下方上传 .md 或 .txt 文件。")

    # ---- 上传 ----
    up_files = st.file_uploader(
        "上传知识源 (.md / .txt，可多选)",
        type=["md", "txt"],
        accept_multiple_files=True,
        key="kb_upload",
    )
    if up_files:
        if st.button(f"保存 {len(up_files)} 个文件到 sources/", key="kb_btn_save_sources"):
            kb.sources_dir.mkdir(parents=True, exist_ok=True)
            for f in up_files:
                out = kb.sources_dir / f.name
                out.write_bytes(f.getvalue())
            st.success(f"已写入 {len(up_files)} 个文件，记得点下方「重建索引」。")
            st.rerun()

    # ---- 重建索引 ----
    st.markdown("#### 重建索引")
    if not cfg.is_configured():
        st.warning("请先填写 Embedding API Key 并保存配置。")
    elif not sources:
        st.info("没有源文件可索引。")
    else:
        if st.button("🔨 重建索引（会清空并重新 embed 全部源文件）", type="secondary"):
            progress = st.progress(0.0)
            status = st.empty()

            def cb(stage, current, total):
                if total > 0:
                    progress.progress(min(current / total, 1.0))
                status.text(f"{stage}: {current}/{total}")

            try:
                with st.spinner("正在调用 embedding API…"):
                    res = kb.rebuild_from_sources(progress_cb=cb)
                st.success(f"索引重建完成：{res['sources']} 源文件 / {res['chunks']} 片段")
            except Exception as e:
                st.error(f"重建失败：{e}")
            finally:
                progress.empty()

    st.divider()

    # ---- 检索预览 ----
    st.markdown("#### 🔍 检索预览（验证 KB 质量）")
    query = st.text_input("输入 query，看会命中哪些片段", key="kb_query_input")
    preview_k = st.slider("top-K", min_value=1, max_value=20, value=cfg.top_k, key="kb_preview_k")
    if query.strip() and cfg.is_configured():
        if st.button("检索", key="kb_btn_search"):
            try:
                with st.spinner("embedding query…"):
                    hits = kb.query(query, top_k=preview_k)
                if not hits:
                    st.info("无结果（可能是 KB 空或所有片段相似度都低于阈值）。")
                else:
                    for i, h in enumerate(hits, 1):
                        with st.expander(f"#{i} 【{h.source}】 score={h.score:.3f} dist={h.distance:.3f}"):
                            st.write(h.text)
            except Exception as e:
                st.error(f"检索失败：{e}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    st.set_page_config(
        page_title="trpg-to-novel",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title("TRPG 日志 → 小说")

    _sidebar()

    camp = _current_campaign()
    if camp is None:
        st.warning("请先在左侧选择或新建一个团（Campaign）。")
        return

    st.caption(f"当前团：**{camp.name}**  `{camp.id}`  —  {camp.system}")

    tab_pipe, tab_review, tab_polish, tab_cards, tab_kb = st.tabs([
        "Pipeline（处理日志）", "审稿", "润色", "人物卡", "知识库",
    ])
    with tab_pipe:
        _tab_pipeline(camp)
    with tab_review:
        _tab_review(camp)
    with tab_polish:
        _tab_polish(camp)
    with tab_cards:
        _tab_cards(camp)
    with tab_kb:
        _tab_kb(camp)


if __name__ == "__main__":
    main()

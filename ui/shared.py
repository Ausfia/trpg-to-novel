"""ui/shared.py — Streamlit 多页面共用工具。

包含：
- 路径常量、.env 读写、LLM 阶段配置
- Campaign 加载、状态/场景缓存读取
- Pipeline 命令执行（subprocess 包装）
- 模型列表拉取 + 模型选择控件
- 主题注入、顶栏渲染
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st
import yaml

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from trpg2novel.campaign import Campaign  # noqa: E402


# ---------------------------------------------------------------------------
# 显示常量（场景片段着色 / 标签）
# ---------------------------------------------------------------------------

KIND_LABEL = {"narration": "叙事", "battle": "战斗"}

SEG_COLOR = {
    "dm_narration": "#1a6fba",
    "pc_dialogue": "#2a8a3a",
    "pc_action": "#8a5a00",
    "pc_ooc": "#888",
    "roll_result": "#6a2a8a",
    "unmarked_warning": "#cc0000",
}

SEG_LABEL = {
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
# .env 读写
# ---------------------------------------------------------------------------

ENV_PATH = _ROOT / ".env"

STAGES: list[tuple[str, str]] = [
    ("detect", "断点检测"),
    ("draft", "章节起草"),
    ("polish", "润色"),
    ("review", "一致性审稿"),
]

STAGE_ICONS = {
    "detect": "🔍",
    "draft": "✍️",
    "polish": "✨",
    "review": "🪞",
}

_STAGE_MODEL_DEFAULTS = {
    "detect": "deepseek-chat",
    "draft": "deepseek-reasoner",
    "polish": "deepseek-reasoner",
    "review": "deepseek-chat",
}

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def read_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
    return vals


def stage_value(env: dict[str, str], stage: str, field: str) -> str:
    """从 env 字典里取阶段配置；兼容旧 key。"""
    new_key = f"LLM_{stage.upper()}_{field.upper()}"
    if new_key in env:
        return env[new_key]
    if field == "api_key":
        return env.get("OPENAI_API_KEY", "")
    if field == "base_url":
        return env.get("OPENAI_BASE_URL", DEFAULT_BASE_URL)
    legacy = {
        "detect": "STAGE_MODEL_CHAPTER_DETECT",
        "draft": "STAGE_MODEL_DRAFT",
        "polish": "STAGE_MODEL_POLISH",
        "review": "STAGE_MODEL_REVIEW",
    }
    return env.get(legacy[stage], _STAGE_MODEL_DEFAULTS[stage])


def write_env(env: dict[str, str], stage_cfg: dict[str, dict[str, str]]) -> None:
    """把 4 个阶段的配置写回 .env，保留其他自定义键。"""
    out: dict[str, str] = {
        k: v for k, v in env.items()
        if not k.startswith("LLM_") and k not in {
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "STAGE_MODEL_CHAPTER_DETECT",
            "STAGE_MODEL_DRAFT", "STAGE_MODEL_POLISH", "STAGE_MODEL_REVIEW",
            "STAGE_MODEL_SEGMENT",
        }
    }
    for stage, cfg in stage_cfg.items():
        upper = stage.upper()
        out[f"LLM_{upper}_API_KEY"] = cfg.get("api_key", "")
        out[f"LLM_{upper}_BASE_URL"] = cfg.get("base_url", DEFAULT_BASE_URL)
        out[f"LLM_{upper}_MODEL"] = cfg.get("model", _STAGE_MODEL_DEFAULTS.get(stage, ""))
    draft = stage_cfg.get("draft", {})
    if draft.get("api_key"):
        out["OPENAI_API_KEY"] = draft["api_key"]
    if draft.get("base_url"):
        out["OPENAI_BASE_URL"] = draft["base_url"]
    lines = [f"{k}={v}" for k, v in out.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 模型列表拉取 + 模型选择控件
# ---------------------------------------------------------------------------


def fetch_model_list(api_key: str, base_url: str) -> list[str]:
    """GET /v1/models，返回按名称排序的 model ID 列表。"""
    from trpg2novel.llm.client import make_client
    client = make_client(api_key, base_url)
    resp = client.models.list()
    return sorted(m.id for m in resp.data)


def model_picker_widget(
    fetch_key: str,
    model_input_key: str,
    current_model: str,
    api_key: str,
    base_url: str,
) -> str:
    """"获取模型列表 → 下拉选择 / 手动输入"控件，返回当前选定的 model 名。"""
    sk_mlist = f"_fetched_models_{fetch_key}"

    fetch_col, _ = st.columns([1, 2])
    with fetch_col:
        if st.button(
            "↓ 获取模型列表",
            key=f"_btn_fetch_models_{fetch_key}",
            use_container_width=True,
            help="使用上方填写的 API Key + Base URL 获取可用模型",
        ):
            if api_key.strip() and base_url.strip():
                with st.spinner("获取中…"):
                    try:
                        st.session_state[sk_mlist] = fetch_model_list(api_key, base_url)
                    except Exception as exc:
                        st.session_state[sk_mlist] = []
                        st.error(f"获取失败：{exc}")
            else:
                st.warning("请先填写 API Key 和 Base URL")

    fetched: list[str] = st.session_state.get(sk_mlist, [])
    if fetched:
        options = fetched + ["〔手动输入〕"]
        cur_idx = fetched.index(current_model) if current_model in fetched else len(fetched)
        picked = st.selectbox(
            "model_sel",
            options,
            index=min(cur_idx, len(options) - 1),
            key=f"_sel_model_{fetch_key}",
            label_visibility="collapsed",
        )
        if picked == "〔手动输入〕":
            return st.text_input(
                "model_input",
                value=current_model,
                key=model_input_key,
                label_visibility="collapsed",
            )
        st.session_state[model_input_key] = picked
        return picked
    return st.text_input(
        "model_input",
        value=current_model,
        key=model_input_key,
        label_visibility="collapsed",
    )


# ---------------------------------------------------------------------------
# Campaign 辅助
# ---------------------------------------------------------------------------


def current_campaign() -> Campaign | None:
    cid = st.session_state.get("selected_campaign_id")
    if not cid:
        return None
    # 从已加载列表里直接找，避免 Campaign.load() 路径差异导致 FileNotFoundError
    try:
        all_camps = Campaign.list_all()
    except Exception:
        all_camps = []
    for c in all_camps:
        if c.id == cid:
            return c
    # 兜底：直接 load（兼容 session_state 里存了合法 ID 但 list_all 遗漏的情况）
    try:
        return Campaign.load(cid)
    except Exception:
        return None


def require_campaign() -> Campaign | None:
    """若无团，渲染提示并返回 None；否则返回当前 Campaign。"""
    camp = current_campaign()
    if camp is None:
        all_camps = []
        try:
            all_camps = Campaign.list_all()
        except Exception:
            pass
        if all_camps:
            st.warning(
                "⚠️ 当前团已失效或未选择。请在顶栏下拉框重新选一个团。"
            )
        else:
            st.info("还没有任何团。请到「🏛️ 团管理」新建第一个团。")
        return None
    return camp


# ---------------------------------------------------------------------------
# 数据加载（带缓存）
# ---------------------------------------------------------------------------


@st.cache_data
def load_scenes(parsed_dir: str, session_id: str) -> list[dict]:
    path = Path(parsed_dir) / f"{session_id}.scenes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


@st.cache_data
def load_tagged(parsed_dir: str, session_id: str) -> dict[str, dict]:
    path = Path(parsed_dir) / f"{session_id}.tagged.json"
    if not path.exists():
        return {}
    return {e["id"]: e for e in json.loads(path.read_text(encoding="utf-8"))}


@st.cache_data
def load_state(campaign_root: str) -> dict:
    path = Path(campaign_root) / "story_state.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def list_chapters(camp: Campaign) -> list[Path]:
    return sorted(camp.chapters_dir.glob("ch*_draft.md"))


def list_sessions(camp: Campaign) -> list[str]:
    return camp.list_sessions()


def list_raw_sessions(camp: Campaign) -> list[str]:
    """raw_logs/ 下所有 .md 文件（含未 segment 的）。"""
    return [p.stem for p in sorted(camp.raw_logs_dir.glob("*.md"))]


# ---------------------------------------------------------------------------
# Pipeline 命令执行
# ---------------------------------------------------------------------------


def run_cmd(args: list[str], placeholder, campaign_id: str | None = None) -> tuple[int, str]:
    """跑 `python -m trpg2novel.pipeline ...`，把 stdout 流式渲染到 placeholder。"""
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
# 主题与顶栏
# ---------------------------------------------------------------------------

_THEME_CSS_PATH = _ROOT / "ui" / "theme.css"


def inject_theme() -> None:
    """注入 theme.css。每个 page 切换都会重新跑顶层代码，所以这里幂等就好。"""
    if not _THEME_CSS_PATH.exists():
        return
    css = _THEME_CSS_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_topbar() -> Campaign | None:
    """顶栏：logo + 当前团切换 + 健康徽章。返回当前 Campaign（可能为 None）。"""
    all_camps = Campaign.list_all()
    camp_ids = [c.id for c in all_camps]
    camp_names = {c.id: c.name for c in all_camps}

    # 没有团：渲染空顶栏 + 提示
    if not camp_ids:
        st.markdown(
            """
            <div class="tn-topbar">
                <div class="tn-logo">🎲 trpg-to-novel</div>
                <div class="tn-topbar-right">
                    <span class="tn-badge tn-badge-warn">尚无团</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return None

    # 默认选第一个
    if (
        "selected_campaign_id" not in st.session_state
        or st.session_state["selected_campaign_id"] not in camp_ids
    ):
        st.session_state["selected_campaign_id"] = camp_ids[0]

    # 顶栏布局：左 logo / 中团选择 / 右徽章
    top_l, top_m, top_r = st.columns([3, 4, 5], gap="medium")
    with top_l:
        st.markdown(
            '<div class="tn-logo">🎲 trpg-to-novel<span class="tn-logo-sub"> · 跑团 → 小说</span></div>',
            unsafe_allow_html=True,
        )
    with top_m:
        st.selectbox(
            "当前团",
            options=camp_ids,
            format_func=lambda cid: camp_names.get(cid, cid),
            key="selected_campaign_id",
            label_visibility="collapsed",
        )
    camp = current_campaign()
    with top_r:
        _render_health_badges(camp)

    st.markdown('<div class="tn-divider"></div>', unsafe_allow_html=True)
    return camp


def _render_health_badges(camp: Campaign | None) -> None:
    env = read_env()
    badges: list[str] = []
    for stage, label in STAGES:
        ok = bool(stage_value(env, stage, "api_key").strip())
        cls = "tn-badge-ok" if ok else "tn-badge-warn"
        mark = "●" if ok else "○"
        icon = STAGE_ICONS.get(stage, "")
        badges.append(f'<span class="tn-badge {cls}">{mark} {icon}{label}</span>')

    kb_state = "—"
    if camp is not None:
        try:
            from trpg2novel.rag import KnowledgeBase, load_kb_config
            cfg = load_kb_config(camp.kb_config_yaml)
            if cfg.is_configured():
                kb = KnowledgeBase.open(camp.knowledge_base_dir, cfg)
                cnt = kb.count_chunks()
                kb_state = f"{cnt} 片段" if cnt > 0 else "空"
        except Exception:
            kb_state = "未启用"
    badges.append(f'<span class="tn-badge tn-badge-info">📚 KB {kb_state}</span>')

    st.markdown(
        '<div class="tn-topbar-right">' + " ".join(badges) + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 小工具：徽章 HTML
# ---------------------------------------------------------------------------


def badge(text: str, kind: str = "info") -> str:
    """生成内联徽章 HTML。kind: ok / warn / err / info / accent / neutral."""
    return f'<span class="tn-badge tn-badge-{kind}">{text}</span>'


def card_html(content: str, title: str | None = None) -> str:
    """把内容包成卡片。content 已是 HTML 字符串。"""
    head = f'<div class="tn-card-title">{title}</div>' if title else ""
    return f'<div class="tn-card">{head}{content}</div>'


def metric_html(label: str, value: str, hint: str | None = None, kind: str = "accent") -> str:
    """指标卡（用于 dashboard 顶部）。kind 影响数值的颜色。"""
    hint_html = f'<div class="tn-metric-hint">{hint}</div>' if hint else ""
    return (
        f'<div class="tn-metric-card">'
        f'<div class="tn-metric-label">{label}</div>'
        f'<div class="tn-metric-value tn-metric-{kind}">{value}</div>'
        f'{hint_html}'
        f'</div>'
    )

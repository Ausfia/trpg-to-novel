"""⚙️ LLM 配置 — 4 阶段独立 API Key / Base URL / Model，支持拉取模型列表。"""

from __future__ import annotations

import streamlit as st

from ui.shared import (
    DEFAULT_BASE_URL,
    STAGE_ICONS,
    STAGES,
    model_picker_widget,
    read_env,
    stage_value,
    write_env,
)

st.title("⚙️ LLM 配置")
st.caption("为断点检测、章节起草、润色、一致性审稿四个阶段分别配置 API Key 和模型。")

env = read_env()
new_cfg: dict[str, dict[str, str]] = {}

# ---------------------------------------------------------------------------
# 快捷填充区：一键同步到所有阶段
# ---------------------------------------------------------------------------

with st.expander("⚡ 快捷：将同一套凭据填入所有阶段", expanded=False):
    qa_key = st.text_input(
        "API Key（统一）",
        type="password",
        placeholder="sk-...",
        key="quick_api_key",
    )
    qa_url = st.text_input(
        "Base URL（统一）",
        value=DEFAULT_BASE_URL,
        key="quick_base_url",
    )
    if st.button("填入所有阶段（不覆盖 Model）", key="btn_quick_fill"):
        for stage, _ in STAGES:
            if qa_key.strip():
                st.session_state[f"cfg_{stage}_api_key"] = qa_key
            if qa_url.strip():
                st.session_state[f"cfg_{stage}_base_url"] = qa_url
        st.success("已填入，请再点下方「保存全部配置」持久化到 .env。")

st.divider()

# ---------------------------------------------------------------------------
# 四个阶段独立配置
# ---------------------------------------------------------------------------

cols = st.columns(2)
stage_pairs = [(STAGES[0], STAGES[1]), (STAGES[2], STAGES[3])]

for row_idx, pair in enumerate(stage_pairs):
    for col_idx, (stage, label) in enumerate(pair):
        with cols[col_idx]:
            icon = STAGE_ICONS.get(stage, "")
            api_key_cur = stage_value(env, stage, "api_key")
            base_url_cur = stage_value(env, stage, "base_url")
            model_cur = stage_value(env, stage, "model")

            configured = bool(api_key_cur.strip())
            status_text = "● 已配置" if configured else "○ 未配置"
            status_color = "#2fa84f" if configured else "#e09c30"

            st.markdown(
                f'<div class="tn-card-title">{icon} {label}'
                f'<span style="font-size:11px;color:{status_color};margin-left:8px">{status_text}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            api_key = st.text_input(
                "API Key",
                value=api_key_cur,
                type="password",
                key=f"cfg_{stage}_api_key",
                placeholder="sk-...",
            )
            base_url = st.text_input(
                "Base URL",
                value=base_url_cur,
                key=f"cfg_{stage}_base_url",
            )
            st.markdown("**Model**")
            model = model_picker_widget(
                fetch_key=stage,
                model_input_key=f"cfg_{stage}_model",
                current_model=model_cur,
                api_key=api_key,
                base_url=base_url,
            )
            new_cfg[stage] = {"api_key": api_key, "base_url": base_url, "model": model}

            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    if row_idx == 0:
        st.divider()

st.divider()

# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

save_col, status_col = st.columns([1, 3])
with save_col:
    if st.button("💾 保存全部配置", type="primary", use_container_width=True):
        write_env(env, new_cfg)
        st.success("已保存到 .env")
        st.rerun()

with status_col:
    configured_stages = [label for stage, label in STAGES if new_cfg.get(stage, {}).get("api_key", "").strip()]
    missing_stages = [label for stage, label in STAGES if not new_cfg.get(stage, {}).get("api_key", "").strip()]
    if configured_stages:
        st.success(f"已配置：{'、'.join(configured_stages)}")
    if missing_stages:
        st.warning(f"未配置：{'、'.join(missing_stages)}")

st.divider()

# ---------------------------------------------------------------------------
# 当前 .env 预览（折叠）
# ---------------------------------------------------------------------------

with st.expander("🔍 当前 .env 内容预览（脱敏）", expanded=False):
    if not env:
        st.caption(".env 文件不存在或为空")
    else:
        lines = []
        for k, v in env.items():
            if "KEY" in k.upper() or "SECRET" in k.upper():
                display_v = v[:6] + "…" if len(v) > 6 else "***"
            else:
                display_v = v
            lines.append(f"{k}={display_v}")
        st.code("\n".join(lines), language="bash")

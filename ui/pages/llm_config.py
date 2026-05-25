"""⚙️ LLM 配置 — 4 阶段独立 API Key / Base URL / Model，支持拉取模型列表。"""

from __future__ import annotations

import streamlit as st

from ui.shared import (
    DEFAULT_BASE_URL,
    POLISH_SUBSTAGES,
    STAGE_ICONS,
    STAGES,
    model_picker_widget,
    polish_substage_value,
    read_env,
    read_vision_config,
    stage_value,
    write_env,
    write_polish_substage_env,
    write_vision_config,
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

            # 测试按钮
            _can_test = bool(api_key.strip() and model.strip())
            if st.button(
                "🧪 测试连接",
                key=f"btn_test_{stage}",
                disabled=not _can_test,
                width="stretch",
            ):
                with st.spinner(f"测试 {model} …"):
                    try:
                        from trpg2novel.llm.client import chat, make_client
                        _client = make_client(api_key.strip(), base_url.strip())
                        _reply = chat(
                            _client, model.strip(),
                            [{"role": "user", "content": "Reply with the single word OK."}],
                            temperature=0,
                            max_tokens=10,
                        )
                        st.success(f"✅ 可用，回复：{_reply.strip()[:40]}")
                    except Exception as _exc:
                        st.error(f"❌ 失败：{_exc}")

            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    if row_idx == 0:
        st.divider()

st.divider()

# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

save_col, status_col = st.columns([1, 3])
with save_col:
    if st.button("💾 保存全部配置", type="primary", width="stretch"):
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
# 润色工作流子步骤模型
# ---------------------------------------------------------------------------

st.markdown("#### ✨ 高级：润色模型")
st.caption("当前润色流程已移除“先规划再改写”，只配置文学成稿模型；轻量自检仅在润色页勾选时使用。")
polish_sub_cfg: dict[str, dict[str, str]] = {}
with st.expander("配置文学成稿 / 轻量自检", expanded=False):
    sub_cols = st.columns(2)
    for idx, (substage, label) in enumerate(POLISH_SUBSTAGES):
        with sub_cols[idx]:
            api_key_cur = polish_substage_value(env, substage, "api_key")
            base_url_cur = polish_substage_value(env, substage, "base_url")
            model_cur = polish_substage_value(env, substage, "model")
            st.markdown(f"**{label}**")
            if substage == "polish_rewrite":
                st.caption("建议：Opus 4.7 / 最强文笔模型")
            else:
                st.caption("建议：便宜稳定模型")
            api_key = st.text_input(
                "API Key",
                value=api_key_cur,
                type="password",
                key=f"cfg_{substage}_api_key",
                placeholder="留空则继承润色阶段",
            )
            base_url = st.text_input(
                "Base URL",
                value=base_url_cur,
                key=f"cfg_{substage}_base_url",
            )
            st.markdown("**Model**")
            model = model_picker_widget(
                fetch_key=substage,
                model_input_key=f"cfg_{substage}_model",
                current_model=model_cur,
                api_key=api_key,
                base_url=base_url,
            )
            polish_sub_cfg[substage] = {"api_key": api_key, "base_url": base_url, "model": model}
            if st.button(
                "🧪 测试连接",
                key=f"btn_test_{substage}",
                disabled=not (api_key.strip() and model.strip()),
                width="stretch",
            ):
                with st.spinner(f"测试 {model} …"):
                    try:
                        from trpg2novel.llm.client import chat, make_client
                        _client = make_client(api_key.strip(), base_url.strip())
                        _reply = chat(
                            _client, model.strip(),
                            [{"role": "user", "content": "Reply with the single word OK."}],
                            temperature=0,
                            max_tokens=10,
                        )
                        st.success(f"✅ 可用，回复：{_reply.strip()[:40]}")
                    except Exception as _exc:
                        st.error(f"❌ 失败：{_exc}")

    if st.button("💾 保存润色工作流模型", key="btn_save_polish_workflow", type="primary"):
        write_polish_substage_env(env, polish_sub_cfg)
        st.success("已保存润色模型配置")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 识图模型（人物卡立绘外貌识别）
# ---------------------------------------------------------------------------

st.markdown("#### 🖼️ 识图模型（人物卡立绘外貌识别）")
st.caption("用于分析角色立绘图片，自动生成外貌描述注入起草 prompt。需要支持视觉输入（Vision）的模型，如 gpt-4o、qwen-vl-max 等。")

vcfg = read_vision_config()
v_api_key_cur = vcfg["api_key"]
v_base_url_cur = vcfg["base_url"]
v_model_cur = vcfg["model"]

vc1, vc2 = st.columns(2)
with vc1:
    v_configured = bool(v_api_key_cur.strip())
    v_status_color = "#2fa84f" if v_configured else "#e09c30"
    v_status_text = "● 已配置" if v_configured else "○ 未配置"
    st.markdown(
        f'<div class="tn-card-title">🖼️ 识图模型'
        f'<span style="font-size:11px;color:{v_status_color};margin-left:8px">{v_status_text}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    v_api_key = st.text_input("API Key", value=v_api_key_cur, type="password", key="cfg_vision_api_key", placeholder="sk-...")
    v_base_url = st.text_input("Base URL", value=v_base_url_cur, key="cfg_vision_base_url")
    st.markdown("**Model**")
    v_model = model_picker_widget(
        fetch_key="vision",
        model_input_key="cfg_vision_model",
        current_model=v_model_cur,
        api_key=v_api_key,
        base_url=v_base_url,
    )

with vc2:
    st.markdown("")
    st.markdown("")
    st.info("识图模型仅在「人物卡」页点击「分析外貌」时调用，不参与章节流水线。")
    if st.button("💾 保存识图配置", key="btn_save_vision", type="primary"):
        write_vision_config(v_api_key.strip(), v_base_url.strip(), v_model.strip())
        st.success("识图模型配置已保存")
    _can_test_v = bool(v_api_key.strip() and v_model.strip())
    if st.button("🧪 测试连接", key="btn_test_vision", disabled=not _can_test_v, width="stretch"):
        with st.spinner(f"测试 {v_model} …"):
            try:
                from trpg2novel.llm.client import chat, make_client
                _vc = make_client(v_api_key.strip(), v_base_url.strip())
                _vr = chat(
                    _vc, v_model.strip(),
                    [{"role": "user", "content": "Reply with the single word OK."}],
                    temperature=0,
                    max_tokens=10,
                )
                st.success(f"✅ 可用，回复：{_vr.strip()[:40]}")
            except Exception as _exc:
                st.error(f"❌ 失败：{_exc}")

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

"""✨ 润色对比 — 选章节、运行 Polish LLM、左右对比原稿/润色稿。"""

from __future__ import annotations

import streamlit as st

from ui.shared import (
    list_chapters,
    read_env,
    require_campaign,
    run_cmd,
    stage_value,
)

st.title("✨ 润色对比")
st.caption("选择章节，调用 LLM 润色，左右对比草稿与润色稿。")

camp = require_campaign()
if camp is None:
    st.stop()

chapters = list_chapters(camp)
if not chapters:
    st.info("暂无章节草稿，请先在 Pipeline 页面运行 Draft。")
    st.stop()

# ---------------------------------------------------------------------------
# 章节选择
# ---------------------------------------------------------------------------

chap_names = [p.name for p in chapters]
col_sel, col_status = st.columns([3, 2])
with col_sel:
    sel_name = st.selectbox("选择章节", chap_names, key="pol_chap")

draft_path = camp.chapters_dir / sel_name
revised_path = draft_path.with_name(draft_path.name.replace("_draft", "_revised"))
polished_path = draft_path.with_name(draft_path.stem.replace("_draft", "") + "_polished.md")

source_path = revised_path if revised_path.exists() else draft_path
source_label = "修订稿" if revised_path.exists() else "草稿"
source_text = source_path.read_text(encoding="utf-8")

with col_status:
    st.markdown('<div style="margin-top:30px"></div>', unsafe_allow_html=True)
    if polished_path.exists():
        st.success("已润色")
    else:
        st.info("尚未润色")

st.divider()

# ---------------------------------------------------------------------------
# 操作面板
# ---------------------------------------------------------------------------

env = read_env()
has_key = bool(stage_value(env, "polish", "api_key").strip())

op_col1, op_col2, op_col3 = st.columns([2, 2, 1])
with op_col1:
    if not has_key:
        st.warning("请先在「⚙️ LLM 配置」页配置「润色」阶段的 API Key。")
    last_summary = st.text_area("上一章摘要（可选）", height=80, key="pol_summary")

with op_col2:
    st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
    if st.button(
        "▶ 运行润色 (Polish)",
        disabled=not has_key,
        use_container_width=True,
        key="btn_pol",
        type="primary",
    ):
        ph = st.empty()
        with st.spinner("润色中（调用 LLM，可能需要 1-3 分钟）…"):
            args = ["polish", str(draft_path)]
            if last_summary.strip():
                args += ["--last-summary", last_summary.strip()]
            code, _ = run_cmd(args, ph, camp.id)
        if code == 0:
            st.success("润色完成！")
            st.rerun()
        else:
            st.error("润色失败，见上方输出")

with op_col3:
    if polished_path.exists():
        st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
        if st.button("🗑 删除润色稿", use_container_width=True, key="btn_del_polished"):
            polished_path.unlink()
            st.success("已删除润色稿")
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 左右对比
# ---------------------------------------------------------------------------

left_c, right_c = st.columns(2)

with left_c:
    st.markdown(f"**{source_label}（{source_path.name}）**")
    st.download_button(
        "⬇ 下载",
        data=source_text.encode("utf-8"),
        file_name=source_path.name,
        mime="text/markdown",
        key=f"dl_source_{sel_name}",
    )
    st.text_area(
        "原稿",
        value=source_text,
        height=700,
        key=f"pol_src_{sel_name}",
        disabled=True,
    )

with right_c:
    if polished_path.exists():
        polished_text = polished_path.read_text(encoding="utf-8")
        st.markdown(f"**润色稿（{polished_path.name}）**")
        st.download_button(
            "⬇ 下载",
            data=polished_text.encode("utf-8"),
            file_name=polished_path.name,
            mime="text/markdown",
            key=f"dl_pol_{sel_name}",
        )
        st.text_area(
            "润色稿",
            value=polished_text,
            height=700,
            key=f"pol_out_{sel_name}",
            disabled=True,
        )
    else:
        st.info('尚未润色。点击上方「运行润色」生成润色稿。')

"""✨ 润色对比 — 选章节、运行 Polish LLM、左右对比原稿/润色稿。"""

from __future__ import annotations

import streamlit as st
from trpg2novel.chapterize.anchors import ANCHOR_KEYS, anchor_path_for_chapter, load_anchor_file

from ui.shared import (
    chapter_stage_paths,
    chapter_status,
    list_chapters,
    polish_substage_value,
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
    st.info("暂无章节草稿，请先在大纲规划页确认卷并切章。")
    st.stop()

# ---------------------------------------------------------------------------
# 章节选择
# ---------------------------------------------------------------------------

chap_names = [p.name for p in chapters]
col_sel, col_status = st.columns([3, 2])
with col_sel:
    sel_name = st.selectbox("选择章节", chap_names, key="pol_chap")

draft_path = camp.chapters_dir / sel_name
stage_paths = chapter_stage_paths(draft_path)
revised_path = stage_paths["revised"]
polished_path = stage_paths["polished"]

source_path = revised_path if revised_path.exists() else draft_path
source_label = "修订稿" if revised_path.exists() else "草稿"
source_text = source_path.read_text(encoding="utf-8")
anchor_path = anchor_path_for_chapter(draft_path)

with col_status:
    st.markdown('<div style="margin-top:30px"></div>', unsafe_allow_html=True)
    status = chapter_status(draft_path)
    if status["stage"] in {"polished", "reviewed", "final"}:
        st.success(status["label"])
    else:
        st.info(status["label"])
    if anchor_path.exists():
        try:
            _anchors = load_anchor_file(anchor_path)
            _anchor_count = sum(len(_anchors.get(k) or []) for k in ANCHOR_KEYS)
            st.caption(f"素材锚点：{_anchor_count} 条")
        except Exception:
            st.caption("素材锚点：读取失败")
    else:
        st.caption("素材锚点：缺失")

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

    try:
        from trpg2novel.style import list_style_profiles, load_style_profile
        profile_paths = list_style_profiles(campaign=camp)
    except Exception:
        profile_paths = []
    profile_options = [p.stem for p in profile_paths]
    if profile_options:
        style_profile = st.selectbox("风格方案", profile_options, key="pol_style_profile")
        try:
            selected_profile = load_style_profile(style_profile, campaign=camp)
            summary = selected_profile.style_summary or selected_profile.description or "无说明"
            st.caption(f"当前方案：{selected_profile.name}｜{summary}")
        except Exception:
            selected_profile = None
    else:
        style_profile = ""
        selected_profile = None
        st.warning("还没有风格方案，请先到「🎨 风格方案」页创建。")
    pov_mode = st.selectbox(
        "叙事视角",
        ["继承配方", "third_limited", "third_omniscient", "first_person"],
        key="pol_pov_mode",
    )
    protagonist = st.text_input("主视角 / 主角（可选）", key="pol_protagonist")

with op_col2:
    st.markdown("**润色模型**")
    st.caption(f"文学成稿：{polish_substage_value(env, 'polish_rewrite', 'model')}")
    st.caption(f"轻量自检：{polish_substage_value(env, 'polish_check', 'model')}（仅勾选自检时使用）")
    use_style_kb = st.checkbox("启用风格资料库", value=True, key="pol_use_style_kb")
    st.caption("润色将直接调用润色模型成稿，不再生成文学改写方案。")
    self_check = st.checkbox("运行轻量自检", value=False, key="pol_self_check")

    st.markdown('<div style="margin-top:12px"></div>', unsafe_allow_html=True)
    if st.button(
        "▶ 运行文学化润色 (Polish)",
        disabled=not has_key,
        width="stretch",
        key="btn_pol",
        type="primary",
    ):
        ph = st.empty()
        with st.spinner("文学化成稿中。下方会显示阶段日志；单次 LLM 调用没有 token 级实时进度。"):
            args = ["polish", str(draft_path)]
            if last_summary.strip():
                args += ["--last-summary", last_summary.strip()]
            if style_profile:
                args += ["--style-profile", style_profile]
            if pov_mode != "继承配方":
                args += ["--pov-mode", pov_mode]
            if protagonist.strip():
                args += ["--protagonist", protagonist.strip()]
            args += ["--use-style-kb" if use_style_kb else "--no-style-kb"]
            if self_check:
                args += ["--self-check"]
            code, _ = run_cmd(args, ph, camp.id)
        if code == 0:
            st.session_state["active_chapter"] = draft_path.name
            st.success("润色完成！")
            st.rerun()
        else:
            st.error("润色失败，见上方输出")

with op_col3:
    if polished_path.exists():
        st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
        if st.button("➡ 去一致性检查", width="stretch", key="btn_go_consistency", type="primary"):
            st.session_state["active_chapter"] = draft_path.name
            st.switch_page("pages/consistency.py")
        if st.button("🗑 删除润色稿", width="stretch", key="btn_del_polished"):
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

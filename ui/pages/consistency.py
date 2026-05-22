"""🪞 一致性检查 — 选章节，检查角色/情节/世界观一致性问题。"""

from __future__ import annotations

import streamlit as st

from ui.shared import (
    list_chapters,
    read_env,
    require_campaign,
    stage_value,
)

st.title("🪞 一致性检查")
st.caption("对章节稿件进行 LLM 一致性审查：角色外貌、情节连贯、世界观专有名词。")

camp = require_campaign()
if camp is None:
    st.stop()

# 列出所有 draft + polished 稿件
all_chapters = list_chapters(camp)
polished = sorted(camp.chapters_dir.glob("ch*_polished.md"))
chapter_paths = {p.name: p for p in all_chapters}
for p in polished:
    chapter_paths[p.name] = p
chap_names = sorted(chapter_paths.keys())

if not chap_names:
    st.info("暂无章节稿件，请先在 Pipeline 页面运行 Draft。")
    st.stop()

# ---------------------------------------------------------------------------
# 章节选择 + 状态
# ---------------------------------------------------------------------------

col_sel, col_status = st.columns([3, 2])
with col_sel:
    sel_name = st.selectbox("选择章节稿件", chap_names, key="rev_chap")

chapter_path = chapter_paths[sel_name]
chapter_text = chapter_path.read_text(encoding="utf-8")

with col_status:
    st.markdown('<div style="margin-top:30px"></div>', unsafe_allow_html=True)
    badge_type = "润色稿" if "_polished" in sel_name else "草稿"
    st.info(f"当前：{badge_type}")

st.divider()

# ---------------------------------------------------------------------------
# 操作面板
# ---------------------------------------------------------------------------

env = read_env()
has_key = bool(stage_value(env, "review", "api_key").strip())

op_col1, op_col2 = st.columns([3, 2])
with op_col1:
    if not has_key:
        st.warning("请先在「⚙️ LLM 配置」页配置「一致性审稿」阶段的 API Key。")
    last_summary = st.text_area("上一章摘要（可选）", height=80, key="rev_summary")

with op_col2:
    st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
    run_btn = st.button(
        "▶ 运行一致性检查",
        disabled=not has_key,
        use_container_width=True,
        key="btn_rev",
        type="primary",
    )

st.divider()

# ---------------------------------------------------------------------------
# 运行 & 结果展示
# ---------------------------------------------------------------------------

result_key = f"rev_result_{sel_name}"

if run_btn:
    with st.spinner("一致性检查中（调用 LLM，可能需要 1-2 分钟）…"):
        try:
            from trpg2novel.narrate.review import review_chapter
            from trpg2novel.worldview import load_worldview_for_campaign, load_worldview
            from trpg2novel.character.card_loader import load_all_cards

            wv = None
            pc_facts: dict = {}
            kb = None

            try:
                wv = load_worldview_for_campaign(camp)
            except Exception:
                wv = load_worldview("dnd5e")

            try:
                cards = load_all_cards(camp.character_cards_dir)
                pc_facts = {n: c.atomic_facts for n, c in cards.items() if c.atomic_facts}
            except Exception:
                pass

            try:
                from trpg2novel.rag import KnowledgeBase, load_kb_config
                kb_cfg = load_kb_config(camp.kb_config_yaml)
                if kb_cfg.is_configured():
                    _kb = KnowledgeBase.open(camp.knowledge_base_dir, kb_cfg)
                    if _kb.count_chunks() > 0:
                        kb = _kb
            except Exception:
                pass

            chapter_title = sel_name.replace("_draft.md", "").replace("_polished.md", "")
            api_key = stage_value(env, "review", "api_key")
            base_url = stage_value(env, "review", "base_url")
            model = stage_value(env, "review", "model")

            result = review_chapter(
                chapter_text,
                worldview=wv,
                pc_facts=pc_facts or None,
                last_chapter_summary=last_summary.strip(),
                api_key=api_key,
                base_url=base_url,
                model=model,
                kb=kb,
                chapter_title=chapter_title,
            )
            st.session_state[result_key] = result
        except Exception as e:
            st.error(f"检查失败：{e}")

# 展示结果
if result_key in st.session_state:
    result = st.session_state[result_key]

    # 总结行
    if result.passed:
        st.success(f"✅ 通过审查  —  {result.summary}")
    else:
        st.warning(f"⚠️ 发现问题  —  {result.summary}")

    if result.issues:
        sev_col1, sev_col2, sev_col3 = st.columns(3)
        sev_col1.metric("严重", result.severe_count)
        sev_col2.metric("一般", result.normal_count)
        sev_col3.metric("轻微", result.minor_count)

        st.divider()

        _SEV_COLOR = {"严重": "🔴", "一般": "🟡", "轻微": "🔵"}
        _TYPE_ICON = {
            "角色一致性": "🧑",
            "情节连贯性": "📖",
            "世界观准确性": "🌍",
            "内部矛盾": "⚡",
        }

        for i, issue in enumerate(result.issues):
            sev_icon = _SEV_COLOR.get(issue.severity, "○")
            type_icon = _TYPE_ICON.get(issue.type, "•")
            with st.expander(
                f"{sev_icon} [{issue.severity}] {type_icon} {issue.type}  —  {issue.location}",
                expanded=(issue.severity == "严重"),
            ):
                st.markdown(f"**问题描述**：{issue.description}")
                if issue.suggestion:
                    st.markdown(f"**修改建议**：{issue.suggestion}")
    else:
        st.info("未发现一致性问题。")

    st.divider()

# 章节原文预览
with st.expander("📄 章节原文", expanded=False):
    st.text_area(
        "原文",
        value=chapter_text,
        height=500,
        key=f"rev_src_{sel_name}",
        disabled=True,
    )

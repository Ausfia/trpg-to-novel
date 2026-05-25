"""🪞 一致性检查 — 选章节，检查角色/情节/世界观一致性问题。"""

from __future__ import annotations

import streamlit as st

from ui.shared import (
    badge,
    chapter_stage_paths,
    chapter_status,
    chapter_title_from_state,
    extract_markdown_title,
    final_chapter_path,
    list_chapters,
    load_state,
    read_env,
    require_campaign,
    stage_value,
    update_chapter_entry,
)

st.title("🪞 一致性检查")
st.caption("对润色稿进行 LLM 一致性审查；审查后可人工修订并确认正式成稿。")

camp = require_campaign()
if camp is None:
    st.stop()

state = load_state(str(camp.root))
chapters = list_chapters(camp)
if not chapters:
    st.info("暂无章节，请先在大纲规划页确认卷并切章。")
    st.stop()

chapters_by_name = {p.name: p for p in chapters}
active_name = st.session_state.get("active_chapter")
if active_name not in chapters_by_name:
    polished_chapters = [p for p in chapters if chapter_stage_paths(p)["polished"].exists()]
    active_name = polished_chapters[0].name if polished_chapters else chapters[0].name
    st.session_state["active_chapter"] = active_name

draft_path = chapters_by_name[active_name]
stage_paths = chapter_stage_paths(draft_path)
status = chapter_status(draft_path, state)

# ---------------------------------------------------------------------------
# 章节状态 + 高级切换
# ---------------------------------------------------------------------------

title = chapter_title_from_state(state, draft_path.name) or draft_path.stem
input_path = stage_paths["reviewed"] if stage_paths["reviewed"].exists() else stage_paths["polished"]
can_review = stage_paths["polished"].exists()

head_l, head_r = st.columns([3, 2])
with head_l:
    st.markdown(f"### {title}")
    st.caption(f"当前章节：{draft_path.name} · 检查对象：{input_path.name if can_review else '无润色稿'}")
with head_r:
    st.markdown('<div style="margin-top:8px"></div>', unsafe_allow_html=True)
    st.markdown(badge(status["label"], status["kind"]), unsafe_allow_html=True)
    if not can_review:
        st.warning("该章节还没有润色稿，请先完成润色。")
        if st.button("➡ 去润色页", width="stretch", key="btn_to_polish"):
            st.session_state["pol_chap"] = draft_path.name
            st.switch_page("pages/polish.py")

with st.expander("高级：切换章节", expanded=False):
    polished_chapters = [p for p in chapters if chapter_stage_paths(p)["polished"].exists()]
    switch_options = polished_chapters or chapters
    labels = []
    for path in switch_options:
        ch_title = chapter_title_from_state(state, path.name) or path.stem
        ch_status = chapter_status(path, state)["label"]
        labels.append(f"{path.name} · {ch_title} · {ch_status}")
    current_index = next((i for i, p in enumerate(switch_options) if p.name == draft_path.name), 0)
    picked = st.selectbox(
        "章节",
        options=list(range(len(switch_options))),
        index=current_index,
        format_func=lambda i: labels[i],
        key="consistency_chapter_switch",
    )
    picked_name = switch_options[picked].name
    if picked_name != draft_path.name:
        st.session_state["active_chapter"] = picked_name
        st.rerun()

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
        disabled=(not has_key) or (not can_review),
        width="stretch",
        key="btn_rev",
        type="primary",
    )

st.divider()

# ---------------------------------------------------------------------------
# 运行 & 结果展示
# ---------------------------------------------------------------------------

chapter_text = input_path.read_text(encoding="utf-8") if can_review else ""
result_key = f"rev_result_{draft_path.name}_{input_path.name if can_review else 'missing'}"

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

            chapter_title = title
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

# ---------------------------------------------------------------------------
# 审查后修订与正式成稿
# ---------------------------------------------------------------------------

reviewed_path = stage_paths["reviewed"]
reviewed_text = reviewed_path.read_text(encoding="utf-8") if reviewed_path.exists() else chapter_text
final_path = final_chapter_path(draft_path, reviewed_text, state)

st.subheader("审查后修订")
st.caption("一致性检查只提供意见。请在这里手工采纳、改写或忽略，确认后再导出正式成稿。")

edit_key = f"rev_edit_{draft_path.name}_{int(reviewed_path.exists())}"
edited_text = st.text_area(
    "章节正文（可编辑）",
    value=reviewed_text,
    height=620,
    key=edit_key,
    disabled=not can_review,
)

save_col, final_col, info_col = st.columns([1, 1, 3])
with save_col:
    if st.button(
        "💾 保存审查后修订稿",
        width="stretch",
        key=f"btn_save_reviewed_{draft_path.name}",
        disabled=not can_review,
    ):
        reviewed_path.write_text(edited_text, encoding="utf-8")
        update_chapter_entry(
            str(camp.root),
            draft_path,
            reviewed_file=reviewed_path.name,
        )
        st.cache_data.clear()
        st.success(f"已保存：{reviewed_path.name}")
        st.rerun()

with final_col:
    if st.button(
        "✅ 确认并生成正式成稿",
        width="stretch",
        type="primary",
        key=f"btn_make_final_{draft_path.name}",
        disabled=not can_review,
    ):
        if not edited_text.strip():
            st.error("正文为空，无法生成正式成稿。")
        else:
            reviewed_path.write_text(edited_text, encoding="utf-8")
            final_path = final_chapter_path(draft_path, edited_text, state)
            final_path.write_text(edited_text, encoding="utf-8")
            update_chapter_entry(
                str(camp.root),
                draft_path,
                reviewed_file=reviewed_path.name,
                final_file=final_path.name,
                title=extract_markdown_title(edited_text) or title,
            )
            st.cache_data.clear()
            st.success(f"已生成正式成稿：{final_path.name}")

with info_col:
    st.caption(f"审查后修订稿：{reviewed_path.name}")
    st.caption(f"正式成稿将命名为：{final_chapter_path(draft_path, edited_text, state).name}")
    existing_final = final_chapter_path(draft_path, edited_text, state)
    if existing_final.exists():
        st.caption("同名正式成稿已存在，再次确认会覆盖。")

st.divider()

# 章节原文预览
with st.expander("📄 原始选中稿件（只读）", expanded=False):
    st.text_area(
        "检查输入稿件",
        value=chapter_text,
        height=500,
        key=f"rev_src_{draft_path.name}",
        disabled=True,
    )

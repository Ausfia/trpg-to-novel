"""📖 章节审稿 — 三栏布局：原始场景日志 / 章节草稿（含编辑） / 故事状态。"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from ui.shared import (
    KIND_LABEL,
    SEG_COLOR,
    SEG_LABEL,
    badge,
    list_chapters,
    list_sessions,
    load_scenes,
    load_state,
    load_tagged,
    require_campaign,
)

st.title("📖 章节审稿")
st.caption("左：原始场景日志 · 中：章节草稿（可编辑）· 右：故事状态")

camp = require_campaign()
if camp is None:
    st.stop()

sessions = list_sessions(camp)
chapters = list_chapters(camp)


# ---------------------------------------------------------------------------
# 控制栏
# ---------------------------------------------------------------------------

ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 3, 1, 1])

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
        sel_chap_name = None

# 章节元信息
if chapters and sel_chap is not None:
    state_yaml = load_state(str(camp.root))
    _index = state_yaml.get("chapter_index") or []
    _entry = next((e for e in _index if e.get("file") == sel_chap_name), None)
    if _entry:
        title = _entry.get("title", "")
        scene_n = len(_entry.get("scene_ids") or [])
        focus = "、".join(_entry.get("focus") or []) or "—"
        st.caption(f"📖 **{title}** · 入章 {scene_n} 场 · 焦点：{focus}")

with ctrl3:
    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
    if chapters and st.button(
        "🗑 删除章节",
        key="btn_del_chap_init",
        use_container_width=True,
        help="删除该章节的草稿、修订稿、润色稿",
    ):
        st.session_state["del_confirm_chap"] = sel_chap_name

with ctrl4:
    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
    if st.button("↺ 刷新", use_container_width=True, key="rev_refresh"):
        st.cache_data.clear()
        st.rerun()

# 删除确认对话
if chapters and st.session_state.get("del_confirm_chap") == sel_chap_name and sel_chap is not None:
    base_stem = sel_chap.stem.replace("_draft", "")
    related = [
        sel_chap,
        sel_chap.with_name(sel_chap.name.replace("_draft", "_revised")),
        sel_chap.with_name(base_stem + "_polished.md"),
    ]
    existing = [p for p in related if p.exists()]
    names_str = "、".join(p.name for p in existing) if existing else sel_chap_name
    st.warning(f"⚠ 即将删除：{names_str}，不可撤销。")
    dc1, dc2, _ = st.columns([1, 1, 4])
    if dc1.button("确认删除", type="primary", key="btn_del_chap_confirm"):
        deleted = []
        for p in existing:
            p.unlink()
            deleted.append(p.name)
        st.session_state.pop("del_confirm_chap", None)
        st.cache_data.clear()
        st.success(f"已删除：{', '.join(deleted) if deleted else '（无相关文件）'}")
        st.rerun()
    if dc2.button("取消", key="btn_del_chap_cancel"):
        st.session_state.pop("del_confirm_chap", None)
        st.rerun()

st.divider()


# ---------------------------------------------------------------------------
# 三栏主体
# ---------------------------------------------------------------------------

left, mid, right = st.columns([2, 3, 2], gap="medium")


# -------- 左：场景日志 --------
with left:
    st.subheader("原始场景日志")
    if sel_session:
        scenes = load_scenes(str(camp.parsed_dir), sel_session)
        tagged = load_tagged(str(camp.parsed_dir), sel_session)
        if scenes:
            labels = [
                f"{i+1}. {KIND_LABEL.get(s['kind'], s['kind'])} {s['start_ts']}–{s['end_ts']} ({len(s['event_ids'])} 条)"
                for i, s in enumerate(scenes)
            ]
            idx = st.selectbox(
                "场景",
                range(len(scenes)),
                format_func=lambda i: labels[i],
                key="rev_scene",
            )
            st.markdown("---")
            # --- 渲染场景内容 ---
            scene = scenes[idx]
            kind_lbl = KIND_LABEL.get(scene["kind"], scene["kind"])
            st.caption(
                f"{kind_lbl}  |  {scene['start_ts']} → {scene['end_ts']}  |  "
                f"触发: {', '.join(scene['triggers']) or '场次开始'}"
            )
            for event_id in scene["event_ids"]:
                ev = tagged.get(event_id)
                if ev is None:
                    continue
                segs = ev.get("segments", [])
                visible = [
                    s for s in segs
                    if s["kind"] not in ("pc_ooc", "roll_cmd", "bot_state", "record_meta", "image")
                ]
                if not visible:
                    continue
                lines = []
                for seg in visible:
                    kind = seg["kind"]
                    text = seg["text"].strip()
                    if not text:
                        continue
                    color = SEG_COLOR.get(kind, "#444")
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
                        label = SEG_LABEL.get(kind, kind)
                        lines.append(f"<span style='font-size:0.85em;color:#888'>[{label}] {text}</span>")
                if lines:
                    header = f"**{ev['speaker']}** `{ev['timestamp']}`"
                    st.markdown(
                        f"<div style='margin-bottom:8px'>{header}  \n" + "  \n".join(lines) + "</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.info("该场次暂无场景数据，请先完成 Segment。")
    else:
        st.info("选择左上方的场次后显示场景日志。")


# -------- 中：章节草稿 --------
with mid:
    st.subheader("章节草稿")
    if sel_chap is None:
        st.info("暂无章节草稿。请先在 Pipeline 页面运行 Draft。")
    else:
        text = sel_chap.read_text(encoding="utf-8")
        lines = text.splitlines()
        body_lines = []
        for line in lines:
            if line.startswith("<!--") and line.endswith("-->"):
                m = re.search(r"scenes: ([^|]+)\|.*events: (\d+)\|.*focus: ([^\-]+)", line)
                if m:
                    st.caption(
                        f"场景: {m.group(1).strip()}  |  事件: {m.group(2).strip()} 条  |  焦点: {m.group(3).strip()}"
                    )
            else:
                body_lines.append(line)
        body_text = "\n".join(body_lines)

        revised_path = sel_chap.with_name(sel_chap.name.replace("_draft", "_revised"))
        show_revised = revised_path.exists()
        display_text = revised_path.read_text(encoding="utf-8") if show_revised else body_text

        btn1, btn2 = st.columns([2, 3])
        with btn1:
            st.download_button(
                "⬇ 下载草稿",
                data=text.encode("utf-8"),
                file_name=sel_chap.name,
                mime="text/markdown",
                key=f"dl_{sel_chap.name}",
            )
        with btn2:
            if show_revised:
                st.caption(f"显示修订版：{revised_path.name}")
            else:
                st.caption(f"草稿：{sel_chap.name}")

        with st.container(height=600):
            st.markdown(display_text)

        with st.expander("✏️ 编辑草稿", expanded=False):
            edited = st.text_area(
                "章节正文",
                value=display_text,
                height=500,
                key=f"edit_{sel_chap.name}",
                label_visibility="collapsed",
            )
            if st.button("保存修订", key=f"save_{sel_chap.name}"):
                revised_path.write_text(edited, encoding="utf-8")
                st.success(f"已保存：{revised_path.name}")
                st.rerun()


# -------- 右：故事状态 --------
with right:
    st.subheader("故事状态")
    state = load_state(str(camp.root))
    if not state:
        st.info("未找到 story_state.yaml。")
    else:
        characters: dict = state.get("characters") or {}
        if characters:
            st.markdown("**角色状态**")
            for name, cs in characters.items():
                alive = cs.get("alive", True)
                lvl = cs.get("level", "?")
                conds = cs.get("conditions") or []
                cond_str = "、".join(conds) if conds else "无"
                status_icon = "❤️" if alive else "💀"
                with st.expander(f"{name}  Lv{lvl}  {status_icon}", expanded=False):
                    st.markdown(f"状态：{cond_str}")
                    if cs.get("notes"):
                        st.markdown(f"备注：{cs['notes']}")

        lore = state.get("lore_unlocked") or []
        if lore:
            st.markdown("**已解锁设定**")
            for item in lore:
                st.markdown(f"- {item}")

        chapter_index = state.get("chapter_index") or []
        processed_ids = set(state.get("processed_scene_ids") or [])
        st.markdown("**入章统计**")
        st.markdown(
            f"- 已生成章节：**{len(chapter_index)}**\n"
            f"- 已入章场景：**{len(processed_ids)}**"
        )

        session_log = state.get("session_log") or []
        if session_log:
            st.markdown("**已处理场次**")
            for s in session_log:
                st.markdown(f"- `{s}`")

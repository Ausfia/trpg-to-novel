"""📖 章节审稿 — 三栏布局：原始场景日志 / 章节草稿（含编辑） / 故事状态。"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st
from trpg2novel.chapterize.anchors import (
    ANCHOR_KEYS,
    anchor_path_for_chapter,
    empty_anchor_payload,
    load_anchor_file,
    save_anchor_file,
)

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
    save_state,
)

st.title("📖 章节审稿")
st.caption("左：原始场景日志 · 中：章节草稿（可编辑）· 右：故事状态")

ANCHOR_LABELS = {
    "actions": "关键动作",
    "dialogues": "关键台词",
    "choices": "角色选择",
    "emotions": "情绪/关系变化",
    "discarded_noise": "应舍弃噪音",
}


def _items_to_text(items: list[dict]) -> str:
    lines: list[str] = []
    for item in items:
        scene = item.get("scene_id") or ""
        speaker = item.get("speaker") or ""
        text = item.get("text") or ""
        prefix = " / ".join(x for x in (scene, speaker) if x)
        lines.append(f"{prefix}: {text}" if prefix else text)
    return "\n".join(lines)


def _text_to_items(text: str) -> list[dict]:
    items: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        speaker = ""
        scene_id = ""
        body = line
        if ": " in line:
            prefix, body = line.split(": ", 1)
            parts = [p.strip() for p in prefix.split("/") if p.strip()]
            if len(parts) >= 1:
                scene_id = parts[0]
            if len(parts) >= 2:
                speaker = parts[1]
        items.append({"scene_id": scene_id, "event_id": "", "speaker": speaker, "text": body.strip()})
    return items

camp = require_campaign()
if camp is None:
    st.stop()

sessions = list_sessions(camp)
chapters = list_chapters(camp)
state_yaml = load_state(str(camp.root))


# ---------------------------------------------------------------------------
# 控制栏
# ---------------------------------------------------------------------------

ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([2, 3, 1, 1, 1])

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
        "🔄 重新生成",
        key="btn_regen_chap_init",
        width="stretch",
        help="删除本章并清理状态，然后回到大纲规划页重新切章",
    ):
        st.session_state["regen_confirm_chap"] = sel_chap_name

with ctrl4:
    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
    if chapters and st.button(
        "🗑 删除章节",
        key="btn_del_chap_init",
        width="stretch",
        help="删除该章节的草稿、修订稿、润色稿",
    ):
        st.session_state["del_confirm_chap"] = sel_chap_name

with ctrl5:
    st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
    if st.button("↺ 刷新", width="stretch", key="rev_refresh"):
        st.cache_data.clear()
        st.rerun()

# 重新生成确认对话
if chapters and st.session_state.get("regen_confirm_chap") == sel_chap_name and sel_chap is not None:
    base_stem = sel_chap.stem.replace("_draft", "")
    related = [
        sel_chap,
        sel_chap.with_name(sel_chap.name.replace("_draft", "_revised")),
        sel_chap.with_name(base_stem + "_polished.md"),
        sel_chap.with_name(base_stem + "_reviewed.md"),
        sel_chap.with_name(base_stem + "_anchors.json"),
    ]
    _entry = next((e for e in (state_yaml.get("chapter_index") or []) if e.get("file") == sel_chap_name), None)
    if _entry and _entry.get("final_file"):
        related.append(sel_chap.with_name(_entry["final_file"]))
    existing = [p for p in related if p.exists()]
    names_str = "、".join(p.name for p in existing) if existing else sel_chap_name
    st.warning(f"将删除文件 {names_str} 并释放对应场景，之后请到大纲规划页重新切章。")
    rc1, rc2, _ = st.columns([1, 1, 4])
    if rc1.button("确认重新生成", type="primary", key="btn_regen_chap_confirm"):
        for p in existing:
            if p.exists():
                p.unlink()
        # 清理 state
        if _entry:
            processed = list(state_yaml.get("processed_scene_ids") or [])
            for sid in _entry.get("scene_ids") or []:
                if sid in processed:
                    processed.remove(sid)
            state_yaml["processed_scene_ids"] = processed
            state_yaml["chapter_index"] = [e for e in (state_yaml.get("chapter_index") or []) if e.get("file") != sel_chap_name]
            save_state(str(camp.root), state_yaml)
        st.session_state.pop("regen_confirm_chap", None)
        st.cache_data.clear()
        st.success(f"已清理 {sel_chap_name} 及其状态。请切换到「🧭 大纲规划」页面重新切章。")
        st.rerun()
    if rc2.button("取消", key="btn_regen_chap_cancel"):
        st.session_state.pop("regen_confirm_chap", None)
        st.rerun()

# 删除确认对话
if chapters and st.session_state.get("del_confirm_chap") == sel_chap_name and sel_chap is not None:
    base_stem = sel_chap.stem.replace("_draft", "")
    related = [
        sel_chap,
        sel_chap.with_name(sel_chap.name.replace("_draft", "_revised")),
        sel_chap.with_name(base_stem + "_polished.md"),
        sel_chap.with_name(base_stem + "_reviewed.md"),
        sel_chap.with_name(base_stem + "_anchors.json"),
    ]
    _entry = next((e for e in (state_yaml.get("chapter_index") or []) if e.get("file") == sel_chap_name), None)
    if _entry and _entry.get("final_file"):
        related.append(sel_chap.with_name(_entry["final_file"]))
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

# 孤儿条目检测：chapter_index 引用的文件已不存在
_chapter_index = state_yaml.get("chapter_index") or []
_orphaned = [e for e in _chapter_index if not (camp.chapters_dir / e["file"]).exists()]
if _orphaned:
    _names = "、".join(e["file"] for e in _orphaned)
    st.warning(f"状态文件记录了 {len(_orphaned)} 个章节但其草稿文件已不存在：{_names}")
    if st.button("🧹 清理孤儿条目（释放场景可重新入章）", key="btn_clean_orphans"):
        _processed = list(state_yaml.get("processed_scene_ids") or [])
        for e in _orphaned:
            for sid in e.get("scene_ids") or []:
                if sid in _processed:
                    _processed.remove(sid)
        state_yaml["processed_scene_ids"] = _processed
        state_yaml["chapter_index"] = [e for e in _chapter_index if e not in _orphaned]
        save_state(str(camp.root), state_yaml)
        st.cache_data.clear()
        st.success(f"已清理 {len(_orphaned)} 个孤儿条目，对应场景已释放。请切换到「🧭 大纲规划」页面重新切章。")
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
        st.info("暂无章节草稿。请先在大纲规划页确认卷并切章。")
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

        anchor_path = anchor_path_for_chapter(sel_chap)
        try:
            anchors = load_anchor_file(anchor_path) if anchor_path.exists() else empty_anchor_payload(
                chapter=sel_chap.name,
            )
            anchor_error = ""
        except Exception as exc:
            anchors = empty_anchor_payload(chapter=sel_chap.name)
            anchor_error = str(exc)

        with st.expander("📌 素材锚点（润色硬约束）", expanded=False):
            if anchor_error:
                st.warning(f"素材锚点读取失败，将以空锚点编辑：{anchor_error}")
            st.caption("这些内容会传入润色模型，用来保留跑团中的玩家动作、台词、选择和情绪关系。每行一条；删除行即可删除锚点。")
            edited_anchor_text: dict[str, str] = {}
            for key in ANCHOR_KEYS:
                edited_anchor_text[key] = st.text_area(
                    ANCHOR_LABELS[key],
                    value=_items_to_text(anchors.get(key) or []),
                    height=110 if key != "discarded_noise" else 80,
                    key=f"anchors_{key}_{sel_chap.name}",
                )
            if st.button("保存素材锚点", key=f"save_anchors_{sel_chap.name}", type="primary"):
                payload = dict(anchors)
                payload["chapter"] = sel_chap.name
                for key in ANCHOR_KEYS:
                    payload[key] = _text_to_items(edited_anchor_text[key])
                save_anchor_file(anchor_path, payload)
                st.success(f"已保存：{anchor_path.name}")
                st.rerun()


# -------- 右：故事状态 --------
with right:
    st.subheader("故事状态")
    state = state_yaml
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

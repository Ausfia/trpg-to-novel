"""🌍 世界观 — worldview.md 编辑 + 已解锁设定（可编辑）+ 角色/章节状态。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import yaml

from ui.shared import load_state, require_campaign

st.title("🌍 世界观")
st.caption("编辑世界观备忘录（注入起草 prompt），管理已解锁设定条目，查看故事状态。")

camp = require_campaign()
if camp is None:
    st.stop()

tab_wv, tab_lore, tab_state = st.tabs(["📝 worldview.md", "🗝️ 已解锁设定", "📜 故事状态"])


# ---------------------------------------------------------------------------
# Tab 1: worldview.md 编辑
# ---------------------------------------------------------------------------

with tab_wv:
    st.markdown(
        "下方文本会在起草章节时整体注入 prompt，作为世界观补充。"
        "建议记录关键历史事件、势力关系、地名说明、规则特例等。"
    )
    cur_wv = camp.worldview_md.read_text(encoding="utf-8") if camp.worldview_md.exists() else ""
    new_wv = st.text_area(
        "worldview.md 内容",
        value=cur_wv,
        height=500,
        key="wv_text",
        label_visibility="collapsed",
    )
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("保存", type="primary", key="btn_save_wv", use_container_width=True):
        camp.worldview_md.parent.mkdir(parents=True, exist_ok=True)
        camp.worldview_md.write_text(new_wv, encoding="utf-8")
        st.success("已保存 worldview.md")
    if c2.button("↺ 重新加载", key="btn_reload_wv", use_container_width=True):
        st.rerun()
    if cur_wv:
        st.caption(f"当前：{len(cur_wv)} 字符 / {cur_wv.count(chr(10)) + 1} 行")


# ---------------------------------------------------------------------------
# Tab 2: 已解锁设定（可编辑/删除/新增，保存到 story_state.yaml）
# ---------------------------------------------------------------------------

_STATE_PATH = camp.root / "story_state.yaml"
_LORE_KEY = f"lore_edit_{camp.id}"
_LORE_PAGE_KEY = f"lore_page_{camp.id}"
_LORE_LOADED_KEY = f"lore_loaded_{camp.id}"
_PAGE_SIZE = 15


def _load_state_raw() -> dict:
    if _STATE_PATH.exists():
        return yaml.safe_load(_STATE_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _save_lore(items: list[str]) -> None:
    state = _load_state_raw()
    state["lore_unlocked"] = [s.strip() for s in items if s.strip()]
    _STATE_PATH.write_text(
        yaml.safe_dump(state, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    st.cache_data.clear()


def _init_lore() -> None:
    if not st.session_state.get(_LORE_LOADED_KEY):
        raw = _load_state_raw().get("lore_unlocked") or []
        st.session_state[_LORE_KEY] = list(raw)
        st.session_state[_LORE_PAGE_KEY] = 0
        st.session_state[_LORE_LOADED_KEY] = True


with tab_lore:
    _init_lore()
    items: list[str] = st.session_state[_LORE_KEY]

    # ---- 顶部工具栏
    tool_col1, tool_col2, tool_col3 = st.columns([3, 1, 1])
    with tool_col1:
        search = st.text_input(
            "搜索",
            key="lore_search",
            placeholder="输入关键词过滤条目…",
            label_visibility="collapsed",
        )
    with tool_col2:
        if st.button("💾 保存全部", key="lore_save_top", use_container_width=True, type="primary"):
            _save_lore(items)
            st.success(f"已保存 {len([s for s in items if s.strip()])} 条设定到 story_state.yaml")
    with tool_col3:
        if st.button("↺ 从磁盘重载", key="lore_reload", use_container_width=True):
            st.session_state[_LORE_LOADED_KEY] = False
            st.rerun()

    st.caption(
        "修改、删除或新增条目后点「保存全部」写入 story_state.yaml；"
        "下次 Draft 和润色时会自动读取，直接影响叙事内容。"
    )
    st.divider()

    # ---- 过滤 + 分页
    lc = search.strip().lower()
    filtered = [(i, v) for i, v in enumerate(items) if not lc or lc in v.lower()]

    total = len(filtered)
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(st.session_state.get(_LORE_PAGE_KEY, 0), total_pages - 1)
    st.session_state[_LORE_PAGE_KEY] = page

    page_slice = filtered[page * _PAGE_SIZE: (page + 1) * _PAGE_SIZE]

    if not items:
        st.info("还没有解锁设定。跑过一次 Draft 后会自动写入，也可以在下方手动新增。")
    elif not page_slice:
        st.info("没有匹配的条目。")
    else:
        to_delete = -1
        for orig_idx, val in page_slice:
            row_c1, row_c2 = st.columns([10, 1])
            items[orig_idx] = row_c1.text_input(
                f"条目 {orig_idx + 1}",
                value=val,
                key=f"lore_item_{camp.id}_{orig_idx}",
                label_visibility="collapsed",
            )
            if row_c2.button("✕", key=f"lore_del_{camp.id}_{orig_idx}", help="删除此条"):
                to_delete = orig_idx

        if to_delete >= 0:
            items.pop(to_delete)
            st.session_state[_LORE_KEY] = items
            # 页数可能减少，回退一页
            new_total = len([v for v in items if not lc or lc in v.lower()])
            new_tp = max(1, (new_total + _PAGE_SIZE - 1) // _PAGE_SIZE)
            st.session_state[_LORE_PAGE_KEY] = min(page, new_tp - 1)
            st.rerun()

    # ---- 分页控件
    if total_pages > 1:
        st.divider()
        pg1, pg2, pg3 = st.columns([1, 3, 1])
        if pg1.button("← 上一页", key="lore_prev", disabled=page == 0, use_container_width=True):
            st.session_state[_LORE_PAGE_KEY] = page - 1
            st.rerun()
        pg2.markdown(
            f'<div style="text-align:center;padding-top:8px;color:var(--color-text-soft)">'
            f'第 {page + 1} / {total_pages} 页 · 共 {total} 条（全部 {len(items)} 条）'
            f'</div>',
            unsafe_allow_html=True,
        )
        if pg3.button("下一页 →", key="lore_next", disabled=page >= total_pages - 1, use_container_width=True):
            st.session_state[_LORE_PAGE_KEY] = page + 1
            st.rerun()

    # ---- 新增条目
    st.divider()
    st.markdown("**新增设定条目**")
    add_col1, add_col2 = st.columns([6, 1])
    new_item = add_col1.text_input(
        "新条目",
        key="lore_new_input",
        placeholder="如：银月城已被玩家夺回，统治者为 Ausfia",
        label_visibility="collapsed",
    )
    if add_col2.button("+ 添加", key="lore_add", use_container_width=True):
        stripped = new_item.strip()
        if stripped and stripped not in items:
            items.append(stripped)
            st.session_state[_LORE_KEY] = items
            # 跳到最后一页
            new_tp = max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)
            st.session_state[_LORE_PAGE_KEY] = new_tp - 1
            st.rerun()
        elif stripped in items:
            st.warning("该条目已存在。")
        else:
            st.warning("条目不能为空。")

    st.markdown(
        '<div style="font-size:12px;color:var(--color-text-soft);margin-top:8px">'
        '提示：修改完毕后须点上方「💾 保存全部」才会写入磁盘并在下次 Draft 中生效。'
        '</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Tab 3: 故事状态（只读总览）
# ---------------------------------------------------------------------------

with tab_state:
    state = load_state(str(camp.root))
    if not state:
        st.info("story_state.yaml 不存在或为空。跑过一次 Draft 后会自动生成。")
        st.stop()

    characters = state.get("characters") or {}
    chapter_index = state.get("chapter_index") or []
    processed_ids = set(state.get("processed_scene_ids") or [])
    lore_count = len(state.get("lore_unlocked") or [])

    m1, m2, m3 = st.columns(3)
    m1.metric("已生成章节", len(chapter_index))
    m2.metric("已入章场景", len(processed_ids))
    m3.metric("已解锁设定", lore_count, help="可在「已解锁设定」标签编辑")

    st.divider()

    left_s, right_s = st.columns(2)

    with left_s:
        st.markdown("#### 🎭 角色状态")
        if characters:
            for name, cs in characters.items():
                alive = cs.get("alive", True)
                lvl = cs.get("level", "?")
                conds = cs.get("conditions") or []
                icon = "❤️" if alive else "💀"
                cond_str = "、".join(conds) if conds else "无异常"
                with st.expander(f"{icon} {name}  Lv{lvl}", expanded=False):
                    st.markdown(f"**状态：** {cond_str}")
                    if cs.get("notes"):
                        st.markdown(f"**备注：** {cs['notes']}")
        else:
            st.caption("还没有角色记录。跑过一次 Draft 后会自动填充。")

    with right_s:
        st.markdown("#### 📖 章节目录")
        if chapter_index:
            for i, entry in enumerate(chapter_index, 1):
                title = entry.get("title") or entry.get("file", f"第 {i} 章")
                scene_n = len(entry.get("scene_ids") or [])
                focus = "、".join(entry.get("focus") or []) or "—"
                st.markdown(f"**{i}.** {title}  ·  {scene_n} 场  ·  焦点：{focus}")
        else:
            st.caption("章节目录为空。")

"""📊 仪表盘 — 当前团的可视化总览（首页）。

只读 / 纯展示：
- 顶部 4 指标卡（场次 / 场景 / 章节 / 已润色）
- 入章进度环
- PC 状态卡
- 最近章节列表
- LLM 阶段健康
- KB 状态
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import streamlit as st

from ui.shared import (
    STAGES,
    STAGE_ICONS,
    badge,
    list_chapters,
    list_raw_sessions,
    list_sessions,
    load_scenes,
    load_state,
    metric_html,
    read_env,
    require_campaign,
    stage_value,
)

st.title("📊 仪表盘")
st.caption("当前团的总览。所有数据来自本地文件，不会发起 LLM 请求。")

camp = require_campaign()
if camp is None:
    st.stop()


# ---------------------------------------------------------------------------
# 数据采集
# ---------------------------------------------------------------------------

state = load_state(str(camp.root))
chapter_index = state.get("chapter_index") or []
processed_ids: set[str] = set(state.get("processed_scene_ids") or [])

raw_sids = list_raw_sessions(camp)              # raw_logs/ 下所有 sid
seg_sids = list_sessions(camp)                  # 完成 segment 的 sid

total_scenes = 0
for sid in seg_sids:
    total_scenes += len(load_scenes(str(camp.parsed_dir), sid))

chapter_files = list_chapters(camp)
polished_count = 0
for chap in chapter_files:
    polished = chap.with_name(chap.stem.replace("_draft", "") + "_polished.md")
    if polished.exists():
        polished_count += 1

pending_scenes = max(0, total_scenes - len(processed_ids))


# ---------------------------------------------------------------------------
# Section 1：4 指标卡
# ---------------------------------------------------------------------------

m1, m2, m3, m4 = st.columns(4, gap="medium")

with m1:
    hint = f"已切分 {len(seg_sids)}" if len(seg_sids) < len(raw_sids) else "全部已切分"
    st.markdown(metric_html("已跑场次", str(len(raw_sids)), hint, kind="accent"), unsafe_allow_html=True)

with m2:
    hint = f"待入章 {pending_scenes}" if pending_scenes else "全部已入章"
    st.markdown(metric_html("已切分场景", str(total_scenes), hint, kind="info"), unsafe_allow_html=True)

with m3:
    hint = f"待润色 {len(chapter_files) - polished_count}" if len(chapter_files) > polished_count else "全部已润色"
    st.markdown(metric_html("已生成章节", str(len(chapter_files)), hint, kind="warning"), unsafe_allow_html=True)

with m4:
    rate = (polished_count / len(chapter_files) * 100) if chapter_files else 0
    hint = f"占比 {rate:.0f}%" if chapter_files else "—"
    st.markdown(metric_html("已润色章节", str(polished_count), hint, kind="success"), unsafe_allow_html=True)


st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 2：左 = 入章进度 + 最近章节 / 右 = PC 状态 + LLM 健康
# ---------------------------------------------------------------------------

left_col, right_col = st.columns([2, 1], gap="large")


# ============ 左列 ============
with left_col:
    # 入章进度
    if total_scenes > 0:
        pct = len(processed_ids) / total_scenes * 100
        st.markdown(
            f"""
            <div class="tn-card">
                <div class="tn-card-title">场景入章进度</div>
                <div style="display:flex; align-items:baseline; gap:14px; margin-bottom:10px">
                    <div style="font-size:36px; font-weight:700; color:var(--color-accent)">{pct:.0f}%</div>
                    <div style="color:var(--color-text-soft)">
                        <strong>{len(processed_ids)}</strong> 场景已入章，剩余 <strong>{pending_scenes}</strong> 场待处理
                    </div>
                </div>
                <div style="background:var(--color-bg-soft); border-radius:6px; height:12px; overflow:hidden">
                    <div style="background:linear-gradient(90deg, var(--color-accent-soft), var(--color-accent));
                                width:{pct:.1f}%; height:100%; border-radius:6px"></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 最近章节列表
    st.markdown('<div class="tn-card-title" style="margin-top:18px">📖 最近章节</div>', unsafe_allow_html=True)
    if not chapter_files:
        st.markdown(
            '<div class="tn-empty">'
            '<div class="tn-empty-icon">📄</div>'
            '还没有章节草稿。<br>到 <strong>✍️ Pipeline</strong> 页面上传日志、跑完场景切分后即可生成。'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        # chapter_index 是 dict 列表，可能比 chapter_files 少（如手工创建未回填）
        entry_by_file = {e.get("file"): e for e in chapter_index}
        # 取最近 6 个
        recent = sorted(chapter_files, key=lambda p: p.name, reverse=True)[:6]
        rows = []
        for chap in recent:
            entry = entry_by_file.get(chap.name) or {}
            title = entry.get("title") or chap.stem
            scene_n = len(entry.get("scene_ids") or [])
            focus = "、".join(entry.get("focus") or [])
            polished_path = chap.with_name(chap.stem.replace("_draft", "") + "_polished.md")
            revised_path = chap.with_name(chap.name.replace("_draft", "_revised"))
            if polished_path.exists():
                status_badge = badge("已润色", "ok")
                words = len(polished_path.read_text(encoding="utf-8"))
            elif revised_path.exists():
                status_badge = badge("待润色", "warn")
                words = len(revised_path.read_text(encoding="utf-8"))
            else:
                status_badge = badge("草稿", "info")
                words = len(chap.read_text(encoding="utf-8"))
            meta_bits = [f"{scene_n} 场" if scene_n else "—"]
            if focus:
                meta_bits.append(f"焦点：{focus}")
            meta_bits.append(f"{words // 1000}.{(words % 1000) // 100}k 字")
            rows.append(
                f'<div class="tn-chapter-row">'
                f'<span class="ch-icon">📖</span>'
                f'<span class="ch-title">{title}</span>'
                f'<span class="ch-meta">{" · ".join(meta_bits)}</span>'
                f"{status_badge}"
                f"</div>"
            )
        st.markdown("\n".join(rows), unsafe_allow_html=True)
        if len(chapter_files) > 6:
            st.caption(f"… 另有 {len(chapter_files) - 6} 章，去「📖 章节审稿」查看全部")


# ============ 右列 ============
with right_col:
    # PC 状态
    st.markdown('<div class="tn-card-title">👥 PC 状态</div>', unsafe_allow_html=True)
    characters: dict = state.get("characters") or {}
    if not characters:
        st.markdown(
            '<div class="tn-empty">'
            '<div class="tn-empty-icon">🎭</div>'
            'story_state.yaml 里没有角色记录。<br>跑过一次 Draft 后会自动填充。'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        rows = []
        for name, cs in characters.items():
            alive = cs.get("alive", True)
            lvl = cs.get("level", "?")
            conds = cs.get("conditions") or []
            status_icon = "❤️" if alive else "💀"
            cond_str = "、".join(conds) if conds else "健康"
            rows.append(
                f'<div class="tn-pc-row">'
                f'<span class="tn-pc-avatar">🎭</span>'
                f'<span class="tn-pc-name">{name}</span>'
                f'<span class="tn-pc-meta">Lv{lvl} {status_icon} {cond_str}</span>'
                f"</div>"
            )
        st.markdown("\n".join(rows), unsafe_allow_html=True)

    # LLM 阶段健康
    st.markdown('<div class="tn-card-title" style="margin-top:18px">⚙️ LLM 阶段</div>', unsafe_allow_html=True)
    env = read_env()
    rows = []
    for stage, label in STAGES:
        api_ok = bool(stage_value(env, stage, "api_key").strip())
        model = stage_value(env, stage, "model")
        base_url = stage_value(env, stage, "base_url")
        b = badge("已配置", "ok") if api_ok else badge("未配置", "warn")
        icon = STAGE_ICONS.get(stage, "")
        rows.append(
            f'<div class="tn-pc-row" style="background:var(--color-bg-soft)">'
            f'<span class="tn-pc-avatar">{icon}</span>'
            f'<span class="tn-pc-name">{label}'
            f'<div class="tn-pc-meta" style="font-size:11px">{model or "—"}</div></span>'
            f"{b}"
            f"</div>"
        )
    st.markdown("\n".join(rows), unsafe_allow_html=True)

    # KB 状态
    st.markdown('<div class="tn-card-title" style="margin-top:18px">📚 知识库</div>', unsafe_allow_html=True)
    kb_html = None
    try:
        from trpg2novel.rag import KnowledgeBase, load_kb_config
        kb_cfg = load_kb_config(camp.kb_config_yaml)
        if not kb_cfg.is_configured():
            kb_html = (
                '<div class="tn-pc-row">'
                '<span class="tn-pc-avatar">📚</span>'
                '<span class="tn-pc-name">未配置 Embedding</span>'
                + badge("待设置", "warn") +
                "</div>"
            )
        else:
            kb = KnowledgeBase.open(camp.knowledge_base_dir, kb_cfg)
            cnt = kb.count_chunks()
            sources = kb.list_sources()
            if cnt > 0:
                kb_html = (
                    '<div class="tn-pc-row">'
                    '<span class="tn-pc-avatar">📚</span>'
                    f'<span class="tn-pc-name">{cnt} 片段'
                    f'<div class="tn-pc-meta" style="font-size:11px">{len(sources)} 源文件 · {kb_cfg.model}</div></span>'
                    + badge("索引就绪", "ok") +
                    "</div>"
                )
            else:
                kb_html = (
                    '<div class="tn-pc-row">'
                    '<span class="tn-pc-avatar">📚</span>'
                    f'<span class="tn-pc-name">{len(sources)} 源文件'
                    '<div class="tn-pc-meta" style="font-size:11px">索引未重建</div></span>'
                    + badge("空索引", "warn") +
                    "</div>"
                )
    except Exception as e:
        kb_html = (
            '<div class="tn-pc-row">'
            '<span class="tn-pc-avatar">📚</span>'
            f'<span class="tn-pc-name">不可用</span>'
            f'<span class="tn-pc-meta" style="font-size:11px">{type(e).__name__}</span>'
            "</div>"
        )
    if kb_html:
        st.markdown(kb_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Section 3：底部信息
# ---------------------------------------------------------------------------

st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
foot_l, foot_r = st.columns(2)

with foot_l:
    st.markdown('<div class="tn-card-title">📁 团信息</div>', unsafe_allow_html=True)
    fields = [
        ("ID", camp.id),
        ("名称", camp.name),
        ("系统", camp.system),
        ("根目录", str(camp.root)),
    ]
    rows = [
        f'<div style="display:flex; gap:14px; padding:6px 0; border-bottom:1px solid var(--color-border)">'
        f'<span style="color:var(--color-text-soft); width:80px">{k}</span>'
        f'<span><code>{v}</code></span></div>'
        for k, v in fields
    ]
    st.markdown('<div class="tn-card">' + "".join(rows) + "</div>", unsafe_allow_html=True)

with foot_r:
    st.markdown('<div class="tn-card-title">🔓 已解锁设定</div>', unsafe_allow_html=True)
    lore = state.get("lore_unlocked") or []
    if not lore:
        st.markdown(
            '<div class="tn-empty" style="padding:20px"><div class="tn-empty-icon">🗝️</div>'
            '还没有解锁的设定条目。</div>',
            unsafe_allow_html=True,
        )
    else:
        rows = "".join(f'<div style="padding:4px 0">• {item}</div>' for item in lore)
        st.markdown(f'<div class="tn-card">{rows}</div>', unsafe_allow_html=True)

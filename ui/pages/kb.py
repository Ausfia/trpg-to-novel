"""📚 知识库（RAG）— Embedding 配置 / 源文件管理 / 重建索引 / 检索预览。"""

from __future__ import annotations

import streamlit as st

from ui.shared import model_picker_widget, require_campaign

st.title("📚 知识库 (RAG)")
st.caption(
    "把世界观资料（种族设定、地理、组织、历史）拆成多个 .md/.txt 放进知识库；"
    "起草章节时会自动检索 top-K 片段注入 prompt。"
)

camp = require_campaign()
if camp is None:
    st.stop()

try:
    from trpg2novel.rag import KnowledgeBase, load_kb_config, save_kb_config
    from trpg2novel.rag.config import KBConfig
except ImportError as e:
    st.error(f"RAG 模块加载失败：{e}。请运行 `pip install sqlite-vec`。")
    st.stop()


# ---------------------------------------------------------------------------
# Embedding 配置
# ---------------------------------------------------------------------------

st.markdown("### ⚙️ Embedding 配置")
cfg = load_kb_config(camp.kb_config_yaml)

c1, c2 = st.columns(2)
kb_api_key = c1.text_input(
    "API Key", value=cfg.api_key, type="password", key=f"kb_api_{camp.id}"
)
kb_base_url = c2.text_input(
    "Base URL", value=cfg.base_url, key=f"kb_base_url_{camp.id}"
)

st.markdown("**Embedding 模型**")
kb_model = model_picker_widget(
    fetch_key=f"kb_{camp.id}",
    model_input_key=f"kb_model_{camp.id}",
    current_model=cfg.model,
    api_key=kb_api_key,
    base_url=kb_base_url,
)

c4, c5 = st.columns(2)
kb_dim = c4.number_input(
    "向量维度", value=cfg.dim, min_value=64, max_value=4096, key=f"kb_dim_{camp.id}"
)
kb_top_k = c5.number_input(
    "检索 top-K", value=cfg.top_k, min_value=1, max_value=20, key=f"kb_top_k_{camp.id}"
)
c6, c7 = st.columns(2)
kb_chunk_size = c6.number_input(
    "分块大小（字符）", value=cfg.chunk_size, min_value=100, max_value=2000,
    key=f"kb_chunk_{camp.id}",
)
kb_chunk_overlap = c7.number_input(
    "分块重叠（字符）", value=cfg.chunk_overlap, min_value=0, max_value=500,
    key=f"kb_overlap_{camp.id}",
)
kb_min_score = st.slider(
    "最小相似度阈值",
    min_value=0.0, max_value=1.0,
    value=float(cfg.min_score),
    step=0.05,
    key=f"kb_score_{camp.id}",
)

if st.button("保存配置", type="primary", key=f"kb_cfg_save_{camp.id}"):
    new_cfg = KBConfig(
        api_key=kb_api_key.strip(),
        base_url=kb_base_url.strip(),
        model=kb_model.strip(),
        dim=int(kb_dim),
        chunk_size=int(kb_chunk_size),
        chunk_overlap=int(kb_chunk_overlap),
        top_k=int(kb_top_k),
        min_score=float(kb_min_score),
    )
    save_kb_config(new_cfg, camp.kb_config_yaml)
    cfg = new_cfg
    st.success("已保存 kb_config.yaml")

st.divider()

# 打开 KB（用当前 cfg）
kb = KnowledgeBase.open(camp.knowledge_base_dir, cfg)

# ---------------------------------------------------------------------------
# 源文件管理
# ---------------------------------------------------------------------------

st.markdown("### 📂 知识源文件")
sources = kb.list_sources()
chunk_count = kb.count_chunks()

if sources:
    st.markdown(
        f"当前共 **{len(sources)}** 个源文件，索引中 **{chunk_count}** 片段："
    )
    for src in sources:
        cc1, cc2, cc3 = st.columns([4, 1, 1])
        size_kb = src.stat().st_size / 1024
        cc1.markdown(f"`{src.name}` — {size_kb:.1f} KB")
        if cc2.button("查看", key=f"kb_view_{src.name}"):
            st.session_state["kb_view_file"] = src.name
        if cc3.button("删除", key=f"kb_del_{src.name}"):
            src.unlink()
            st.success(f"已删除：{src.name}")
            st.rerun()

    if st.session_state.get("kb_view_file"):
        fname = st.session_state["kb_view_file"]
        view_src = kb.sources_dir / fname
        if view_src.exists():
            with st.expander(f"📄 {fname}", expanded=True):
                st.text_area(
                    "内容",
                    value=view_src.read_text(encoding="utf-8"),
                    height=300,
                    disabled=True,
                    key=f"kb_view_area_{fname}",
                )
                if st.button("关闭", key=f"kb_view_close_{fname}"):
                    st.session_state.pop("kb_view_file", None)
                    st.rerun()
else:
    st.info("知识库为空。在下方上传 .md 或 .txt 文件。")

# 上传
up_files = st.file_uploader(
    "上传知识源 (.md / .txt，可多选)",
    type=["md", "txt"],
    accept_multiple_files=True,
    key="kb_upload",
)
if up_files:
    if st.button(f"保存 {len(up_files)} 个文件到 sources/", key="kb_btn_save_sources"):
        kb.sources_dir.mkdir(parents=True, exist_ok=True)
        for f in up_files:
            out = kb.sources_dir / f.name
            out.write_bytes(f.getvalue())
        st.success(f"已写入 {len(up_files)} 个文件，记得点「重建索引」。")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 重建索引
# ---------------------------------------------------------------------------

st.markdown("### 🔨 重建索引")
if not cfg.is_configured():
    st.warning("请先填写 Embedding API Key 并保存配置。")
elif not sources:
    st.info("没有源文件可索引。")
else:
    st.caption("会清空当前索引并对所有源文件重新 embed，耗时取决于文件量和 API 速度。")
    if st.button(
        "🔨 重建索引（清空并重新 embed 全部源）",
        type="secondary",
        key="kb_rebuild",
    ):
        progress = st.progress(0.0)
        status = st.empty()

        def _cb(stage, current, total):
            if total > 0:
                progress.progress(min(current / total, 1.0))
            status.text(f"{stage}: {current}/{total}")

        try:
            with st.spinner("正在调用 embedding API…"):
                res = kb.rebuild_from_sources(progress_cb=_cb)
            st.success(
                f"索引重建完成：{res['sources']} 源文件 / {res['chunks']} 片段"
            )
        except Exception as e:
            st.error(f"重建失败：{e}")
        finally:
            progress.empty()
            status.empty()

st.divider()

# ---------------------------------------------------------------------------
# 检索预览
# ---------------------------------------------------------------------------

st.markdown("### 🔍 检索预览")
st.caption("输入 query 验证 KB 质量，看场景相关描述能否命中。")

query = st.text_input("输入 query", key="kb_query_input", placeholder="例：银月城的守卫")
preview_k = st.slider("top-K", min_value=1, max_value=20, value=cfg.top_k, key="kb_preview_k")

if query.strip():
    if not cfg.is_configured():
        st.warning("需要先配置 Embedding API Key 才能检索。")
    elif chunk_count == 0:
        st.info("索引为空，请先重建索引。")
    elif st.button("检索", key="kb_btn_search"):
        try:
            with st.spinner("embedding query…"):
                hits = kb.query(query, top_k=preview_k)
            if not hits:
                st.info("无结果（KB 可能为空，或所有片段相似度均低于阈值）。")
            else:
                for i, h in enumerate(hits, 1):
                    with st.expander(
                        f"#{i}  【{h.source}】  score={h.score:.3f}  dist={h.distance:.3f}"
                    ):
                        st.write(h.text)
        except Exception as e:
            st.error(f"检索失败：{e}")

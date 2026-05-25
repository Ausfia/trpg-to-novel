"""📚 知识库（RAG）— Embedding 配置 / 源文件管理 / 重建索引 / 检索预览。"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

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
    missing = getattr(e, "name", "")
    if missing == "sqlite_vec":
        st.error("RAG 模块加载失败：缺少 sqlite-vec。请在当前 WebUI 使用的 Python 环境中运行 `pip install sqlite-vec`。")
    elif missing == "openai":
        st.error("RAG 模块加载失败：缺少 openai。请在当前 WebUI 使用的 Python 环境中运行 `pip install -e \".[dev]\"` 或 `pip install openai`。")
    else:
        st.error(f"RAG 模块加载失败：{e}")
    st.stop()


# ---------------------------------------------------------------------------
# 模块级后台重建状态 + KB 实例缓存（跨页切换不丢失，同一 Python 进程内共享）
# ---------------------------------------------------------------------------

# {camp_id: {"running": bool, "stage": str, "current": int, "total": int,
#             "result": dict|None, "error": str|None}}
_rebuild_states: dict[str, dict] = {}
_rebuild_lock = threading.Lock()

# {camp_id: KnowledgeBase} —— 避免每次进页面都重跑 _ensure_schema
_kb_cache: dict[str, "KnowledgeBase"] = {}


def _get_kb(camp_id: str, kb_dir: Path, cfg: "KBConfig") -> "KnowledgeBase":
    """从模块缓存取 KB；维度变化时丢弃旧实例重建。"""
    cached = _kb_cache.get(camp_id)
    if cached is not None and cached.cfg.dim == cfg.dim:
        cached.cfg = cfg  # 同步其他字段
        return cached
    if cached is not None:
        try:
            cached.close()
        except Exception:
            pass
    kb_new = KnowledgeBase.open(kb_dir, cfg)
    _kb_cache[camp_id] = kb_new
    return kb_new


def _rebuild_worker(kb: "KnowledgeBase", camp_id: str) -> None:
    print(f"[kb-rebuild] worker started for camp={camp_id}", flush=True)

    def cb(stage: str, current: int, total: int) -> None:
        print(f"[kb-rebuild] {stage} {current}/{total}", flush=True)
        with _rebuild_lock:
            _rebuild_states[camp_id].update(stage=stage, current=current, total=total)

    try:
        res = kb.rebuild_from_sources(progress_cb=cb)
        print(f"[kb-rebuild] done: {res}", flush=True)
        with _rebuild_lock:
            _rebuild_states[camp_id].update(running=False, result=res, error=None)
    except Exception as exc:
        err_msg = f"{exc}\n\n{traceback.format_exc()}"
        print(f"[kb-rebuild] FAILED: {err_msg}", flush=True)
        with _rebuild_lock:
            _rebuild_states[camp_id].update(running=False, error=err_msg, result=None)


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

_can_test_emb = bool(kb_api_key.strip() and kb_model.strip())
_tcol1, _tcol2 = st.columns([1, 3])
if _tcol1.button(
    "🧪 测试 Embedding",
    key=f"kb_test_emb_{camp.id}",
    disabled=not _can_test_emb,
    width="stretch",
):
    with st.spinner(f"测试 {kb_model} …"):
        try:
            from trpg2novel.rag.embedder import embed_texts
            vecs = embed_texts(
                ["test"],
                api_key=kb_api_key.strip(),
                base_url=kb_base_url.strip(),
                model=kb_model.strip(),
            )
            dim = len(vecs[0]) if vecs else 0
            _tcol2.success(f"✅ 可用，返回维度：{dim}")
        except Exception as _exc:
            _tcol2.error(f"❌ 失败：{_exc}")

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

# 打开 KB（模块缓存，避免每次进页面重跑 schema）
kb = _get_kb(camp.id, camp.knowledge_base_dir, cfg)

# ---------------------------------------------------------------------------
# 源文件管理
# ---------------------------------------------------------------------------

st.markdown("### 📂 知识源文件")
sources = kb.list_sources()
chunk_count = kb.count_chunks()

if sources:
    total_kb = sum(s.stat().st_size for s in sources) / 1024
    st.markdown(
        f"当前共 **{len(sources)}** 个源文件（{total_kb:.0f} KB），索引中 **{chunk_count}** 片段"
    )

    # 文件数过多时改为搜索模式，避免一次渲染上千 widget 拖垮 Streamlit
    filter_kw = st.text_input(
        "🔍 按文件名筛选（留空展示前 50 个）",
        key=f"kb_src_filter_{camp.id}",
        placeholder="例：races, spell, dragon",
    ).strip().lower()
    if filter_kw:
        matched = [s for s in sources if filter_kw in s.name.lower()]
    else:
        matched = sources[:50]
    st.caption(f"当前显示 {len(matched)} / {len(sources)} 个文件")

    with st.container(height=260):
        for src in matched:
            cc1, cc2, cc3 = st.columns([4, 1, 1])
            try:
                size_kb = src.stat().st_size / 1024
                cc1.markdown(f"`{src.name}` — {size_kb:.1f} KB")
            except OSError:
                cc1.markdown(f"`{src.name}` — (文件不可读)")
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

    # 危险操作：批量清空
    with st.expander("⚠️ 危险：批量清空源文件", expanded=False):
        st.caption("一次性删除 sources/ 下所有 .md / .txt 文件，会同时清空索引。")
        confirm_text = st.text_input(
            f"输入 `DELETE ALL {len(sources)}` 以确认",
            key=f"kb_purge_confirm_{camp.id}",
        )
        purge_token = f"DELETE ALL {len(sources)}"
        if st.button(
            "🗑️ 清空全部源文件",
            key=f"kb_btn_purge_{camp.id}",
            disabled=confirm_text != purge_token,
        ):
            removed = 0
            for s in sources:
                try:
                    s.unlink()
                    removed += 1
                except Exception:
                    pass
            kb.reset()
            st.success(f"已删除 {removed} 个源文件并清空索引")
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

# 从本地文件夹导入
with st.expander("📁 从本地文件夹导入（递归扫描所有 .md / .txt）", expanded=False):
    folder_path = st.text_input(
        "文件夹路径",
        key="kb_folder_path",
        placeholder=r"例：D:\TRPG\worldview",
        help="输入本机文件夹的绝对路径，会递归扫描所有子文件夹中的 .md 和 .txt 文件",
    )
    scan_key = f"kb_folder_scan_{camp.id}"
    if st.button("🔍 扫描文件夹", key="kb_btn_scan_folder", disabled=not folder_path.strip()):
        fp = Path(folder_path.strip())
        if not fp.exists():
            st.error(f"路径不存在：{fp}")
        elif not fp.is_dir():
            st.error(f"不是文件夹：{fp}")
        else:
            found = sorted(set(fp.rglob("*.md")) | set(fp.rglob("*.txt")))
            st.session_state[scan_key] = [str(p) for p in found]

    if scan_key in st.session_state:
        found_paths = st.session_state[scan_key]
        if not found_paths:
            st.info("未找到任何 .md / .txt 文件。")
        else:
            st.markdown(f"找到 **{len(found_paths)}** 个文件：")
            with st.container(height=200):
                for p in found_paths:
                    st.caption(p)

            if st.button(
                f"📥 导入全部 {len(found_paths)} 个文件到 sources/",
                key="kb_btn_import_folder",
                type="primary",
            ):
                kb.sources_dir.mkdir(parents=True, exist_ok=True)
                imported, skipped = 0, 0
                for p_str in found_paths:
                    src = Path(p_str)
                    if not src.exists():
                        continue
                    dest = kb.sources_dir / src.name
                    # 同名冲突时加父目录前缀避免覆盖
                    if dest.exists() and dest.resolve() != src.resolve():
                        dest = kb.sources_dir / f"{src.parent.name}__{src.name}"
                    try:
                        dest.write_bytes(src.read_bytes())
                        imported += 1
                    except Exception:
                        skipped += 1
                st.session_state.pop(scan_key, None)
                msg = f"已导入 {imported} 个文件"
                if skipped:
                    msg += f"，{skipped} 个失败"
                st.success(msg + "，记得点「重建索引」。")
                st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 重建索引（后台线程，切换页面不中断）
# ---------------------------------------------------------------------------

st.markdown("### 🔨 重建索引")

rb_state = _rebuild_states.get(camp.id, {})
is_running = rb_state.get("running", False)

if not cfg.is_configured():
    st.warning("请先填写 Embedding API Key 并保存配置。")
elif not sources:
    st.info("没有源文件可索引。")
else:
    if not is_running:
        st.caption("会清空当前索引并对所有源文件重新 embed，耗时取决于文件量和 API 速度。切换页面不影响进度。")
        if st.button(
            "🔨 重建索引（清空并重新 embed 全部源）",
            type="secondary",
            key="kb_rebuild",
        ):
            with _rebuild_lock:
                _rebuild_states[camp.id] = {
                    "running": True,
                    "stage": "准备中…",
                    "current": 0,
                    "total": 0,
                    "result": None,
                    "error": None,
                }
            t = threading.Thread(
                target=_rebuild_worker,
                args=(kb, camp.id),
                daemon=True,
            )
            t.start()
            st.rerun()


@st.fragment(run_every="1.5s")
def _show_rebuild_progress(camp_id: str) -> None:
    rb = _rebuild_states.get(camp_id)
    if rb is None:
        return

    running = rb.get("running", False)
    error = rb.get("error")
    result = rb.get("result")

    if running:
        stage = rb.get("stage", "")
        current = rb.get("current", 0)
        total = rb.get("total", 0)
        pct = (current / total) if total > 0 else 0.0
        st.progress(pct)
        if total > 0:
            st.caption(f"⏳ {stage}  {current} / {total} 片段（每 1.5 秒自动刷新，可自由切换页面）")
        else:
            st.caption(f"⏳ {stage}（每 1.5 秒自动刷新，可自由切换页面）")
    elif error:
        st.error(f"重建失败：{error}")
        if st.button("清除错误", key="kb_clear_err"):
            _rebuild_states.pop(camp_id, None)
            st.rerun()
    elif result:
        st.success(
            f"✅ 索引重建完成：{result.get('sources', '?')} 源文件 / {result.get('chunks', '?')} 片段"
        )
        if st.button("清除提示", key="kb_clear_ok"):
            _rebuild_states.pop(camp_id, None)
            st.rerun()


if camp.id in _rebuild_states:
    _show_rebuild_progress(camp.id)

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

"""🎨 风格方案 — 管理 Polish 使用的写作偏好与风格参考资料。"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

import streamlit as st

from ui.shared import model_picker_widget, require_campaign

st.title("🎨 风格方案")
st.caption(
    "用中文描述你想要的文风；也可以让模型根据描述和风格资料库自动生成方案，保存前你可以审查和修改。"
)

camp = require_campaign()
if camp is None:
    st.stop()

try:
    from trpg2novel.config import load_llm_settings
    from trpg2novel.rag import KnowledgeBase, load_kb_config, save_kb_config
    from trpg2novel.rag.config import KBConfig
    from trpg2novel.style import (
        StyleProfile,
        list_style_profiles,
        list_style_recipes,
        load_style_profile,
        load_style_recipe,
        profile_from_recipe,
        save_style_profile,
    )
except ImportError as e:
    missing = getattr(e, "name", "")
    if missing == "sqlite_vec":
        st.error("风格方案加载失败：缺少 sqlite-vec。请在当前 WebUI 使用的 Python 环境中运行 `pip install sqlite-vec`。")
    elif missing == "openai":
        st.error("风格方案加载失败：缺少 openai。请在当前 WebUI 使用的 Python 环境中运行 `pip install -e \".[dev]\"` 或 `pip install openai`。")
    else:
        st.error(f"风格方案加载失败：{e}")
    st.stop()


@st.cache_resource
def _get_rebuild_runtime() -> tuple[dict[str, dict], threading.Lock]:
    return {}, threading.Lock()


_rebuild_states, _rebuild_lock = _get_rebuild_runtime()
_kb_cache: dict[str, "KnowledgeBase"] = {}


def _get_kb(camp_id: str, kb_dir: Path, cfg: "KBConfig") -> "KnowledgeBase":
    cached = _kb_cache.get(camp_id)
    if cached is not None and cached.cfg.dim == cfg.dim:
        cached.cfg = cfg
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
    def cb(stage: str, current: int, total: int) -> None:
        with _rebuild_lock:
            _rebuild_states[camp_id].update(stage=stage, current=current, total=total)

    try:
        res = kb.rebuild_from_sources(progress_cb=cb)
        with _rebuild_lock:
            _rebuild_states[camp_id].update(running=False, result=res, error=None)
    except Exception as exc:
        err_msg = f"{exc}\n\n{traceback.format_exc()}"
        with _rebuild_lock:
            _rebuild_states[camp_id].update(running=False, error=err_msg, result=None)


def _lines_to_list(text: str) -> list[str]:
    return [x.strip() for x in text.splitlines() if x.strip()]


def _list_to_lines(values: list[str]) -> str:
    return "\n".join(values)


def _profile_state_key(profile_id: str) -> str:
    return f"style_profile_draft_{camp.id}_{profile_id}"


def _current_profile(profile: "StyleProfile") -> "StyleProfile":
    draft = st.session_state.get(_profile_state_key(profile.id))
    if isinstance(draft, dict):
        return StyleProfile.from_dict(draft)
    return profile


# ---------------------------------------------------------------------------
# 风格方案管理
# ---------------------------------------------------------------------------

st.markdown("### 🧭 当前风格方案")
profile_paths = list_style_profiles(camp)
profile_options = [p.stem for p in profile_paths]
selected_profile_id = st.selectbox("选择风格方案", profile_options, key=f"style_profile_select_{camp.id}")
profile = _current_profile(load_style_profile(selected_profile_id, campaign=camp))

with st.expander("➕ 新建 / 复制风格方案", expanded=False):
    c_new1, c_new2 = st.columns(2)
    new_name = c_new1.text_input("新方案名称", key=f"style_profile_new_name_{camp.id}", placeholder="例：雨夜青春奇幻")
    new_id = c_new2.text_input("方案 ID", key=f"style_profile_new_id_{camp.id}", placeholder="例：rainy_youth_fantasy")

    template_paths = list_style_recipes(campaign=camp)
    template_options = ["复制当前方案"] + [p.stem for p in template_paths]
    template_choice = st.selectbox("创建方式", template_options, key=f"style_profile_template_{camp.id}")
    if st.button("创建风格方案", key=f"style_profile_create_{camp.id}", disabled=not new_name.strip()):
        if template_choice == "复制当前方案":
            new_profile = StyleProfile.from_dict(profile.to_dict())
        else:
            recipe = load_style_recipe(template_choice, campaign=camp)
            new_profile = profile_from_recipe(recipe, profile_id=new_id or new_name)
        new_profile.id = new_id or new_name
        new_profile.name = new_name.strip()
        save_style_profile(new_profile, campaign=camp)
        st.success(f"已创建：{new_profile.name}")
        st.rerun()

profile_name = st.text_input("方案名称", value=profile.name, key=f"style_profile_name_{profile.id}")

st.markdown("### ✍️ 中文风格说明")
user_brief = st.text_area(
    "你想要什么文风？（一句话或几句话描述）",
    value=profile.user_brief,
    height=110,
    placeholder="例：少年感、雨夜、克制但有爆发力；希望有史诗奇幻的距离感，但不要太古典。",
    key=f"style_profile_user_brief_{profile.id}",
)

cfg = load_kb_config(camp.style_kb_config_yaml)
kb = _get_kb(camp.id, camp.style_knowledge_base_dir, cfg)
sources = kb.list_sources()
chunk_count = kb.count_chunks()

st.caption("可以先写一句简单描述，再让模型补全下面的风格说明；保存前不会覆盖文件。")
gen_col1, gen_col2 = st.columns(2)
if gen_col1.button("根据描述生成风格方案草稿", key=f"style_profile_generate_{profile.id}", disabled=not user_brief.strip()):
    try:
        with st.status("正在生成风格方案草稿…", expanded=True) as status:
            st.write("🤖 调用大模型生成方案…")
            from trpg2novel.style import generate_style_profile_draft
            llm_cfg = load_llm_settings().draft
            draft_profile = StyleProfile.from_dict(profile.to_dict())
            draft_profile.user_brief = user_brief.strip()
            generated = generate_style_profile_draft(
                profile=draft_profile,
                user_brief=user_brief.strip(),
                style_references=[],
                model_cfg=llm_cfg,
            )
            st.session_state[_profile_state_key(profile.id)] = generated.to_dict()
            status.update(label="✓ 生成完成", state="complete")
        st.rerun()
    except Exception as exc:
        st.error(f"生成失败：{exc}")

can_use_kb_for_generation = cfg.is_configured() and chunk_count > 0
kb_button_disabled = not user_brief.strip() or not can_use_kb_for_generation
if gen_col2.button(
    "参考风格资料库重新生成",
    key=f"style_profile_generate_kb_{profile.id}",
    disabled=kb_button_disabled,
):
    try:
        with st.status("正在生成风格方案草稿…", expanded=True) as status:
            st.write("📥 检索风格资料库…")
            from trpg2novel.style import generate_style_profile_draft
            llm_cfg = load_llm_settings().draft
            query = " ".join(filter(None, [user_brief.strip(), profile.name, profile.description]))
            refs = kb.query(query, top_k=profile.style_kb_top_k)
            st.write(f"✓ 检索到 {len(refs)} 条参考片段")

            st.write("🤖 调用大模型生成方案…")
            draft_profile = StyleProfile.from_dict(profile.to_dict())
            draft_profile.user_brief = user_brief.strip()
            generated = generate_style_profile_draft(
                profile=draft_profile,
                user_brief=user_brief.strip(),
                style_references=refs,
                model_cfg=llm_cfg,
            )
            st.session_state[_profile_state_key(profile.id)] = generated.to_dict()
            status.update(label="✓ 生成完成", state="complete")
        st.rerun()
    except Exception as exc:
        st.error(f"生成失败：{exc}")

if kb_button_disabled:
    reasons = []
    if not user_brief.strip():
        reasons.append("需要填写文风描述")
    if not cfg.is_configured():
        reasons.append("需要配置 Embedding API")
    if cfg.is_configured() and chunk_count == 0:
        reasons.append("需要重建索引")
    if reasons:
        gen_col2.caption(f"💡 参考资料库生成需要：{' · '.join(reasons)}")

if sources and chunk_count == 0:
    st.info("已上传风格资料，但索引为空；请先重建索引，才能让 AI 参考资料库生成风格特点。")

style_summary = st.text_area("总体风格说明", value=profile.style_summary, height=100, key=f"style_profile_style_summary_{profile.id}")
pov_summary = st.text_area("视角与叙事策略", value=profile.pov_summary, height=90, key=f"style_profile_pov_summary_{profile.id}")
prose_summary = st.text_area("文笔与描写方式", value=profile.prose_summary, height=110, key=f"style_profile_prose_summary_{profile.id}")
c_sum1, c_sum2 = st.columns(2)
dialogue_summary = c_sum1.text_area("对白风格", value=profile.dialogue_summary, height=120, key=f"style_profile_dialogue_summary_{profile.id}")
action_summary = c_sum2.text_area("战斗 / 动作风格", value=profile.action_summary, height=120, key=f"style_profile_action_summary_{profile.id}")
avoid_summary = st.text_area("禁用项 / 不想要的风格", value=profile.avoid_summary, height=90, key=f"style_profile_avoid_summary_{profile.id}")

narrative = dict(profile.narrative)
prose_style = dict(profile.prose_style)
dialogue_style = dict(profile.dialogue_style)
combat_style = dict(profile.combat_style)
style_kb_cfg_profile = dict(profile.style_kb)

with st.expander("高级：结构化参数（通常由 AI 生成，不必手动改）", expanded=False):
    c1, c2, c3 = st.columns(3)
    narrative["pov_default"] = c1.selectbox(
        "默认视角",
        ["third_limited", "third_omniscient", "first_person"],
        index=["third_limited", "third_omniscient", "first_person"].index(narrative.get("pov_default", "third_limited"))
        if narrative.get("pov_default", "third_limited") in ["third_limited", "third_omniscient", "first_person"] else 0,
        key=f"style_profile_pov_{profile.id}",
    )
    narrative["tense"] = c2.selectbox(
        "时态",
        ["past", "present"],
        index=0 if narrative.get("tense", "past") == "past" else 1,
        key=f"style_profile_tense_{profile.id}",
    )
    narrative["pacing"] = c3.text_input("节奏", value=str(narrative.get("pacing", "dynamic_with_breathing_room")), key=f"style_profile_pacing_{profile.id}")

    c4, c5, c6 = st.columns(3)
    prose_style["sentence_length"] = c4.text_input("句长倾向", value=str(prose_style.get("sentence_length", "variable")), key=f"style_profile_sentence_{profile.id}")
    prose_style["imagery_density"] = c5.text_input("意象密度", value=str(prose_style.get("imagery_density", "medium_high")), key=f"style_profile_imagery_{profile.id}")
    prose_style["internal_monologue"] = c6.text_input("心理描写强度", value=str(prose_style.get("internal_monologue", "frequent")), key=f"style_profile_inner_{profile.id}")

    themes_text = st.text_area("主题 / 氛围关键词（每行一个）", value=_list_to_lines(profile.themes), height=110, key=f"style_profile_themes_{profile.id}")
    forbidden_text = st.text_area("禁用项关键词（每行一个）", value=_list_to_lines(profile.forbidden), height=110, key=f"style_profile_forbidden_{profile.id}")

st.markdown("### 🔗 风格参考绑定")
c_kb1, c_kb2 = st.columns(2)
style_kb_cfg_profile["enabled"] = c_kb1.checkbox("此方案默认启用风格资料库", value=bool(style_kb_cfg_profile.get("enabled", True)), key=f"style_profile_kb_enabled_{profile.id}")
style_kb_cfg_profile["top_k"] = c_kb2.number_input(
    "风格检索 top-K（0 表示用资料库默认配置）",
    value=int(style_kb_cfg_profile.get("top_k") or 0),
    min_value=0,
    max_value=20,
    key=f"style_profile_kb_topk_{profile.id}",
)
if style_kb_cfg_profile["top_k"] == 0:
    style_kb_cfg_profile["top_k"] = None

save_col, clear_col, delete_col = st.columns([1, 1, 1])
if save_col.button("💾 保存风格方案", type="primary", key=f"style_profile_save_{profile.id}"):
    updated = StyleProfile(
        id=profile.id,
        name=profile_name.strip() or profile.name,
        description=user_brief.strip(),
        user_brief=user_brief.strip(),
        style_summary=style_summary.strip(),
        pov_summary=pov_summary.strip(),
        prose_summary=prose_summary.strip(),
        dialogue_summary=dialogue_summary.strip(),
        action_summary=action_summary.strip(),
        avoid_summary=avoid_summary.strip(),
        narrative=narrative,
        prose_style=prose_style,
        themes=_lines_to_list(themes_text),
        tropes_to_embrace=profile.tropes_to_embrace,
        tropes_to_avoid=profile.tropes_to_avoid,
        dialogue_style={**dialogue_style, "summary": dialogue_summary.strip()},
        combat_style={**combat_style, "summary": action_summary.strip()},
        forbidden=_lines_to_list(forbidden_text),
        style_kb=style_kb_cfg_profile,
    )
    save_style_profile(updated, campaign=camp)
    st.session_state.pop(_profile_state_key(profile.id), None)
    st.success("风格方案已保存")
    st.rerun()

if clear_col.button("撤销未保存草稿", key=f"style_profile_clear_draft_{profile.id}"):
    st.session_state.pop(_profile_state_key(profile.id), None)
    st.rerun()

if profile.id != "default":
    if delete_col.button("🗑 删除当前方案", key=f"style_profile_delete_{profile.id}"):
        path = camp.style_profiles_dir / f"{profile.id}.yaml"
        if path.exists():
            path.unlink()
        st.session_state.pop(_profile_state_key(profile.id), None)
        st.success("已删除风格方案")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Embedding 配置
# ---------------------------------------------------------------------------

st.markdown("### ⚙️ 风格资料库 Embedding 配置")

c1, c2 = st.columns(2)
kb_api_key = c1.text_input("API Key", value=cfg.api_key, type="password", key=f"style_kb_api_{camp.id}")
kb_base_url = c2.text_input("Base URL", value=cfg.base_url, key=f"style_kb_base_url_{camp.id}")

st.markdown("**Embedding 模型**")
kb_model = model_picker_widget(
    fetch_key=f"style_kb_{camp.id}",
    model_input_key=f"style_kb_model_{camp.id}",
    current_model=cfg.model,
    api_key=kb_api_key,
    base_url=kb_base_url,
)

c4, c5 = st.columns(2)
kb_dim = c4.number_input("向量维度", value=cfg.dim, min_value=64, max_value=4096, key=f"style_kb_dim_{camp.id}")
kb_top_k = c5.number_input("默认检索 top-K", value=cfg.top_k, min_value=1, max_value=20, key=f"style_kb_top_k_{camp.id}")
c6, c7 = st.columns(2)
kb_chunk_size = c6.number_input("分块大小（字符）", value=cfg.chunk_size, min_value=100, max_value=4000, key=f"style_kb_chunk_{camp.id}")
kb_chunk_overlap = c7.number_input("分块重叠（字符）", value=cfg.chunk_overlap, min_value=0, max_value=1000, key=f"style_kb_overlap_{camp.id}")
kb_min_score = st.slider("最小相似度阈值", 0.0, 1.0, float(cfg.min_score), step=0.05, key=f"style_kb_score_{camp.id}")

if st.button("保存 Embedding 配置", type="primary", key=f"style_kb_cfg_save_{camp.id}"):
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
    save_kb_config(new_cfg, camp.style_kb_config_yaml)
    cfg = new_cfg
    st.success("已保存 style_knowledge_base/kb_config.yaml")

st.divider()

st.markdown("### 📂 此风格方案使用的参考资料")
if sources:
    total_kb = sum(s.stat().st_size for s in sources) / 1024
    st.markdown(f"当前共 **{len(sources)}** 个源文件（{total_kb:.0f} KB），索引中 **{chunk_count}** 片段")
    filter_kw = st.text_input("🔍 按文件名筛选（留空展示前 50 个）", key=f"style_kb_src_filter_{camp.id}").strip().lower()
    matched = [s for s in sources if filter_kw in s.name.lower()] if filter_kw else sources[:50]
    with st.container(height=260):
        for src in matched:
            cc1, cc2, cc3 = st.columns([4, 1, 1])
            cc1.markdown(f"`{src.name}` — {src.stat().st_size / 1024:.1f} KB")
            if cc2.button("查看", key=f"style_kb_view_{src.name}"):
                st.session_state["style_kb_view_file"] = src.name
            if cc3.button("删除", key=f"style_kb_del_{src.name}"):
                src.unlink()
                st.success(f"已删除：{src.name}")
                st.rerun()
else:
    st.info("风格资料库为空。在下方上传 .md 或 .txt 文件。")

if st.session_state.get("style_kb_view_file"):
    fname = st.session_state["style_kb_view_file"]
    view_src = kb.sources_dir / fname
    if view_src.exists():
        with st.expander(f"📄 {fname}", expanded=True):
            st.text_area("内容", value=view_src.read_text(encoding="utf-8"), height=300, disabled=True)
            if st.button("关闭", key=f"style_kb_view_close_{fname}"):
                st.session_state.pop("style_kb_view_file", None)
                st.rerun()

up_files = st.file_uploader(
    "上传风格资料 (.md / .txt，可多选)",
    type=["md", "txt"],
    accept_multiple_files=True,
    key="style_kb_upload",
)
if up_files:
    if st.button(f"保存 {len(up_files)} 个文件到 style_knowledge_base/sources/", key="style_kb_btn_save_sources"):
        kb.sources_dir.mkdir(parents=True, exist_ok=True)
        for f in up_files:
            (kb.sources_dir / f.name).write_bytes(f.getvalue())
        st.success(f"已写入 {len(up_files)} 个文件，记得点「重建索引」。")
        st.rerun()

with st.expander("📁 从本地文件夹导入（递归扫描所有 .md / .txt）", expanded=False):
    folder_path = st.text_input("文件夹路径", key="style_kb_folder_path", placeholder=r"例：D:\TRPG\style_refs")
    scan_key = f"style_kb_folder_scan_{camp.id}"
    if st.button("🔍 扫描文件夹", key="style_kb_btn_scan_folder", disabled=not folder_path.strip()):
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
        st.markdown(f"找到 **{len(found_paths)}** 个文件")
        with st.container(height=180):
            for p in found_paths:
                st.caption(p)
        if st.button(f"📥 导入全部 {len(found_paths)} 个文件", key="style_kb_btn_import_folder"):
            kb.sources_dir.mkdir(parents=True, exist_ok=True)
            imported = 0
            for p_str in found_paths:
                src = Path(p_str)
                if src.exists():
                    dest = kb.sources_dir / src.name
                    if dest.exists() and dest.resolve() != src.resolve():
                        dest = kb.sources_dir / f"{src.parent.name}__{src.name}"
                    dest.write_bytes(src.read_bytes())
                    imported += 1
            st.session_state.pop(scan_key, None)
            st.success(f"已导入 {imported} 个文件")
            st.rerun()

st.divider()

st.markdown("### 🔨 重建索引")
rb_state = _rebuild_states.get(camp.id, {})
is_running = rb_state.get("running", False)

if not cfg.is_configured():
    st.warning("请先填写 Embedding API Key 并保存配置。")
elif not sources:
    st.info("没有风格源文件可索引。")
else:
    if not is_running:
        st.caption("会清空当前索引并对所有风格源文件重新 embed，耗时取决于文件量和 API 速度。切换页面不影响进度。")
        if st.button(
            "🔁 重建风格资料库索引",
            type="primary",
            key=f"style_kb_rebuild_{camp.id}",
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
            threading.Thread(target=_rebuild_worker, args=(kb, camp.id), daemon=True).start()
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
        current = int(rb.get("current") or 0)
        total = int(rb.get("total") or 0)
        pct = (current / total) if total > 0 else 0.0
        st.progress(pct)
        if total > 0:
            st.caption(f"⏳ {stage}  {current} / {total} 片段（每 1.5 秒自动刷新，可自由切换页面）")
        else:
            st.caption(f"⏳ {stage}（每 1.5 秒自动刷新，可自由切换页面）")
    elif error:
        st.error(f"重建失败：{error}")
        if st.button("清除错误", key=f"style_kb_clear_err_{camp_id}"):
            _rebuild_states.pop(camp_id, None)
            st.rerun()
    elif result:
        st.success(
            f"✅ 风格资料库索引重建完成：{result.get('sources', '?')} 源文件 / {result.get('chunks', '?')} 片段"
        )
        if st.button("清除提示", key=f"style_kb_clear_ok_{camp_id}"):
            _rebuild_states.pop(camp_id, None)
            st.rerun()


if camp.id in _rebuild_states:
    _show_rebuild_progress(camp.id)

st.divider()

st.markdown("### 🔎 检索预览")
q = st.text_input("输入情绪、主题或场景关键词", key="style_kb_query")
if st.button("检索", disabled=not q.strip() or not cfg.is_configured()):
    try:
        for r in kb.query(q.strip()):
            with st.expander(f"{r.source} · score={r.score:.3f}"):
                st.write(r.text)
    except Exception as exc:
        st.error(f"检索失败：{exc}")

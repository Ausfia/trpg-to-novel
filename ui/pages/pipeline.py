"""✍️ Pipeline — 上传日志 / 切分 / parse / classify / segment。

1. 上传日志 + 场次切分
2. 选择场次 + 跑 parse/classify/segment
3. 新写作流程入口（大纲规划页）；旧版单章流程保留过渡
"""

from __future__ import annotations

import json
import re

import streamlit as st
import yaml

from ui.shared import (
    list_raw_sessions,
    list_sessions,
    load_scenes,
    load_state,
    read_env,
    require_campaign,
    run_cmd,
    stage_value,
)

st.title("✍️ Pipeline 控制台")
st.caption("跑团原始日志 → 切分场次 → 解析事件 → 划分场景；后续卷规划、细节粗稿与切章在大纲规划页完成。")

camp = require_campaign()
if camp is None:
    st.stop()


# ---------------------------------------------------------------------------
# 第一步：上传日志 + 自动场次切分
# ---------------------------------------------------------------------------

with st.expander("📤 第一步：上传日志 + 自动场次切分", expanded=True):
    from trpg2novel.parse.session_splitter import split_by_time_gap

    uploaded = st.file_uploader(
        "上传 .md 日志文件",
        type=["md"],
        help="单场或多场融合的 .md 都可。上传后下方会预览自动场次切分。",
        key="pipe_upload",
    )

    if uploaded:
        try:
            text = uploaded.getvalue().decode("utf-8")
        except UnicodeDecodeError:
            text = uploaded.getvalue().decode("utf-8", errors="replace")

        cfg_col, _ = st.columns([1, 2])
        with cfg_col:
            gap_hours = st.number_input(
                "场次切分阈值（小时）",
                min_value=0.5,
                max_value=24.0,
                value=8.0,
                step=0.5,
                key="split_gap",
                help="相邻两条日志时间差 ≥ 此值视为换场。跨夜自动 +24h 计算。",
            )

        chunks = split_by_time_gap(text, min_gap_hours=float(gap_hours))

        if len(chunks) == 0:
            st.warning("未检测到任何带时间戳的事件，请确认日志格式。")
        elif len(chunks) == 1:
            st.info(
                f"检测到单场跑团（{chunks[0].start_ts} → {chunks[0].end_ts}，{chunks[0].line_count} 行）。"
            )
        else:
            st.success(f"自动识别到 **{len(chunks)} 场** 跑团：")
            for c in chunks:
                st.markdown(
                    f"- chunk {c.index + 1}：`{c.start_ts}` → `{c.end_ts}`（{c.line_count} 行）"
                )

        existing_sids_for_upload = [p.stem for p in sorted(camp.raw_logs_dir.glob("*.md"))]
        write_mode = st.radio(
            "写入方式",
            ["追加（新场次）", "覆盖已有场次"],
            horizontal=True,
            key="upload_write_mode",
            help="覆盖模式：所选场次的 parse/classify/segment 派生文件将一并清除。",
        )

        overwrite_start_num: int | None = None
        if write_mode == "覆盖已有场次":
            if not existing_sids_for_upload:
                st.info("暂无已有场次，将自动追加。")
            else:
                ow_sid = st.selectbox(
                    "从哪个场次开始覆盖", existing_sids_for_upload, key="upload_overwrite_sid"
                )
                m_ow = re.search(r"\d+", ow_sid)
                overwrite_start_num = int(m_ow.group()) if m_ow else 1
                if len(chunks) > 1:
                    preview = ", ".join(
                        f"s{overwrite_start_num + i:02d}" for i in range(len(chunks))
                    )
                    st.caption(f"将覆盖：{preview}，并清除对应派生文件")
                else:
                    st.caption(f"将覆盖：s{overwrite_start_num:02d}，并清除其派生文件")

        start_n = len(camp.list_raw_logs()) + 1
        is_overwrite = write_mode == "覆盖已有场次" and overwrite_start_num is not None
        effective_start = overwrite_start_num if is_overwrite else start_n

        def _clear_derived(sid: str) -> None:
            for suf in (".events.json", ".tagged.json", ".scenes.json"):
                p = camp.parsed_dir / f"{sid}{suf}"
                if p.exists():
                    p.unlink()

        def _write_session_yaml(sid: str) -> None:
            yaml_path = camp.raw_logs_dir / f"{sid}.yaml"
            if yaml_path.exists():
                return
            try:
                from trpg2novel.session_loader import load_players as _lp
                pc = _lp(camp.players_yaml)
                data = {
                    "session_id": sid,
                    "dm_handle": pc.dm_handle,
                    "bot_handles": pc.known_bots,
                    "absent_players": [],
                }
            except Exception:
                data = {"session_id": sid, "dm_handle": "", "bot_handles": [], "absent_players": []}
            yaml_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )

        save_cols = st.columns(2)
        with save_cols[0]:
            if len(chunks) > 1:
                end_num = effective_start + len(chunks) - 1
                label = f"{'覆盖' if is_overwrite else '追加'}：s{effective_start:02d} ~ s{end_num:02d}"
            else:
                label = f"{'覆盖写入' if is_overwrite else '写入'} s{effective_start:02d}.md"
            if st.button(label, width="stretch", key="btn_accept_split", type="primary"):
                camp.raw_logs_dir.mkdir(parents=True, exist_ok=True)
                written = []
                for c in chunks:
                    sid = f"s{effective_start + c.index:02d}"
                    (camp.raw_logs_dir / f"{sid}.md").write_text(c.text, encoding="utf-8")
                    _write_session_yaml(sid)
                    written.append(f"{sid}.md")
                    if is_overwrite:
                        _clear_derived(sid)
                st.success(f"已{'覆盖' if is_overwrite else '写入'} {len(written)} 份：{', '.join(written)}")
                st.cache_data.clear()

        with save_cols[1]:
            if len(chunks) > 1:
                label2 = f"忽略切分：整份{'覆盖' if is_overwrite else '写入'} s{effective_start:02d}.md"
                if st.button(label2, width="stretch", key="btn_force_single"):
                    camp.raw_logs_dir.mkdir(parents=True, exist_ok=True)
                    sid = f"s{effective_start:02d}"
                    (camp.raw_logs_dir / f"{sid}.md").write_text(text, encoding="utf-8")
                    _write_session_yaml(sid)
                    if is_overwrite:
                        _clear_derived(sid)
                    st.success(f"已{'覆盖' if is_overwrite else ''}写入：{sid}.md")
                    st.cache_data.clear()


# ---------------------------------------------------------------------------
# 第二步：选择场次 + parse / classify / segment
# ---------------------------------------------------------------------------

raw_sids = list_raw_sessions(camp)

with st.expander("⚙️ 第二步：选择场次并跑解析阶段", expanded=True):
    if not raw_sids:
        st.warning(f"`{camp.id}/raw_logs/` 下暂无 .md 文件，请先在第一步上传。")
    else:
        sel_col, del_col = st.columns([4, 1])
        with sel_col:
            selected_sid = st.selectbox("选择场次", raw_sids, key="pipe_sel_sid")
        with del_col:
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            if st.button(
                "🗑 删除",
                key="btn_del_session_init",
                width="stretch",
                help="删除原始日志及全部派生文件（events / tagged / scenes）",
            ):
                st.session_state["del_confirm_sid"] = selected_sid

        if st.session_state.get("del_confirm_sid") == selected_sid:
            st.warning(
                f"⚠ 即将永久删除 `{selected_sid}` 的原始日志及其 events / tagged / scenes 文件，不可撤销。"
            )
            cc1, cc2, _ = st.columns([1, 1, 3])
            if cc1.button("确认删除", type="primary", key="btn_del_confirm"):
                deleted = []
                md_path = camp.raw_logs_dir / f"{selected_sid}.md"
                if md_path.exists():
                    md_path.unlink()
                    deleted.append(md_path.name)
                for suf in (".events.json", ".tagged.json", ".scenes.json"):
                    p = camp.parsed_dir / f"{selected_sid}{suf}"
                    if p.exists():
                        p.unlink()
                        deleted.append(p.name)
                st.session_state.pop("del_confirm_sid", None)
                st.cache_data.clear()
                st.success(f"已删除：{', '.join(deleted) if deleted else '（无相关文件）'}")
                st.rerun()
            if cc2.button("取消", key="btn_del_cancel"):
                st.session_state.pop("del_confirm_sid", None)
                st.rerun()

        # 阶段产物状态指示
        events_p = camp.parsed_dir / f"{selected_sid}.events.json"
        tagged_p = camp.parsed_dir / f"{selected_sid}.tagged.json"
        scenes_p = camp.parsed_dir / f"{selected_sid}.scenes.json"
        status_bits = []
        status_bits.append("✅ parse" if events_p.exists() else "⏳ parse")
        status_bits.append("✅ classify" if tagged_p.exists() else "⏳ classify")
        status_bits.append("✅ segment" if scenes_p.exists() else "⏳ segment")
        st.caption(" · ".join(status_bits))

        # ---- 参与情况分析 ----
        _absent_key = f"absent_ui_{camp.id}_{selected_sid}"
        _absent_loaded_key = f"absent_ui_loaded_{camp.id}_{selected_sid}"

        def _read_session_yaml(sid: str) -> dict:
            p = camp.raw_logs_dir / f"{sid}.yaml"
            if p.exists():
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return {}

        def _analyze_participation(sid: str):
            import re as _re
            HDER = _re.compile(r"^(\d{2}:\d{2}:\d{2})\s+\\<(.+?)\\>:\s*(.*)$")
            md_path = camp.raw_logs_dir / f"{sid}.md"
            handle_counts: dict[str, int] = {}
            if md_path.exists():
                for line in md_path.read_text(encoding="utf-8").splitlines():
                    m = HDER.match(line)
                    if m:
                        h = m.group(2).strip()
                        handle_counts[h] = handle_counts.get(h, 0) + 1

            # 从人物卡读取所有角色
            try:
                from trpg2novel.character import load_all_cards
                cards = load_all_cards(camp.character_cards_dir)
            except Exception:
                return [], []

            # 获取 DM 和 bot 列表（用于排除未知发言人）
            try:
                from trpg2novel.session_loader import load_players as _lp
                pcfg = _lp(camp.players_yaml)
                dm_lower = {pcfg.dm_handle.lower()} if pcfg.dm_handle else set()
                bot_lower = {b.lower() for b in (pcfg.known_bots or [])}
            except Exception:
                dm_lower = set()
                bot_lower = set()

            pc_rows = []
            known_all_lower: set[str] = set(dm_lower) | set(bot_lower)

            # 遍历所有人物卡
            for card in cards.values():
                # 构建该角色的所有可能昵称（name + aliases）
                handles = {card.name.lower()} | {a.lower() for a in (card.aliases or [])}
                known_all_lower |= handles
                # 统计该角色的发言数
                count = sum(v for k, v in handle_counts.items() if k.lower() in handles)
                pc_rows.append({"name": card.name, "count": count})

            unknown = [
                {"handle": h, "count": c}
                for h, c in handle_counts.items()
                if h.lower() not in known_all_lower
            ]
            return pc_rows, unknown

        if not st.session_state.get(_absent_loaded_key):
            _sess_data = _read_session_yaml(selected_sid)
            _existing_absent: list[str] = _sess_data.get("absent_players") or []
            _existing_not_joined: list[str] = _sess_data.get("not_joined_players") or []
            _pc_rows, _unknown = _analyze_participation(selected_sid)

            # 从人物卡读取入场/退团时间用于自动判定
            _sid_num = int(selected_sid.lstrip("s")) if selected_sid.startswith("s") else 0
            _card_first: dict[str, int] = {}   # name -> first_appearance 场次数
            _card_retired: dict[str, int] = {}  # name -> left_after 场次数
            try:
                from trpg2novel.character import load_all_cards as _lac2
                _cards2 = _lac2(camp.character_cards_dir)
                for _cn, _cc in _cards2.items():
                    if _cc.first_appearance_session:
                        _n = int(_cc.first_appearance_session.lstrip("s")) if _cc.first_appearance_session.startswith("s") else 0
                        if _n > 0:
                            _card_first[_cn] = _n
                    if _cc.left_after_session:
                        _n = int(_cc.left_after_session.lstrip("s")) if _cc.left_after_session.startswith("s") else 0
                        if _n > 0:
                            _card_retired[_cn] = _n
            except Exception:
                pass

            ui_state: dict = {"pc_rows": _pc_rows, "unknown": _unknown}
            for row in _pc_rows:
                nm = row["name"]
                # 根据人物卡的首入场次和退团场次 + 发言数 + 已保存状态判断默认状态
                _first_app = _card_first.get(nm)
                _retired_at = _card_retired.get(nm)
                if any(nm in a for a in _existing_not_joined):
                    default_status = "未入场"
                elif _retired_at is not None and _sid_num > 0 and _sid_num > _retired_at:
                    default_status = "永久退团"
                elif any(nm in a for a in _existing_absent):
                    default_status = "本场缺席（加戏）"
                elif _first_app is not None and _sid_num > 0 and _sid_num < _first_app:
                    default_status = "未入场"
                elif row["count"] == 0:
                    default_status = "未入场"
                else:
                    default_status = "正常参与"
                ui_state[f"status_{nm}"] = default_status
                # 提取缺席原因
                saved_reason = next(
                    (a[len(nm):].strip("（）()") for a in _existing_absent
                     if a.startswith(nm) and len(a) > len(nm)),
                    "",
                )
                ui_state[f"reason_{nm}"] = saved_reason
            st.session_state[_absent_key] = ui_state
            st.session_state[_absent_loaded_key] = True

        _ui: dict = st.session_state.get(_absent_key, {})
        _pc_rows: list = _ui.get("pc_rows", [])
        _unknown: list = _ui.get("unknown", [])

        with st.expander("👥 本场参与情况", expanded=bool(_pc_rows or _unknown)):
            if not _pc_rows:
                st.info("暂无人物卡，请先在「🎭 人物卡管理」页添加角色。")
            else:
                st.markdown("**所有角色的参与状态**")
                status_opts = ["正常参与", "本场缺席（加戏）", "未入场", "永久退团"]
                for row in _pc_rows:
                    nm = row["name"]
                    cnt = row["count"]
                    s_key = f"abs_s_{camp.id}_{selected_sid}_{nm}"
                    r_key = f"abs_r_{camp.id}_{selected_sid}_{nm}"
                    cur_status = _ui.get(f"status_{nm}", "正常参与")
                    cur_reason = _ui.get(f"reason_{nm}", "")
                    c1, c2, c3 = st.columns([2, 3, 4])
                    c1.markdown(
                        f"**{nm}**  <span style='color:var(--color-text-soft);font-size:12px'>"
                        f"发言 {cnt} 条</span>",
                        unsafe_allow_html=True,
                    )
                    new_status = c2.selectbox(
                        f"state_{nm}",
                        status_opts,
                        index=status_opts.index(cur_status) if cur_status in status_opts else 0,
                        key=s_key,
                        label_visibility="collapsed",
                    )
                    _ui[f"status_{nm}"] = new_status
                    if new_status == "本场缺席（加戏）":
                        new_reason = c3.text_input(
                            f"reason_{nm}",
                            value=cur_reason,
                            key=r_key,
                            placeholder="可选备注，如：在神殿闭关",
                            label_visibility="collapsed",
                        )
                        _ui[f"reason_{nm}"] = new_reason
                    elif new_status == "未入场":
                        c3.caption("该角色尚未加入团队，classify 时不会识别为 PC。")
                    elif new_status == "永久退团":
                        c3.caption("请在「人物卡」页填写退场方向，本次标注不影响 Draft。")

            if _unknown:
                st.divider()
                st.markdown("**未登记发言人（可能为新玩家）**")
                for u in _unknown:
                    st.warning(
                        f"⚠ 未登记发言人：**{u['handle']}**（{u['count']} 条）"
                        " — 若为新玩家，请在「人物卡」页添加角色后重新分析。"
                    )

            if _pc_rows:
                st.divider()
                sc1, sc2 = st.columns([2, 4])
                if sc1.button(
                    "💾 保存本场标注",
                    key=f"btn_save_absent_{camp.id}_{selected_sid}",
                    type="primary",
                    width="stretch",
                ):
                    absent_list = []
                    not_joined_list = []
                    player_handles = []
                    for row in _pc_rows:
                        nm = row["name"]
                        status = _ui.get(f"status_{nm}", "正常参与")
                        if status == "本场缺席（加戏）":
                            reason = (_ui.get(f"reason_{nm}") or "").strip()
                            absent_list.append(f"{nm}（{reason}）" if reason else nm)
                            player_handles.append(nm)  # 缺席但仍在团队中
                        elif status == "未入场":
                            not_joined_list.append(nm)
                        elif status == "正常参与":
                            player_handles.append(nm)
                        # "永久退团" 不加入任何列表

                    _sess_data = _read_session_yaml(selected_sid)
                    _sess_data["absent_players"] = absent_list
                    _sess_data["not_joined_players"] = not_joined_list
                    _sess_data["player_handles"] = player_handles
                    (camp.raw_logs_dir / f"{selected_sid}.yaml").write_text(
                        yaml.safe_dump(_sess_data, allow_unicode=True, sort_keys=False),
                        encoding="utf-8",
                    )
                    sc2.success(
                        f"已保存：{len(player_handles)} 位参与（含 {len(absent_list)} 位缺席），"
                        f"{len(not_joined_list)} 位未入场"
                    )
                if sc1.button(
                    "↺ 重新分析",
                    key=f"btn_reanalyze_{camp.id}_{selected_sid}",
                    width="stretch",
                ):
                    st.session_state[_absent_loaded_key] = False
                    st.rerun()

        out_ph = st.empty()
        btn_col1, btn_col2, btn_col3 = st.columns(3)

        with btn_col1:
            if st.button("▶ 解析 (Parse)", width="stretch", key="btn_parse"):
                with st.spinner("解析中…"):
                    code, _ = run_cmd(
                        ["parse", str(camp.raw_logs_dir / f"{selected_sid}.md"),
                         "--session-id", selected_sid],
                        out_ph, camp.id,
                    )
                if code == 0:
                    st.success("Parse 完成")
                    st.cache_data.clear()
                else:
                    st.error("Parse 失败，见上方输出")

        with btn_col2:
            if st.button("▶ 分类 (Classify)", width="stretch", key="btn_classify"):
                with st.spinner("分类配对中…"):
                    code, _ = run_cmd(["classify", selected_sid], out_ph, camp.id)
                if code == 0:
                    st.success("Classify 完成")
                    st.cache_data.clear()
                else:
                    st.error("Classify 失败")

        with btn_col3:
            if st.button("▶ 切分场景 (Segment)", width="stretch", key="btn_segment"):
                with st.spinner("切分场景中…"):
                    code, _ = run_cmd(["segment", selected_sid], out_ph, camp.id)
                if code == 0:
                    st.success("Segment 完成")
                    st.cache_data.clear()
                else:
                    st.error("Segment 失败")


# ---------------------------------------------------------------------------
# 第三步：进入大纲规划（新流程主入口）
# ---------------------------------------------------------------------------

from trpg2novel.outline.lifecycle import classify_scenes, remaining_scene_ids_for_next_volume
from trpg2novel.state.story_state import load_state as load_story_state_obj

st.markdown("### 🧭 第三步：进入大纲规划")

sessions_with_scenes = list_sessions(camp)
if not sessions_with_scenes:
    st.info("请先完成至少一个场次的 Segment 阶段。")
    st.stop()

# 加载场景概览
state = load_story_state_obj(camp.story_state_yaml)
all_scene_ids: list[str] = []
for sid in sessions_with_scenes:
    for sc in load_scenes(str(camp.parsed_dir), sid):
        all_scene_ids.append(sc["id"])
total_scenes = len(all_scene_ids)
classification = classify_scenes(all_scene_ids, state)
remaining_ids = remaining_scene_ids_for_next_volume(all_scene_ids, state)
pending_pool_size = len(state.pending_pool.scene_ids) if state.pending_pool else 0

# 统计条
stat_col1, stat_col2, stat_col3, stat_col4, stat_col5 = st.columns(5)
stat_col1.metric("总场景", str(total_scenes))
stat_col2.metric("待提议", str(len(remaining_ids)))
stat_col3.metric("卷记录", str(len(state.volumes)))
stat_col4.metric("Pending 池", str(pending_pool_size))
stat_col5.metric("已入章(旧)", str(len(classification.get("processed", []))))

# 可用场次
with st.expander("📋 可用场次", expanded=False):
    for sid in sessions_with_scenes:
        scenes = load_scenes(str(camp.parsed_dir), sid)
        st.markdown(f"- **{sid}**：{len(scenes)} 个场景")

if not all_scene_ids:
    st.info("还没有可用于规划的 scene。请先完成 parse / classify / segment。")
else:
    st.info("卷规划、长期大纲修订、生成卷级细节粗稿和切分章节草稿现在统一在「大纲规划」页完成。")
    st.page_link("pages/outline.py", label="前往大纲规划页继续写作生产")

# ====== 旧版流程（过渡期保留） ======
st.divider()
st.caption("—— 以下为旧版单章检测+直接起草（过渡期保留，新场景请用上方流程）——")

env = read_env()
api_ok = bool(stage_value(env, "draft", "api_key").strip())

with st.expander("🕐 旧版：detect → draft（单章）", expanded=False):
    state_dict = load_state(str(camp.root))
    processed_ids = set(state_dict.get("processed_scene_ids") or [])
    chapter_index = state_dict.get("chapter_index") or []
    pending_scenes = 0
    for sid in sessions_with_scenes:
        for sc in load_scenes(str(camp.parsed_dir), sid):
            if sc.get("id") not in processed_ids:
                pending_scenes += 1

    if pending_scenes == 0:
        st.info("所有场景均已入章（旧版记录）。")
    else:
        if not api_ok:
            st.warning("需要先在「⚙️ LLM 配置」配置「章节起草」的 API Key。")
            st.stop()

        last_marker = ""
        if chapter_index:
            last_marker = chapter_index[-1].get("ending_marker", "") or chapter_index[-1].get("last_summary", "")

        ls_val = st.text_area("上一章结尾摘要", height=60, key="legacy_last_summary", placeholder=last_marker or "")

        c1, c2 = st.columns(2)
        if c1.button("▶ 生成下一章", key="legacy_draft"):
            args = ["draft", "legacy"]
            if ls_val.strip():
                args += ["--last-summary", ls_val.strip()]
            ph = st.empty()
            with st.spinner("生成中…"):
                code, output = run_cmd(args, ph, camp.id)
            if code == 0:
                st.success("章节草稿已生成")
                st.cache_data.clear()
            else:
                st.error("Draft 失败")

        if c2.button("⚡ 强制生成", key="legacy_force"):
            args = ["draft", "legacy", "--force"]
            if ls_val.strip():
                args += ["--last-summary", ls_val.strip()]
            ph = st.empty()
            with st.spinner("强制生成中…"):
                code, output = run_cmd(args, ph, camp.id)
            if code == 0:
                st.success("章节草稿已生成")
                st.cache_data.clear()
            else:
                st.error("Draft 失败")

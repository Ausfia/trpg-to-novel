"""✍️ Pipeline — 上传日志 / 切分 / parse / classify / segment / draft。

完整迁移自旧 app.py 的 `_tab_pipeline`，分三个折叠步骤呈现：
1. 上传日志 + 场次切分
2. 选择场次 + 跑 parse/classify/segment
3. 多场次生成章节草稿（含"继续下一章"）
"""

from __future__ import annotations

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
st.caption("跑团原始日志 → 切分场次 → 解析事件 → 划分场景 → 生成章节草稿。")

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
            if st.button(label, use_container_width=True, key="btn_accept_split", type="primary"):
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
                if st.button(label2, use_container_width=True, key="btn_force_single"):
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
                use_container_width=True,
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
            try:
                from trpg2novel.session_loader import load_players as _lp
                pcfg = _lp(camp.players_yaml)
            except Exception:
                return [], []
            dm_lower = {pcfg.dm_handle.lower()} if pcfg.dm_handle else set()
            bot_lower = {b.lower() for b in (pcfg.known_bots or [])}
            pc_rows = []
            known_all_lower: set[str] = set(dm_lower) | set(bot_lower)
            for p in pcfg.players:
                if p.role != "pc":
                    continue
                ph = {(p.name or "").lower()} | {a.lower() for a in (p.aliases or [])}
                known_all_lower |= ph
                count = sum(v for k, v in handle_counts.items() if k.lower() in ph)
                pc_rows.append({"name": p.name, "count": count})
            unknown = [
                {"handle": h, "count": c}
                for h, c in handle_counts.items()
                if h.lower() not in known_all_lower
            ]
            return pc_rows, unknown

        if not st.session_state.get(_absent_loaded_key):
            _sess_data = _read_session_yaml(selected_sid)
            _existing_absent: list[str] = _sess_data.get("absent_players") or []
            _pc_rows, _unknown = _analyze_participation(selected_sid)
            ui_state: dict = {"pc_rows": _pc_rows, "unknown": _unknown}
            for row in _pc_rows:
                nm = row["name"]
                default_status = "本场缺席（加戏）" if row["count"] == 0 else "正常参与"
                if any(nm in a for a in _existing_absent):
                    default_status = "本场缺席（加戏）"
                ui_state[f"status_{nm}"] = default_status
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
                st.info("players.yaml 未配置或未找到 PC，请先在「🏛️ 团管理」配置人员信息。")
            else:
                st.markdown("**已登记 PC 的参与状态**")
                status_opts = ["正常参与", "本场缺席（加戏）", "永久退团"]
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
                    use_container_width=True,
                ):
                    absent_list = []
                    for row in _pc_rows:
                        nm = row["name"]
                        status = _ui.get(f"status_{nm}", "正常参与")
                        if status == "本场缺席（加戏）":
                            reason = (_ui.get(f"reason_{nm}") or "").strip()
                            absent_list.append(f"{nm}（{reason}）" if reason else nm)
                    _sess_data = _read_session_yaml(selected_sid)
                    _sess_data["absent_players"] = absent_list
                    (camp.raw_logs_dir / f"{selected_sid}.yaml").write_text(
                        yaml.safe_dump(_sess_data, allow_unicode=True, sort_keys=False),
                        encoding="utf-8",
                    )
                    sc2.success(f"已保存：{len(absent_list)} 位角色标为缺席")
                if sc1.button(
                    "↺ 重新分析",
                    key=f"btn_reanalyze_{camp.id}_{selected_sid}",
                    use_container_width=True,
                ):
                    st.session_state[_absent_loaded_key] = False
                    st.rerun()

        out_ph = st.empty()
        btn_col1, btn_col2, btn_col3 = st.columns(3)

        with btn_col1:
            if st.button("▶ 解析 (Parse)", use_container_width=True, key="btn_parse"):
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
            if st.button("▶ 分类 (Classify)", use_container_width=True, key="btn_classify"):
                with st.spinner("分类配对中…"):
                    code, _ = run_cmd(["classify", selected_sid], out_ph, camp.id)
                if code == 0:
                    st.success("Classify 完成")
                    st.cache_data.clear()
                else:
                    st.error("Classify 失败")

        with btn_col3:
            if st.button("▶ 切分场景 (Segment)", use_container_width=True, key="btn_segment"):
                with st.spinner("切分场景中…"):
                    code, _ = run_cmd(["segment", selected_sid], out_ph, camp.id)
                if code == 0:
                    st.success("Segment 完成")
                    st.cache_data.clear()
                else:
                    st.error("Segment 失败")


# ---------------------------------------------------------------------------
# 第三步：生成章节草稿
# ---------------------------------------------------------------------------

st.markdown("### 📖 第三步：生成章节草稿")

sessions_with_scenes = list_sessions(camp)
if not sessions_with_scenes:
    st.info("请先完成至少一个场次的 Segment 阶段。")
    st.stop()

state_yaml = load_state(str(camp.root))
processed_ids = set(state_yaml.get("processed_scene_ids") or [])
chapter_index = state_yaml.get("chapter_index") or []
total_scenes = 0
pending_scenes = 0
for sid in sessions_with_scenes:
    scenes = load_scenes(str(camp.parsed_dir), sid)
    total_scenes += len(scenes)
    for sc in scenes:
        if sc.get("id") not in processed_ids:
            pending_scenes += 1

# 入章统计条
stat_col1, stat_col2, stat_col3 = st.columns(3)
stat_col1.metric("已生成章节", f"{len(chapter_index)} 章")
stat_col2.metric("已入章场景", f"{len(processed_ids)} / {total_scenes}")
stat_col3.metric("待处理场景", f"{pending_scenes}")

draft_sessions = st.multiselect(
    "纳入本章的场次（按顺序选）",
    sessions_with_scenes,
    default=sessions_with_scenes,
    key="draft_sids",
)
draft_mode = st.radio(
    "生成模式",
    ["auto-detect（自动判断章节边界）", "force（强制生成）"],
    key="draft_mode",
    horizontal=True,
)
last_summary = st.text_area(
    "上一章结尾摘要（可选，留空时自动用上一章 last_summary）",
    height=80,
    key="last_summary",
)

env = read_env()
if not stage_value(env, "draft", "api_key").strip():
    st.warning("需要先在「⚙️ LLM 配置」配置「章节起草」阶段的 API Key 才能运行 Draft。")
    draft_disabled = True
elif not draft_sessions:
    st.warning("请选择至少一个场次。")
    draft_disabled = True
elif pending_scenes == 0:
    st.info("所有场景均已入章。如需重新生成，请去「📖 章节审稿」删除对应章节。")
    draft_disabled = True
else:
    draft_disabled = False

btn_col1, btn_col2 = st.columns([3, 2])
run_draft = btn_col1.button(
    "▶ 生成章节草稿 (Draft)",
    disabled=draft_disabled,
    use_container_width=True,
    key="btn_draft",
    type="primary",
)
cont_disabled = draft_disabled or pending_scenes == 0
run_continue = btn_col2.button(
    "↻ 继续生成下一章",
    disabled=cont_disabled,
    use_container_width=True,
    key="btn_draft_continue",
    help="自动跳过已入章场景；若还有未入章场景就再起一章",
)

if run_draft or run_continue:
    args = ["draft"]
    for s in draft_sessions:
        args += ["--session", s]
    if "force" in draft_mode:
        args.append("--force")
    if last_summary.strip():
        args += ["--last-summary", last_summary.strip()]

    draft_ph = st.empty()
    with st.spinner("正在调用 LLM 生成章节，请稍候（可能需要 1–3 分钟）…"):
        code, output = run_cmd(args, draft_ph, camp.id)
    if code == 0:
        m = re.search(r"本次入章\s*(\d+)\s*场[，,]\s*剩余\s*(\d+)\s*场未处理", output)
        if m:
            used, remaining = int(m.group(1)), int(m.group(2))
            st.success(f"✓ 本次入章 {used} 场，剩余 {remaining} 场未处理")
            if remaining > 0:
                st.info("还有未入章场景，可点「↻ 继续生成下一章」继续。")
        elif "已全部入章" in output:
            st.info("所选场次的场景已全部入章。")
        else:
            st.success("章节草稿已生成！切换到「📖 章节审稿」查看。")
        st.cache_data.clear()
    else:
        st.error("Draft 失败，见上方输出")

"""🧭 大纲规划 — 长期大纲修订、卷方案草案与确认。"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import shutil

import streamlit as st

from trpg2novel.outline.io import (
    campaign_outline_yaml,
    load_campaign_outline,
    load_volume_outline,
    save_campaign_outline,
    volume_yaml_path,
)
from trpg2novel.outline.lifecycle import classify_scenes, remaining_scene_ids_for_next_volume
from trpg2novel.outline.scene_summary import load_summary_cache
from trpg2novel.narrate.skeleton import load_manifest, manifest_path, scene_draft_path
from trpg2novel.state import load_state as load_story_state, save_state as save_story_state
from ui.jobs import chapterize_progress, job_elapsed, output_tail, snapshot_job, start_pipeline_job
from ui.shared import badge, list_sessions, load_scenes, read_env, require_campaign, run_cmd, stage_value


STATUS_LABELS = {
    "processed": ("已锁定", "#888"),
    "proposed": ("待确认", "#2f80ed"),
    "draft": ("已入卷", "#2b7"),
    "confirmed": ("已锁定", "#888"),
    "drafting": ("已锁定", "#888"),
    "closed": ("已锁定", "#888"),
    "pending": ("pending", "#b98b00"),
    "unproposed": ("未规划", "#666"),
}


STATUS_TITLES = {
    "proposed": "待确认",
    "draft": "待确认",
    "confirmed": "已确认",
    "drafting": "卷级细节粗稿已生成",
    "closed": "章节草稿已生成",
}


def _clear_revision_state() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith("rev_"):
            del st.session_state[key]


def _read_text_preview(path: Path, *, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n...（已截断，完整文件：{path}）"


def _render_artifact_viewer(label: str, path: Path, *, language: str = "yaml", expanded: bool = False) -> None:
    if not path.exists():
        st.caption(f"{label}：尚未生成")
        return
    with st.expander(f"查看 {label} · {path.name}", expanded=expanded):
        st.caption(str(path))
        st.code(_read_text_preview(path), language=language)


def _delete_path(path: Path) -> None:
    if path.exists() and path.is_file():
        path.unlink()


def _delete_tree(path: Path) -> None:
    if path.exists() and path.is_dir():
        shutil.rmtree(path)


def _rollback_volume_to_draft(camp, rec) -> None:
    state = load_story_state(camp.story_state_yaml)
    target = next((v for v in state.volumes if v.volume_index == rec.volume_index), None)
    if target is None:
        return
    for sid in list(target.scene_ids):
        if sid in state.processed_scene_ids:
            state.processed_scene_ids.remove(sid)
    target.status = "proposed"
    target.confirmed_at = None
    target.skeleton_path = None
    target.chapter_indices = []
    target.word_count = None
    target.closed_at = None
    target.outline_path = str(volume_yaml_path(camp, rec.volume_index, draft=True))
    final_outline = volume_yaml_path(camp, rec.volume_index, draft=False)
    _delete_path(final_outline)
    draft_outline = load_volume_outline(camp, rec.volume_index, prefer_draft=True)
    if draft_outline is not None:
        draft_outline.status = "proposed"
        draft_outline.user_confirmed = False
        draft_outline.confirmed_at = None
        draft_outline.closed_at = None
        save_volume_outline(camp, draft_outline, as_draft=True, snapshot=True)
    save_story_state(state, camp.story_state_yaml)


def _rollback_volume_to_confirmed(camp, rec) -> None:
    state = load_story_state(camp.story_state_yaml)
    target = next((v for v in state.volumes if v.volume_index == rec.volume_index), None)
    if target is None:
        return
    _delete_path(camp.chapters_dir / f"vol{rec.volume_index:02d}_skeleton.md")
    _delete_path(manifest_path(camp.chapters_dir, rec.volume_index))
    _delete_tree(camp.chapters_dir / f"vol{rec.volume_index:02d}_scene_drafts")
    target.status = "confirmed"
    target.skeleton_path = None
    target.chapter_indices = []
    target.word_count = None
    target.closed_at = None
    save_story_state(state, camp.story_state_yaml)


def _rollback_volume_to_drafting(camp, rec) -> None:
    state = load_story_state(camp.story_state_yaml)
    target = next((v for v in state.volumes if v.volume_index == rec.volume_index), None)
    if target is None:
        return
    for idx in target.chapter_indices or []:
        _delete_path(camp.chapters_dir / f"ch{idx:02d}_draft.md")
        _delete_path(camp.chapters_dir / f"ch{idx:02d}_revised.md")
        _delete_path(camp.chapters_dir / f"ch{idx:02d}_polished.md")
        _delete_path(camp.chapters_dir / f"ch{idx:02d}_reviewed.md")
        _delete_path(camp.chapters_dir / f"ch{idx:02d}_anchors.json")
        for entry in state.chapter_index:
            if entry.get("file") == f"ch{idx:02d}_draft.md" and entry.get("final_file"):
                _delete_path(camp.chapters_dir / entry["final_file"])
    target_files = {f"ch{idx:02d}_draft.md" for idx in target.chapter_indices or []}
    state.chapter_index = [entry for entry in state.chapter_index if entry.get("file") not in target_files]
    target.status = "drafting"
    target.chapter_indices = []
    target.closed_at = None
    save_story_state(state, camp.story_state_yaml)


def _save_pending_revision_edits(camp, campaign_outline, pending: dict[str, Any]) -> None:
    """Persist editable UI fields back into campaign_outline.pending_revision."""
    if not campaign_outline.pending_revision:
        return
    campaign_outline.pending_revision["new_sessions_summary"] = st.session_state.get(
        "rev_new_sessions_summary",
        pending.get("new_sessions_summary", ""),
    )
    campaign_outline.pending_revision["roster_impact_narrative"] = st.session_state.get(
        "rev_roster_impact",
        pending.get("roster_impact_narrative", ""),
    )

    for idx, _ in enumerate(pending.get("roster_changes") or []):
        key = f"rev_roster_desc_{idx}"
        if key in st.session_state and idx < len(campaign_outline.pending_revision.get("roster_changes", [])):
            campaign_outline.pending_revision["roster_changes"][idx]["description"] = st.session_state[key]

    for idx, _ in enumerate(pending.get("arc_updates") or []):
        summary_key = f"rev_arc_sum_{idx}"
        reason_key = f"rev_arc_reason_{idx}"
        if summary_key in st.session_state and idx < len(campaign_outline.pending_revision.get("arc_updates", [])):
            campaign_outline.pending_revision["arc_updates"][idx]["new_summary"] = st.session_state[summary_key]
        if reason_key in st.session_state and idx < len(campaign_outline.pending_revision.get("arc_updates", [])):
            campaign_outline.pending_revision["arc_updates"][idx]["reason"] = st.session_state[reason_key]

    for idx, _ in enumerate(pending.get("narrative_notes") or []):
        summary_key = f"rev_note_sum_{idx}"
        reason_key = f"rev_note_reason_{idx}"
        if summary_key in st.session_state and idx < len(campaign_outline.pending_revision.get("narrative_notes", [])):
            campaign_outline.pending_revision["narrative_notes"][idx]["summary"] = st.session_state[summary_key]
        if reason_key in st.session_state and idx < len(campaign_outline.pending_revision.get("narrative_notes", [])):
            campaign_outline.pending_revision["narrative_notes"][idx]["reason"] = st.session_state[reason_key]

    save_campaign_outline(camp, campaign_outline)


def _render_revision_draft(camp, campaign_outline, cmd_box) -> None:
    pending = campaign_outline.pending_revision
    if not pending:
        st.info("当前没有待处理的长期大纲修订草案。")
        return

    st.markdown("##### 待处理修订草案")
    st.caption("这里的内容可以先编辑，再应用到长期 campaign 大纲。")

    if "rev_new_sessions_summary" not in st.session_state:
        st.session_state["rev_new_sessions_summary"] = pending.get("new_sessions_summary", "")
    st.text_area("新 sessions 概要", key="rev_new_sessions_summary", height=86)

    if "rev_roster_impact" not in st.session_state:
        st.session_state["rev_roster_impact"] = pending.get("roster_impact_narrative", "")
    st.text_area("阵容影响", key="rev_roster_impact", height=86)

    accept_roster: list[str] = []
    accept_arcs: list[str] = []
    accept_notes: list[str] = []

    roster_changes = pending.get("roster_changes") or []
    with st.expander(f"阵容变化 · {len(roster_changes)} 项", expanded=bool(roster_changes)):
        if not roster_changes:
            st.caption("无阵容变化建议。")
        for idx, change in enumerate(roster_changes):
            col_text, col_accept, col_delete = st.columns([6, 1, 1])
            desc_key = f"rev_roster_desc_{idx}"
            if desc_key not in st.session_state:
                st.session_state[desc_key] = change.get("description", "")
            with col_text:
                st.text_area(
                    f"{change.get('name', '未命名')} / {change.get('change_type', '')}",
                    key=desc_key,
                    height=76,
                )
            with col_accept:
                st.write("")
                if st.checkbox("应用", key=f"rev_roster_chk_{idx}"):
                    accept_roster.append(change.get("name", ""))
            with col_delete:
                st.write("")
                if st.button("删除", key=f"rev_roster_del_{idx}"):
                    campaign_outline.pending_revision["roster_changes"].pop(idx)
                    save_campaign_outline(camp, campaign_outline)
                    st.cache_data.clear()
                    st.rerun()

    arc_updates = pending.get("arc_updates") or []
    with st.expander(f"主线更新 · {len(arc_updates)} 项", expanded=bool(arc_updates)):
        if not arc_updates:
            st.caption("无主线更新建议。")
        for idx, update in enumerate(arc_updates):
            col_summary, col_reason, col_accept, col_delete = st.columns([4, 3, 1, 1])
            summary_key = f"rev_arc_sum_{idx}"
            reason_key = f"rev_arc_reason_{idx}"
            if summary_key not in st.session_state:
                st.session_state[summary_key] = update.get("new_summary", "")
            if reason_key not in st.session_state:
                st.session_state[reason_key] = update.get("reason", "")
            with col_summary:
                st.text_area(f"{update.get('arc_id', '')} · 更新内容", key=summary_key, height=86)
            with col_reason:
                st.text_area("理由", key=reason_key, height=86)
            with col_accept:
                st.write("")
                if st.checkbox("应用", key=f"rev_arc_chk_{idx}"):
                    accept_arcs.append(update.get("arc_id", ""))
            with col_delete:
                st.write("")
                if st.button("删除", key=f"rev_arc_del_{idx}"):
                    campaign_outline.pending_revision["arc_updates"].pop(idx)
                    save_campaign_outline(camp, campaign_outline)
                    st.cache_data.clear()
                    st.rerun()

    narrative_notes = pending.get("narrative_notes") or []
    with st.expander(f"叙事备注 · {len(narrative_notes)} 项", expanded=bool(narrative_notes)):
        if not narrative_notes:
            st.caption("无叙事备注建议。")
        for idx, note in enumerate(narrative_notes):
            col_summary, col_reason, col_accept, col_delete = st.columns([4, 3, 1, 1])
            summary_key = f"rev_note_sum_{idx}"
            reason_key = f"rev_note_reason_{idx}"
            if summary_key not in st.session_state:
                st.session_state[summary_key] = note.get("summary", "")
            if reason_key not in st.session_state:
                st.session_state[reason_key] = note.get("reason", "")
            with col_summary:
                st.text_area(f"{note.get('key', '')} · 备注", key=summary_key, height=86)
            with col_reason:
                st.text_area("理由", key=reason_key, height=86)
            with col_accept:
                st.write("")
                if st.checkbox("应用", key=f"rev_note_chk_{idx}"):
                    accept_notes.append(note.get("key", ""))
            with col_delete:
                st.write("")
                if st.button("删除", key=f"rev_note_del_{idx}"):
                    campaign_outline.pending_revision["narrative_notes"].pop(idx)
                    save_campaign_outline(camp, campaign_outline)
                    st.cache_data.clear()
                    st.rerun()

    col_save, col_apply, col_all, col_discard = st.columns(4)
    if col_save.button("保存草案编辑", width="stretch", key="outline_save_revision_edits"):
        _save_pending_revision_edits(camp, campaign_outline, pending)
        st.success("已保存修订草案。")
        st.cache_data.clear()
        st.rerun()
    if col_apply.button("应用选定", width="stretch", key="outline_apply_selected_revision", type="primary"):
        if not (accept_arcs or accept_notes or accept_roster):
            st.warning("请先勾选至少一条修订建议，再应用选定。")
            return
        _save_pending_revision_edits(camp, campaign_outline, pending)
        code, _ = run_cmd(
            [
                "outline", "campaign-apply", "--campaign", camp.id,
                "--accept-arcs", ",".join(accept_arcs),
                "--accept-notes", ",".join(accept_notes),
                "--accept-roster", ",".join(accept_roster),
            ],
            cmd_box,
            camp.id,
        )
        if code == 0:
            _clear_revision_state()
            st.success("已应用选定修订。")
            st.cache_data.clear()
            st.rerun()
    if col_all.button("全部应用", width="stretch", key="outline_apply_all_revision"):
        _save_pending_revision_edits(camp, campaign_outline, pending)
        code, _ = run_cmd(["outline", "campaign-apply", "--campaign", camp.id, "--all"], cmd_box, camp.id)
        if code == 0:
            _clear_revision_state()
            st.success("已应用全部修订。")
            st.cache_data.clear()
            st.rerun()
    if col_discard.button("丢弃草案", width="stretch", key="outline_discard_revision"):
        campaign_outline.pending_revision = None
        save_campaign_outline(camp, campaign_outline)
        _clear_revision_state()
        st.cache_data.clear()
        st.rerun()


def _scene_status(scene_id: str, classification: dict[str, list[str]], scene_to_volume: dict[str, tuple[int, str]]) -> tuple[str, str]:
    owner = scene_to_volume.get(scene_id)
    if scene_id in classification["processed"]:
        return STATUS_LABELS["processed"]
    if owner:
        volume_index, status = owner
        label, color = STATUS_LABELS.get(status, (status, "#666"))
        return f"vol{volume_index:02d} / {label}", color
    if scene_id in classification["pending"]:
        return STATUS_LABELS["pending"]
    return STATUS_LABELS["unproposed"]


def _render_scene_timeline(sessions: list[str], all_scene_dicts: list[dict], summary_cache, classification, scene_to_volume) -> None:
    st.markdown("#### Scene 时间线")
    if not all_scene_dicts:
        st.info("暂无 scene，请先完成 segment。")
        return

    by_session: dict[str, list[dict]] = defaultdict(list)
    for scene in all_scene_dicts:
        by_session[scene["session_id"]].append(scene)

    for sid in sessions:
        scene_list = by_session.get(sid, [])
        with st.expander(f"{sid} · {len(scene_list)} 个 scene", expanded=False):
            for scene in scene_list:
                scene_id = scene["id"]
                summary = summary_cache.get(scene_id).summary if scene_id in summary_cache else ""
                status_text, color = _scene_status(scene_id, classification, scene_to_volume)
                html = (
                    f"<div class='tn-card' style='margin-bottom:10px;border-left:5px solid {color}'>"
                    f"<div class='tn-card-title'>{scene_id} · {scene['kind']} · "
                    f"{len(scene.get('event_ids', []))} 条 · {status_text}</div>"
                    f"<div style='margin-bottom:6px'>{badge(scene['session_id'], 'info')} {badge(scene['kind'], 'neutral')}</div>"
                    f"<div style='color:#d8d8d8'>{summary or '（无摘要）'}</div>"
                    f"</div>"
                )
                st.markdown(html, unsafe_allow_html=True)


def _load_volume_meta(camp, rec):
    outline = None
    try:
        outline = load_volume_outline(camp, rec.volume_index, prefer_draft=True)
    except Exception:
        outline = None
    title = outline.working_title if outline and outline.working_title else f"vol{rec.volume_index:02d}"
    scene_range = " → ".join(outline.scene_range) if outline and outline.scene_range else (
        f"{rec.scene_ids[0]} → {rec.scene_ids[-1]}" if rec.scene_ids else "无 scene"
    )
    total_ch_est = int(getattr(outline, "target_chapter_count_estimate", 0) or 0) if outline else 0
    return outline, title, scene_range, total_ch_est


def _render_job_status(job: dict[str, Any], *, total_ch_est: int | None = None) -> None:
    status = job.get("status")
    label = job.get("label") or "后台任务"
    state = "running" if status == "running" else ("complete" if status == "succeeded" else "error")
    with st.status(f"{label} · {job_elapsed(job)}", state=state, expanded=status == "running"):
        meta_cols = st.columns(4)
        meta_cols[0].metric("状态", {"running": "运行中", "succeeded": "完成", "failed": "失败"}.get(status, status))
        meta_cols[1].metric("PID", str(job.get("pid") or "启动中"))
        meta_cols[2].metric("耗时", job_elapsed(job))
        meta_cols[3].metric("退出码", "—" if job.get("returncode") is None else str(job.get("returncode")))

        if job.get("stage") == "chapterize":
            done, total = chapterize_progress(job, total_ch_est)
            if total:
                st.progress(min(done / total, 1.0), text=f"已切分 {done}/{total} 章")
            else:
                st.progress(0.0, text=f"已切分 {done} 章")
        elif status == "running":
            st.progress(0.25, text="LLM 正在生成整卷中文细节粗稿，完成前不会有精确 token 进度。")

        tail = output_tail(job)
        if tail.strip():
            st.code(tail, language="text")
        if job.get("error"):
            st.error(job["error"])


def _render_volume_header(rec, title: str, scene_range: str, total_ch_est: int) -> None:
    head_cols = st.columns([4, 2, 1, 1])
    head_cols[0].markdown(f"**vol{rec.volume_index:02d} · {title}**")
    head_cols[1].markdown(badge(STATUS_TITLES.get(rec.status, rec.status), "info"), unsafe_allow_html=True)
    head_cols[2].metric("scene", len(rec.scene_ids))
    head_cols[3].metric("预估章", total_ch_est or "—")
    st.caption(scene_range)


def _render_volume_card(camp, rec, all_scene_ids: list[str], cmd_box) -> None:
    outline, title, scene_range, total_ch_est = _load_volume_meta(camp, rec)
    with st.container(border=True):
        _render_volume_header(rec, title, scene_range, total_ch_est)
        _render_artifact_viewer(
            "卷大纲草案",
            volume_yaml_path(camp, rec.volume_index, draft=True),
            expanded=False,
        )
        if outline and outline.theme_summary:
            st.write(outline.theme_summary)
        if rec.proposal_reasoning:
            st.caption(f"理由：{rec.proposal_reasoning}")

        if rec.status in {"proposed", "draft"}:
            selected = st.multiselect(
                "scene 归属",
                options=all_scene_ids,
                default=list(rec.scene_ids),
                key=f"outline_volume_scenes_{rec.volume_index}",
            )
            edit_col, confirm_col, discard_col = st.columns(3)
            if edit_col.button("保存场景归属", width="stretch", key=f"outline_save_volume_{rec.volume_index}"):
                code, _ = run_cmd(
                    [
                        "outline", "volume-edit", str(rec.volume_index),
                        "--campaign", camp.id,
                        "--set-scenes", ",".join(selected),
                    ],
                    cmd_box,
                    camp.id,
                )
                if code == 0:
                    st.success(f"已更新 vol{rec.volume_index:02d}。")
                    st.cache_data.clear()
                    st.rerun()
            if confirm_col.button("确认此卷", width="stretch", key=f"outline_confirm_volume_{rec.volume_index}", type="primary"):
                code, _ = run_cmd(
                    ["outline", "confirm", str(rec.volume_index), "--campaign", camp.id],
                    cmd_box,
                    camp.id,
                )
                if code == 0:
                    st.success(f"已确认 vol{rec.volume_index:02d}。")
                    st.cache_data.clear()
                    st.rerun()
            confirm_key = f"outline_discard_volume_confirm_{rec.volume_index}"
            if discard_col.button("删除草案", width="stretch", key=f"outline_discard_volume_{rec.volume_index}"):
                st.session_state[confirm_key] = True
            if st.session_state.get(confirm_key):
                st.warning("删除草案会移除此卷方案和 draft 大纲文件；不会影响已确认的其他卷。")
                yes_col, no_col = st.columns(2)
                if yes_col.button("确认删除草案", width="stretch", key=f"outline_discard_volume_yes_{rec.volume_index}"):
                    state = load_story_state(camp.story_state_yaml)
                    state.volumes = [v for v in state.volumes if v.volume_index != rec.volume_index]
                    _delete_path(volume_yaml_path(camp, rec.volume_index, draft=True))
                    save_story_state(state, camp.story_state_yaml)
                    st.session_state.pop(confirm_key, None)
                    st.cache_data.clear()
                    st.rerun()
                if no_col.button("取消", width="stretch", key=f"outline_discard_volume_no_{rec.volume_index}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
        elif rec.status == "closed":
            st.page_link("pages/polish.py", label="前往润色页")


def _render_volume_section(camp, state, all_scene_ids: list[str], cmd_box) -> None:
    proposed_or_draft = [v for v in state.volumes if v.status in {"proposed", "draft"}]

    if not state.volumes:
        st.info("还没有卷方案草案。")
        return

    if proposed_or_draft:
        st.markdown("##### 待确认卷")
        for rec in proposed_or_draft:
            _render_volume_card(camp, rec, all_scene_ids, cmd_box)
    else:
        st.success("当前没有待确认卷。已确认的卷会在下一步生产区显示。")


def _render_skeleton_files(camp, rec) -> None:
    skeleton_path = Path(rec.skeleton_path) if rec.skeleton_path else camp.chapters_dir / f"vol{rec.volume_index:02d}_skeleton.md"
    if rec.skeleton_path:
        st.write(f"**卷级细节粗稿文件**：`{rec.skeleton_path}`")
    elif skeleton_path.exists():
        st.write(f"**卷级细节粗稿文件**：`{skeleton_path}`")
    if rec.word_count:
        st.metric("细节粗稿字数", f"{rec.word_count:,}")
    _render_artifact_viewer("卷级细节粗稿", skeleton_path, language="markdown")


def _render_scene_draft_manifest(camp, rec) -> tuple[bool, int]:
    man = load_manifest(manifest_path(camp.chapters_dir, rec.volume_index))
    if man is None:
        st.warning("尚未生成 scene 级草稿 manifest。请先生成卷级细节粗稿。")
        return False, 0
    completed = sum(1 for s in man.scenes if s.status == "complete")
    failed = sum(1 for s in man.scenes if s.status == "failed")
    pending = sum(1 for s in man.scenes if s.status == "pending")
    cols = st.columns(5)
    cols[0].metric("scene", len(man.scenes))
    cols[1].metric("完成", completed)
    cols[2].metric("失败", failed)
    cols[3].metric("待生成", pending)
    cols[4].metric("总字数", f"{man.total_word_count:,}")
    progress = completed / max(1, len(man.scenes))
    st.progress(progress, text=f"scene 级粗稿进度 {completed}/{len(man.scenes)}")
    if not man.complete:
        st.warning(
            f"粗稿不完整：需要全部 scene 完成，且总字数至少 {man.min_total_word_count:,}。"
            "请重试失败/缺失 scene 后再切章。"
        )
    with st.expander("查看 / 重生成 scene 草稿", expanded=not man.complete):
        for status in man.scenes:
            row = st.container(border=True)
            with row:
                top = st.columns([3, 1, 1, 1])
                top[0].markdown(f"**{status.scene_id}**")
                top[1].markdown(badge(status.status, "warn" if status.status == "failed" else "info"), unsafe_allow_html=True)
                top[2].metric("字数", status.word_count)
                top[3].metric("目标", status.target_words or "—")
                if status.error:
                    st.caption(f"错误：{status.error}")
                draft_path = scene_draft_path(camp.chapters_dir, rec.volume_index, status.scene_id)
                if draft_path.exists():
                    with st.expander("预览", expanded=False):
                        st.code(_read_text_preview(draft_path, max_chars=3000), language="markdown")
                c1, c2 = st.columns([1, 4])
                if c1.button("重生成此 scene", key=f"regen_scene_{rec.volume_index}_{status.scene_id}", width="stretch"):
                    start_pipeline_job(
                        campaign_id=camp.id,
                        stage="skeleton",
                        volume_index=rec.volume_index,
                        label=f"vol{rec.volume_index:02d} 重生成 {status.scene_id}",
                        args=[
                            "draft", "skeleton",
                            "--volume", str(rec.volume_index),
                            "--campaign", camp.id,
                            "--scene-id", status.scene_id,
                            "--force",
                        ],
                    )
                    st.rerun()
    return man.complete, man.target_chapter_count_estimate


def _render_chapter_files(camp, rec) -> None:
    if rec.chapter_indices:
        st.write("**已生成章号**：", ", ".join(f"ch{i:02d}" for i in rec.chapter_indices))
    for idx in rec.chapter_indices or []:
        path = camp.chapters_dir / f"ch{idx:02d}_draft.md"
        if path.exists():
            st.write(f"- `{path.name}`")
    if rec.chapter_indices:
        with st.expander("查看章节草稿", expanded=False):
            for idx in rec.chapter_indices:
                path = camp.chapters_dir / f"ch{idx:02d}_draft.md"
                if path.exists():
                    st.markdown(f"**{path.name}**")
                    st.code(_read_text_preview(path, max_chars=6000), language="markdown")


def _render_production_card(camp, rec, draft_ready: bool, detect_ready: bool) -> None:
    outline, title, scene_range, total_ch_est = _load_volume_meta(camp, rec)
    with st.container(border=True):
        _render_volume_header(rec, title, scene_range, total_ch_est)
        _render_artifact_viewer(
            "已确认卷大纲",
            volume_yaml_path(camp, rec.volume_index, draft=False),
            expanded=False,
        )
        _render_artifact_viewer(
            "卷大纲草案",
            volume_yaml_path(camp, rec.volume_index, draft=True),
            expanded=False,
        )
        if outline and outline.theme_summary:
            st.write(outline.theme_summary)

        skeleton_job = snapshot_job(campaign_id=camp.id, stage="skeleton", volume_index=rec.volume_index)
        chapterize_job = snapshot_job(campaign_id=camp.id, stage="chapterize", volume_index=rec.volume_index)
        running_job = next(
            (
                job for job in (skeleton_job, chapterize_job)
                if job and job.get("status") == "running"
            ),
            None,
        )

        if rec.status == "confirmed":
            st.caption("将按 scene 逐段生成中文细节粗稿，用于保留玩家动作、台词和选择；完成后汇总为卷级粗稿。")
            if not draft_ready:
                st.warning("需要先在「LLM 配置」配置“章节起草”阶段的 API Key。")
            if skeleton_job:
                _render_job_status(skeleton_job)
            action_col, rollback_col = st.columns(2)
            if action_col.button(
                "生成卷级细节粗稿",
                key=f"outline_start_skeleton_{rec.volume_index}",
                type="primary",
                width="stretch",
                disabled=(not draft_ready) or bool(running_job),
            ):
                start_pipeline_job(
                    campaign_id=camp.id,
                    stage="skeleton",
                    volume_index=rec.volume_index,
                    label=f"vol{rec.volume_index:02d} 生成卷级细节粗稿",
                    args=["draft", "skeleton", "--volume", str(rec.volume_index), "--campaign", camp.id],
                )
                st.rerun()
            rollback_key = f"outline_rollback_confirmed_confirm_{rec.volume_index}"
            if rollback_col.button(
                "退回待确认",
                key=f"outline_rollback_confirmed_{rec.volume_index}",
                width="stretch",
                disabled=bool(running_job),
            ):
                st.session_state[rollback_key] = True
            if st.session_state.get(rollback_key):
                st.warning("退回后会删除已确认卷大纲，scene 将重新变为待确认；卷草案仍保留。")
                yes_col, no_col = st.columns(2)
                if yes_col.button("确认退回待确认", width="stretch", key=f"outline_rollback_confirmed_yes_{rec.volume_index}"):
                    _rollback_volume_to_draft(camp, rec)
                    st.session_state.pop(rollback_key, None)
                    st.cache_data.clear()
                    st.rerun()
                if no_col.button("取消", width="stretch", key=f"outline_rollback_confirmed_no_{rec.volume_index}"):
                    st.session_state.pop(rollback_key, None)
                    st.rerun()

        elif rec.status == "drafting":
            _render_skeleton_files(camp, rec)
            manifest_complete, manifest_ch_est = _render_scene_draft_manifest(camp, rec)
            st.caption("将把卷级细节粗稿切分为约 2000 字短章草稿，并生成可编辑素材锚点。完成后状态进入 closed。")
            target_words = st.number_input(
                "章节草稿目标字数",
                min_value=1500,
                max_value=2800,
                value=2000,
                step=100,
                key=f"outline_chapterize_target_{rec.volume_index}",
                help="网文短章默认约 2000 字，润色后通常扩到 3500-4500 字。",
            )
            tolerance = st.number_input(
                "允许偏差",
                min_value=100,
                max_value=800,
                value=300,
                step=50,
                key=f"outline_chapterize_tolerance_{rec.volume_index}",
            )
            if not detect_ready:
                st.warning("需要先在「LLM 配置」配置“断点检测”阶段的 API Key。")
            if chapterize_job:
                _render_job_status(chapterize_job, total_ch_est=manifest_ch_est or total_ch_est)
            allow_incomplete = st.checkbox(
                "强行切章未完整粗稿",
                value=False,
                key=f"outline_allow_incomplete_{rec.volume_index}",
                help="仅用于调试；正常应先补齐失败 scene。",
            )
            action_col, rebuild_col, rollback_col = st.columns(3)
            if action_col.button(
                "切章",
                key=f"outline_start_chapterize_{rec.volume_index}",
                type="primary",
                width="stretch",
                disabled=(not detect_ready) or bool(running_job) or ((not manifest_complete) and (not allow_incomplete)),
            ):
                args = [
                    "chapterize",
                    "--volume", str(rec.volume_index),
                    "--campaign", camp.id,
                    "--target-words", str(int(target_words)),
                    "--tolerance", str(int(tolerance)),
                ]
                if allow_incomplete:
                    args.append("--allow-incomplete")
                start_pipeline_job(
                    campaign_id=camp.id,
                    stage="chapterize",
                    volume_index=rec.volume_index,
                    label=f"vol{rec.volume_index:02d} 切章",
                    args=args,
                )
                st.rerun()
            if rebuild_col.button(
                "仅重建卷稿",
                key=f"outline_rebuild_skeleton_{rec.volume_index}",
                width="stretch",
                disabled=bool(running_job),
                help="不调用 LLM，只从已生成 scene 草稿重新汇总 vol 文件。",
            ):
                start_pipeline_job(
                    campaign_id=camp.id,
                    stage="skeleton",
                    volume_index=rec.volume_index,
                    label=f"vol{rec.volume_index:02d} 重建卷级粗稿",
                    args=["draft", "skeleton", "--volume", str(rec.volume_index), "--campaign", camp.id, "--rebuild-only"],
                )
                st.rerun()
            rollback_key = f"outline_rollback_skeleton_confirm_{rec.volume_index}"
            if rollback_col.button(
                "删除细节粗稿并重来",
                key=f"outline_rollback_skeleton_{rec.volume_index}",
                width="stretch",
                disabled=bool(running_job),
            ):
                st.session_state[rollback_key] = True
            if st.session_state.get(rollback_key):
                st.warning("将删除 vol 细节粗稿文件，并把卷状态退回 confirmed。卷大纲和 scene 锁定不变。")
                yes_col, no_col = st.columns(2)
                if yes_col.button("确认删除细节粗稿", width="stretch", key=f"outline_rollback_skeleton_yes_{rec.volume_index}"):
                    _rollback_volume_to_confirmed(camp, rec)
                    st.session_state.pop(rollback_key, None)
                    st.cache_data.clear()
                    st.rerun()
                if no_col.button("取消", width="stretch", key=f"outline_rollback_skeleton_no_{rec.volume_index}"):
                    st.session_state.pop(rollback_key, None)
                    st.rerun()

        elif rec.status == "closed":
            _render_skeleton_files(camp, rec)
            _render_chapter_files(camp, rec)
            if chapterize_job and chapterize_job.get("status") in {"running", "succeeded"}:
                _render_job_status(chapterize_job, total_ch_est=total_ch_est)
            action_col, rollback_col = st.columns(2)
            with action_col:
                st.page_link("pages/polish.py", label="前往润色页")
            rollback_key = f"outline_rollback_chapters_confirm_{rec.volume_index}"
            if rollback_col.button("删除章节草稿", key=f"outline_rollback_chapters_{rec.volume_index}", width="stretch"):
                st.session_state[rollback_key] = True
            if st.session_state.get(rollback_key):
                st.warning("将删除此卷已生成的章节草稿、素材锚点、修订稿和润色稿，并把卷状态退回 drafting；卷级细节粗稿保留。")
                yes_col, no_col = st.columns(2)
                if yes_col.button("确认删除章节草稿", width="stretch", key=f"outline_rollback_chapters_yes_{rec.volume_index}"):
                    _rollback_volume_to_drafting(camp, rec)
                    st.session_state.pop(rollback_key, None)
                    st.cache_data.clear()
                    st.rerun()
                if no_col.button("取消", width="stretch", key=f"outline_rollback_chapters_no_{rec.volume_index}"):
                    st.session_state.pop(rollback_key, None)
                    st.rerun()


@st.fragment(run_every="1.5s")
def _render_production_section(camp, draft_ready: bool, detect_ready: bool) -> None:
    state = load_story_state(camp.story_state_yaml)
    production = [v for v in state.volumes if v.status in {"confirmed", "drafting", "closed"}]
    if not production:
        st.info("还没有已确认卷。先在上一步确认单卷，再生成卷级细节粗稿。")
        return
    for rec in production:
        _render_production_card(camp, rec, draft_ready, detect_ready)


def main() -> None:
    st.title("🧭 大纲规划")
    st.caption("解析完成后，在这里更新长期大纲、规划并确认卷、生成卷级细节粗稿、切分短章节草稿，然后进入章节审稿与润色。")

    camp = require_campaign()
    if camp is None:
        return

    state = load_story_state(camp.story_state_yaml)
    campaign_outline = load_campaign_outline(camp)
    sessions = list_sessions(camp)
    all_scene_dicts: list[dict] = []
    for sid in sessions:
        all_scene_dicts.extend(load_scenes(str(camp.parsed_dir), sid))
    all_scene_ids = [s["id"] for s in all_scene_dicts]
    classification = classify_scenes(all_scene_ids, state)
    remaining_ids = remaining_scene_ids_for_next_volume(all_scene_ids, state)
    summary_cache = load_summary_cache(camp.parsed_dir)
    missing_summaries = [sid for sid in all_scene_ids if sid not in summary_cache or not summary_cache[sid].summary]

    scene_to_volume: dict[str, tuple[int, str]] = {}
    for rec in state.volumes:
        for scene_id in rec.scene_ids:
            scene_to_volume[scene_id] = (rec.volume_index, rec.status)

    st.markdown("### 准备")
    metric_cols = st.columns(5)
    metric_cols[0].metric("总 scene", str(len(all_scene_ids)))
    metric_cols[1].metric("待规划", str(len(remaining_ids)))
    metric_cols[2].metric("已有卷", str(len(state.volumes)))
    metric_cols[3].metric("pending scene", str(len(state.pending_pool.scene_ids) if state.pending_pool else 0))
    metric_cols[4].metric("缺失摘要", str(len(missing_summaries)))

    cmd_box = st.empty()
    env_outline = read_env()
    detect_ready = bool(stage_value(env_outline, "detect", "api_key").strip())
    draft_ready = bool(stage_value(env_outline, "draft", "api_key").strip())

    with st.expander("场景摘要", expanded=bool(missing_summaries)):
        if missing_summaries:
            st.warning(f"{len(missing_summaries)} 个 scene 缺少摘要。卷方案会优先使用已有摘要，缺失时退回 scene 元信息。")
        else:
            st.success("所有 scene 摘要齐全。")
        sum_col1, sum_col2 = st.columns(2)
        if sum_col1.button("生成缺失摘要", width="stretch", key="gen_missing_summaries", disabled=not detect_ready):
            code, _ = run_cmd(["outline", "scene-summaries", "--campaign", camp.id, "--missing-only"], cmd_box, camp.id)
            if code == 0:
                st.cache_data.clear()
                st.rerun()
        if sum_col2.button("重新生成全部摘要", width="stretch", key="regen_all_summaries", disabled=not detect_ready):
            code, _ = run_cmd(["outline", "scene-summaries", "--campaign", camp.id, "--force"], cmd_box, camp.id)
            if code == 0:
                st.cache_data.clear()
                st.rerun()

    if campaign_outline is None:
        st.warning("先在 Pipeline 或 CLI 生成 campaign 大纲，再使用本页的长期大纲修订和卷规划。")

    st.markdown("### 1. 更新长期大纲")
    if campaign_outline is not None:
        _render_artifact_viewer("campaign 大纲", campaign_outline_yaml(camp), expanded=False)
        known = set(campaign_outline.based_on_sessions or [])
        new_sids = [sid for sid in sessions if sid not in known]
        info_cols = st.columns(3)
        info_cols[0].metric("已纳入 session", str(len(known)))
        info_cols[1].metric("新增 session", str(len(new_sids)))
        info_cols[2].metric("待处理修订", "有" if campaign_outline.pending_revision else "无")
        if new_sids:
            st.caption("新增 session：" + ", ".join(new_sids))

        with st.expander("管理已纳入长期大纲的 sessions", expanded=False):
            cur_based = campaign_outline.based_on_sessions or []
            if not cur_based:
                st.caption("当前没有已纳入记录。")
            else:
                st.write(", ".join(cur_based))
                clear_col, _ = st.columns([1, 3])
                if clear_col.button("清除全部纳入记录", key="clear_all_based"):
                    campaign_outline.based_on_sessions = []
                    save_campaign_outline(camp, campaign_outline)
                    st.cache_data.clear()
                    st.rerun()

        if st.button(
            "生成/更新修订草案",
            width="stretch",
            key="outline_generate_revision",
            type="primary",
            disabled=not detect_ready,
        ):
            code, _ = run_cmd(["outline", "campaign-revise", "--campaign", camp.id], cmd_box, camp.id)
            if code == 0:
                _clear_revision_state()
                st.cache_data.clear()
                st.rerun()

        _render_revision_draft(camp, campaign_outline, cmd_box)

    st.markdown("### 2. 规划并确认卷")
    if campaign_outline is None:
        st.info("缺少 campaign 大纲，暂不能生成卷方案。")
    elif not all_scene_ids:
        st.info("暂无 scene，请先完成 parse / classify / segment。")
    elif not remaining_ids:
        st.success("当前没有待规划 scene。")
    else:
        st.caption("生成后会写入 volNN.draft.yaml，并在状态中标记为 proposed；确认前都可以编辑。")
        if st.button(
            "生成/更新卷方案草案",
            width="stretch",
            key="outline_generate_volume_proposals",
            type="primary",
        ):
            code, _ = run_cmd(["outline", "volumes-propose", "--campaign", camp.id], cmd_box, camp.id)
            if code == 0:
                st.cache_data.clear()
                st.rerun()

    if state.pending_pool:
        with st.expander("Pending scenes", expanded=True):
            st.write(state.pending_pool.reason or "尾段暂不足成卷。")
            st.write(", ".join(state.pending_pool.scene_ids))

    _render_volume_section(camp, state, all_scene_ids, cmd_box)

    st.markdown("### 3. 生产卷内容")
    _render_production_section(camp, draft_ready, detect_ready)

    st.divider()
    _render_scene_timeline(sessions, all_scene_dicts, summary_cache, classification, scene_to_volume)


if __name__ == "__main__":
    main()

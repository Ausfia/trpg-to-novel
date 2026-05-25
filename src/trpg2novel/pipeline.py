"""trpg2novel CLI — 命令行入口。

用法：
    trpg2novel parse data/raw_logs/s01.md
    trpg2novel classify s01
    trpg2novel segment s01
    trpg2novel draft --auto-detect
    trpg2novel draft --force
    trpg2novel draft --session s01 --force
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from trpg2novel.config import (
    CHAPTERS_DIR,
    META_DIR,
    PARSED_DIR,
    RAW_LOG_DIR,
    load_llm_settings,
)
from trpg2novel.parse import classify_events, parse_file, save_events, save_tagged
from trpg2novel.parse.classify import SessionConfig, TaggedEvent
from trpg2novel.parse.md_loader import Event
from trpg2novel.parse.session_splitter import SessionChunk, split_by_time_gap
from trpg2novel.segment import save_scenes, segment_scenes
from trpg2novel.session_loader import load_players, load_session
from trpg2novel.state import load_state, save_state
from trpg2novel.worldview import load_worldview, load_worldview_for_campaign
from trpg2novel.campaign import Campaign
from trpg2novel.narrate.narrative_feed import build_feed, feed_to_text
from trpg2novel.narrate.chapter import (
    ChapterResult,
    detect_boundary,
    draft_chapter,
    save_chapter_draft,
    _extract_ending_marker,
)
from trpg2novel.narrate.polish import PolishModelSet, polish_chapter
from trpg2novel.chapterize.anchors import anchor_path_for_chapter, load_anchor_file
from trpg2novel.outline.scene_summary import (
    batch_generate_summaries,
    cache_path as scene_summary_cache_path,
    load_summary_cache,
)
from trpg2novel.outline.lifecycle import classify_scenes
from trpg2novel.outline.io import load_campaign_outline


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _players_cfg():
    return load_players(META_DIR / "players.yaml")


def _session_cfg(session_id: str):
    players = _players_cfg()
    yaml_path = RAW_LOG_DIR / f"{session_id}.yaml"

    if not yaml_path.exists():
        # yaml 不存在时（如通过 UI 上传/切分生成的 md 尚未手动配置），
        # 从 players.yaml 继承 dm/bot/pc 默认值，不崩溃。
        return SessionConfig(
            session_id=session_id,
            dm_handle=players.dm_handle,
            bot_handles=players.known_bots,
            player_handles=players.pc_names,
        )
    return load_session(yaml_path, players)


def _load_tagged(session_id: str) -> tuple[list[TaggedEvent], dict[str, TaggedEvent]]:
    path = PARSED_DIR / f"{session_id}.tagged.json"
    if not path.exists():
        click.echo(f"[ERROR] 找不到 {path}，请先运行 classify {session_id}", err=True)
        sys.exit(1)
    raw = json.loads(path.read_text(encoding="utf-8"))
    events = [TaggedEvent(**e) for e in raw]
    by_id = {e.id: e for e in events}
    return events, by_id


# ---------------------------------------------------------------------------
# 命令组
# ---------------------------------------------------------------------------


@click.group()
def cli():
    """trpg2novel — TRPG 日志 → 小说 pipeline。"""


@cli.command()
@click.argument("md_path", type=click.Path(exists=True))
@click.option("--session-id", default=None, help="Session ID（默认从文件名推断）")
def parse(md_path: str, session_id: str | None):
    """[1] 解析 .md 日志 → events.json。"""
    path = Path(md_path)
    sid = session_id or path.stem.split("_")[0]
    events = parse_file(path, sid)
    out = PARSED_DIR / f"{sid}.events.json"
    save_events(events, out)
    click.echo(f"✓ 解析完成：{len(events)} 条事件 → {out}")


@cli.command()
@click.argument("md_path", type=click.Path(exists=True))
@click.option("--gap-hours", default=8.0, type=float, help="相邻事件时间差阈值（小时），默认 8")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--dry-run", is_flag=True, help="只预览，不写盘")
def split(md_path: str, gap_hours: float, campaign_id: str, dry_run: bool):
    """[0] 按时间差切分多场融合日志 → 多份 s0X.md。"""
    text = Path(md_path).read_text(encoding="utf-8")
    chunks = split_by_time_gap(text, min_gap_hours=gap_hours)
    click.echo(f"✓ 切分结果：{len(chunks)} 段（阈值 {gap_hours}h）")
    for c in chunks:
        click.echo(f"  chunk {c.index}: {c.start_ts} → {c.end_ts}（{c.line_count} 行）")

    if dry_run or len(chunks) == 0:
        return

    camp = Campaign.load(campaign_id)
    start_n = len(camp.list_raw_logs()) + 1
    # 预加载 players，用于生成默认 yaml
    try:
        players = load_players(camp.players_yaml)
        default_dm = players.dm_handle
        default_bots = players.known_bots
    except Exception:
        default_dm, default_bots = "", []

    for c in chunks:
        sid = f"s{start_n + c.index:02d}"
        out = camp.raw_logs_dir / f"{sid}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(c.text, encoding="utf-8")
        click.echo(f"  → {out.name}")
        # 同步写 yaml（已存在则跳过，保留手动配置）
        yaml_out = camp.raw_logs_dir / f"{sid}.yaml"
        if not yaml_out.exists():
            import yaml as _yaml
            yaml_out.write_text(
                _yaml.safe_dump(
                    {"session_id": sid, "dm_handle": default_dm,
                     "bot_handles": default_bots, "absent_players": []},
                    allow_unicode=True, sort_keys=False,
                ),
                encoding="utf-8",
            )
    click.echo(f"✓ 已写入 {len(chunks)} 份日志到 {camp.raw_logs_dir}")


@cli.command()
@click.argument("session_id")
def classify(session_id: str):
    """[2] 分类 + 骰命令配对 → tagged.json。"""
    cfg = _session_cfg(session_id)
    events_raw = json.loads((PARSED_DIR / f"{session_id}.events.json").read_text(encoding="utf-8"))
    events = [Event(**e) for e in events_raw]
    tagged = classify_events(events, cfg)
    out = PARSED_DIR / f"{session_id}.tagged.json"
    save_tagged(tagged, out)
    dice_total = sum(1 for t in tagged for s in t.segments if s.kind == "roll_cmd" and s.extra.get("cmd_type") == "dice")
    paired = sum(1 for t in tagged for s in t.segments if s.kind == "roll_cmd" and "paired_result_event" in s.extra and s.extra.get("cmd_type") == "dice")
    click.echo(f"✓ 分类完成 → {out}")
    click.echo(f"  骰子命令配对率: {paired}/{dice_total}")


@cli.command()
@click.argument("session_id")
def segment(session_id: str):
    """[3] 场景切分 → scenes.json。"""
    _, by_id = _load_tagged(session_id)
    events = list(by_id.values())
    scenes = segment_scenes(events, session_id)
    out = PARSED_DIR / f"{session_id}.scenes.json"
    save_scenes(scenes, out)
    click.echo(f"✓ 切分完成：{len(scenes)} 个场景 → {out}")
    for sc in scenes:
        click.echo(f"  {sc.id} [{sc.kind}] {sc.start_ts}→{sc.end_ts} {sc.event_count}条")


def _discover_sessions() -> list[str]:
    """扫描 parsed/ 目录，返回所有已有 scenes.json 的 session ID。"""
    if not PARSED_DIR.exists():
        return []
    return sorted(p.stem.split(".")[0] for p in PARSED_DIR.glob("*.scenes.json"))


@cli.group()
def draft():
    """生成草稿。子命令：legacy（旧单章流程）/ skeleton（新卷级细节粗稿流程）。"""


@draft.command("legacy")
@click.option("--auto-detect", is_flag=True, help="自动判断章节边界（默认行为）")
@click.option("--force", is_flag=True, help="强制将所有 pending 场景生成一章")
@click.option("--session", "session_ids", multiple=True, help="指定 session（可多次；不传则自动发现）")
@click.option("--last-summary", default="", help="上一章结尾摘要（供 LLM 参考）")
def draft_legacy(auto_detect: bool, force: bool, session_ids: tuple, last_summary: str):
    """检测章节边界并生成草稿。默认自动判断断点，--force 强制全部入章。"""
    if not session_ids:
        session_ids = tuple(_discover_sessions())
        if not session_ids:
            click.echo("[ERROR] 未指定 session，也未找到已 segment 的场次", err=True)
            sys.exit(1)

    all_scenes = []
    all_events_by_id: dict = {}
    from trpg2novel.segment.scene import Scene
    for sid in session_ids:
        _, by_id = _load_tagged(sid)
        all_events_by_id.update(by_id)
        scenes_path = PARSED_DIR / f"{sid}.scenes.json"
        if not scenes_path.exists():
            click.echo(f"[ERROR] {scenes_path} 不存在，请先运行 segment {sid}", err=True)
            sys.exit(1)
        raw = json.loads(scenes_path.read_text(encoding="utf-8"))
        all_scenes.extend([Scene(**s) for s in raw])

    state = load_state(META_DIR / "story_state.yaml")
    _migrate_chapter_index(CHAPTERS_DIR, state)

    processed = set(state.processed_scene_ids)
    remaining = [s for s in all_scenes if s.id not in processed]
    if not remaining:
        click.echo("[INFO] 所有场景已全部入章，没有可处理的内容。")
        return

    cfg = load_llm_settings()

    # ---- 决定章节边界 ----
    chapter_title: str
    focus: list[str]
    scenes_for_chapter: list

    if not force:
        click.echo(f"⏳ 正在判断章节边界（待处理 {len(remaining)} 场，{sum(s.event_count for s in remaining)} 条事件）…")
        result = detect_boundary(
            remaining, all_events_by_id, state, last_summary,
            api_key=cfg.detect.api_key,
            base_url=cfg.detect.base_url,
            model=cfg.detect.model,
        )
        click.echo(f"→ 判断结果：{result.status}  原因：{result.reason}")
        if result.status == "insufficient":
            click.echo("   提示：当前内容不足以成章，请上传新跑团日志后再试（或使用 --force 强制生成）")
            return
        chapter_title = result.chapter_title_suggestion or "无题"
        focus = result.focus_characters
        end_id = (result.end_scene_id or "").strip()
        if end_id and any(s.id == end_id for s in remaining):
            cutoff = next(i for i, s in enumerate(remaining) if s.id == end_id) + 1
            scenes_for_chapter = remaining[:cutoff]
        else:
            if end_id:
                click.echo(f"[WARN] LLM 返回的 end_scene_id='{end_id}' 不在待处理场景中，回退使用全部 remaining")
            scenes_for_chapter = remaining

        # 复杂度检查
        scene_count = len(scenes_for_chapter)
        event_count = sum(s.event_count for s in scenes_for_chapter)
        if scene_count > 6 or event_count > 150:
            click.echo(f"⚠️  章节复杂度超标：{scene_count} 个场景，{event_count} 个事件", err=True)
            click.echo(f"   建议：3-5 个场景，50-100 个事件", err=True)
            click.echo(f"   最大：6 个场景，150 个事件", err=True)
            click.echo(f"   当前章节过于复杂，可能导致 draft 和 polish 质量下降", err=True)
            click.echo(f"   建议：", err=True)
            click.echo(f"     1. 等待更多场景后再生成（让 LLM 选择更早的断点）", err=True)
            click.echo(f"     2. 手动调整场景划分", err=True)
            click.echo(f"     3. 使用 --force 强制生成（不推荐）", err=True)
            return
    else:
        scenes_for_chapter = remaining
        chapter_title = "强制生成章节"
        focus = []
        click.echo("⚡ 强制生成模式，跳过边界检测")

    # 若 last_summary 未传，尝试从上一章 ending_marker 获取
    if not last_summary and state.chapter_index:
        last_entry = state.chapter_index[-1]
        last_summary = last_entry.get("ending_marker", "") or last_entry.get("last_summary", "")
    # 上一章 ending marker 作为续写锚点
    previous_marker = ""
    if state.chapter_index:
        previous_marker = state.chapter_index[-1].get("ending_marker", "")

    # 加载默认团的 worldview
    camp = None
    try:
        camp = Campaign.load("jl_zheng_zheng")
        worldview = load_worldview_for_campaign(camp)
    except Exception:
        worldview = load_worldview("dnd5e")

    # 载入全团人物卡（YAML 优先，xlsx 仅向后兼容诺菲雅一人）
    try:
        from trpg2novel.character import load_all_cards, load_card
        from trpg2novel.config import CHARACTER_CARD_DIR
        cards = load_all_cards(CHARACTER_CARD_DIR)
        # xlsx 回退：若 character_cards/ 下没有任何 yaml，尝试旧 xlsx
        if not cards:
            _card_candidates = [
                CHARACTER_CARD_DIR / "诺菲雅.xlsx",
                Path("诺菲雅.xlsx"),
            ]
            card_path = next((p for p in _card_candidates if p.exists()), None)
            if card_path:
                card = load_card(card_path)
                cards = {card.name: card}
        nofiyad_facts = cards.get("诺菲雅", next(iter(cards.values()), None))
        nofiyad_facts = nofiyad_facts.atomic_facts if nofiyad_facts else []
        pc_facts = {name: c.atomic_facts for name, c in cards.items() if c.atomic_facts}
    except Exception:
        nofiyad_facts = []
        pc_facts = {}

    pc_count = len(pc_facts)
    click.echo(f"✓ 人物卡加载：{pc_count} 人（{'、'.join(pc_facts) if pc_count else '无'}）")

    # 收集缺席玩家（从各场次 yaml 读取）
    all_absent: list[str] = []
    for sid in session_ids:
        try:
            cfg_sid = _session_cfg(sid)
            all_absent.extend(cfg_sid.absent_players)
        except Exception:
            pass
    all_absent = list(dict.fromkeys(all_absent))  # 去重，保持顺序

    # 收集本次应安排离场的退团角色
    retired: list[dict] = []
    for name, c in cards.items():
        if c.left_after_session and c.left_after_session in session_ids:
            retired.append({"name": c.name, "exit_story": c.exit_story or ""})

    if all_absent:
        click.echo(f"✓ 缺席角色：{', '.join(all_absent)}")
    if retired:
        click.echo(f"✓ 退团角色（将安排离场）：{', '.join(r['name'] for r in retired)}")

    click.echo(f"⏳ 正在生成草稿：{chapter_title}（{len(scenes_for_chapter)} 场）…")
    # 尝试加载 KB（若 kb_config.yaml 存在且配置了 api_key）
    kb = None
    if camp is not None:
        try:
            from trpg2novel.rag import KnowledgeBase, load_kb_config
            kb_cfg_path = camp.kb_config_yaml
            if kb_cfg_path.exists():
                kb_cfg = load_kb_config(kb_cfg_path)
                if kb_cfg.is_configured():
                    kb = KnowledgeBase.open(camp.knowledge_base_dir, kb_cfg)
                    if kb.count_chunks() > 0:
                        click.echo(f"✓ RAG 知识库就绪：{kb.count_chunks()} 片段")
                    else:
                        click.echo("[INFO] 知识库为空，跳过 RAG 注入（请先重建索引）")
                        kb = None
        except Exception as e:
            click.echo(f"[WARN] 加载知识库失败，跳过 RAG：{e}")
            kb = None

    chapter = draft_chapter(
        scenes_for_chapter,
        all_events_by_id,
        state,
        nofiyad_facts,
        chapter_title,
        focus,
        last_chapter_summary=last_summary,
        absent_players=all_absent or None,
        retired_characters=retired or None,
        previous_ending_marker=previous_marker,
        api_key=cfg.draft.api_key,
        base_url=cfg.draft.base_url,
        model=cfg.draft.model,
        worldview=worldview,
        pc_facts=pc_facts if pc_count > 0 else None,
        kb=kb,
    )

    # 用 state.chapter_index 而不是 glob，避免并发/迁移期重号
    idx = len(state.chapter_index) + 1
    out = CHAPTERS_DIR / f"ch{idx:02d}_draft.md"
    # 防御：万一同名文件已存在（migration 没覆盖到），递增到空号
    while out.exists():
        idx += 1
        out = CHAPTERS_DIR / f"ch{idx:02d}_draft.md"
    save_chapter_draft(chapter, out)
    words = len(chapter.draft_text)

    # 更新 state
    state.processed_scene_ids.extend([s.id for s in scenes_for_chapter])
    ending_marker = _extract_ending_marker(chapter.draft_text)
    state.chapter_index.append({
        "file": out.name,
        "title": chapter.chapter_title,
        "scene_ids": [s.id for s in scenes_for_chapter],
        "focus": list(chapter.focus_characters),
        "last_summary": last_summary,
        "ending_marker": ending_marker,
    })
    for sid in session_ids:
        if sid not in state.session_log:
            state.session_log.append(sid)
    save_state(state, META_DIR / "story_state.yaml")

    remaining_after = len(remaining) - len(scenes_for_chapter)
    click.echo(f"✓ 草稿已生成：{out}（约 {words} 字）")
    click.echo(f"  本次入章 {len(scenes_for_chapter)} 场，剩余 {remaining_after} 场未处理")
    if remaining_after > 0:
        click.echo("  → 可再次运行 draft legacy 命令生成下一章")


@draft.command("skeleton")
@click.option("--volume", "volume_index", type=int, required=True, help="卷号（必填）")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--scene-id", default="", help="只生成/重生成指定 scene")
@click.option("--force", is_flag=True, help="覆盖已有 scene 草稿")
@click.option("--rebuild-only", is_flag=True, help="不调用 LLM，只从已有 scene 草稿重建卷级细节粗稿")
def draft_skeleton_cmd(volume_index: int, campaign_id: str, scene_id: str, force: bool, rebuild_only: bool):
    """按卷大纲生成连续卷级细节粗稿（vol{NN}_skeleton.md）。"""
    camp = Campaign.load(campaign_id)
    state = load_state(camp.story_state_yaml)

    from trpg2novel.outline.io import load_volume_outline
    from trpg2novel.outline.lifecycle import find_volume, mark_drafting
    from trpg2novel.narrate.skeleton import draft_skeleton_incremental

    volume_outline = load_volume_outline(camp, volume_index, prefer_draft=False)
    if volume_outline is None:
        click.echo(f"[ERROR] 找不到 volume {volume_index} 的大纲。", err=True)
        return

    rec = find_volume(state, volume_index)
    if rec is None:
        click.echo(f"[ERROR] story_state 中没有 volume {volume_index} 记录，请先 outline confirm。", err=True)
        return
    if rec.status not in ("confirmed", "drafting"):
        click.echo(f"[ERROR] volume {volume_index} 状态为 {rec.status}，需先 outline confirm。", err=True)
        return

    all_scenes, all_events_by_id = _load_scenes_for_campaign(camp)
    scene_map = {s.id: s for s in all_scenes}
    vol_scenes = [scene_map[sid] for sid in volume_outline.based_on_scenes if sid in scene_map]
    if not vol_scenes:
        click.echo("[ERROR] 卷大纲的 based_on_scenes 在当前数据中找不到任何 scene。", err=True)
        return
    if scene_id and scene_id not in {s.id for s in vol_scenes}:
        click.echo(f"[ERROR] scene-id {scene_id} 不属于 volume {volume_index}。", err=True)
        return

    settings = load_llm_settings()

    # 加载人物卡
    try:
        from trpg2novel.character import load_all_cards
        cards = load_all_cards(camp.character_cards_dir)
        pc_facts = {name: c.atomic_facts for name, c in cards.items() if c.atomic_facts}
    except Exception:
        pc_facts = {}

    # 加载世界观
    try:
        worldview = load_worldview_for_campaign(camp)
    except Exception:
        worldview = load_worldview("dnd5e")

    # RAG
    kb = None
    try:
        from trpg2novel.rag import KnowledgeBase, load_kb_config
        kb_cfg = load_kb_config(camp.kb_config_yaml)
        if kb_cfg.is_configured():
            _kb = KnowledgeBase.open(camp.knowledge_base_dir, kb_cfg)
            if _kb.count_chunks() > 0:
                kb = _kb
                click.echo(f"✓ RAG 知识库就绪：{_kb.count_chunks()} 片段")
    except Exception:
        pass

    # 上一卷结尾标记（若有）
    last_marker = ""
    if volume_index > 1:
        prev_rec = find_volume(state, volume_index - 1)
        if prev_rec and prev_rec.skeleton_path:
            prev_path = Path(prev_rec.skeleton_path)
            if prev_path.exists():
                prev_text = prev_path.read_text(encoding="utf-8")
                m = re.search(r"<!--\s*SCENE_BOUNDARY:.*?\s*-->", prev_text)
                if m:
                    tail = prev_text[m.end():m.end() + 300]
                    last_marker = tail.strip()[:200]

    click.echo(f"⏳ 正在生成 vol{volume_index:02d} 卷级细节粗稿...")
    click.echo(f"  scenes: {len(vol_scenes)}")
    target_words_per_scene = 2200
    click.echo(f"  target_words: ~{len(vol_scenes) * target_words_per_scene}")
    click.echo(f"  model: {settings.draft.model}")
    if scene_id:
        click.echo(f"  scene_id: {scene_id}")
    if rebuild_only:
        click.echo("  mode: rebuild-only（不调用 LLM）")

    try:
        result = draft_skeleton_incremental(
            volume_outline,
            vol_scenes,
            all_events_by_id,
            state,
            chapters_dir=camp.chapters_dir,
            worldview=worldview,
            pc_facts=pc_facts if pc_facts else None,
            last_volume_ending_marker=last_marker,
            api_key=settings.draft.api_key,
            base_url=settings.draft.base_url,
            model=settings.draft.model,
            kb=kb,
            target_words_per_scene=target_words_per_scene,
            scene_id=scene_id,
            force=force,
            rebuild_only=rebuild_only,
            progress_callback=lambda sid, status: click.echo(f"  → {sid}: {status}"),
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    out_path = camp.chapters_dir / f"vol{volume_index:02d}_skeleton.md"

    rec = mark_drafting(state, volume_index, skeleton_path=str(out_path))
    rec.word_count = result.word_count
    state.current_volume_index = max(state.current_volume_index, volume_index)
    save_state(state, camp.story_state_yaml)

    click.echo(f"✓ 卷级细节粗稿已生成：{out_path}")
    click.echo(f"  字数：{result.word_count}（目标 {result.target_word_count}，最低 {round(result.target_word_count * 0.6)}）")
    click.echo(f"  scene 偏移：{len(result.scene_offsets)}/{len(vol_scenes)} 已定位")
    click.echo(f"  预估章数：{result.target_chapter_count}")
    click.echo(f"  manifest：{result.manifest_path}")
    if not result.complete:
        click.echo("  ⚠ 粗稿不完整：有 scene 缺失/失败，或总字数低于目标 60%。请重试失败 scene 后再切章。")
    click.echo(f"  volume {volume_index}: confirmed → drafting")


def _migrate_chapter_index(chapters_dir: Path, state) -> None:
    """把 chapters/chXX_draft.md 里没记录在 state.chapter_index 的回填进去。

    解析 ch 文件首两行：'# <title>' 和 '<!-- scenes: ..., events: N, focus: ... -->'。
    回填条目里的 scene_ids 也同步加入 state.processed_scene_ids（避免重复入章）。
    一次性、幂等。
    """
    if not chapters_dir.exists():
        return
    recorded = {entry.get("file") for entry in state.chapter_index}
    processed = set(state.processed_scene_ids)
    added = 0
    for path in sorted(chapters_dir.glob("ch*_draft.md")):
        if path.name in recorded:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title = ""
        scene_ids: list[str] = []
        focus: list[str] = []
        for line in text.splitlines()[:6]:
            line = line.strip()
            if line.startswith("# ") and not title:
                title = line[2:].strip()
            elif line.startswith("<!--") and "scenes:" in line:
                # 形如：<!-- scenes: s01-scene-001, s01-scene-002 | events: 12 | focus: 诺菲雅 -->
                body = line.strip("<!- >")
                for part in body.split("|"):
                    k, _, v = part.partition(":")
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "scenes" and v:
                        scene_ids = [x.strip() for x in v.split(",") if x.strip()]
                    elif k == "focus" and v:
                        focus = [x.strip() for x in v.split(",") if x.strip()]
        state.chapter_index.append({
            "file": path.name,
            "title": title or path.stem,
            "scene_ids": scene_ids,
            "focus": focus,
            "last_summary": "",
        })
        for sid in scene_ids:
            if sid not in processed:
                state.processed_scene_ids.append(sid)
                processed.add(sid)
        added += 1
    if added:
        click.echo(f"[migration] 已回填 {added} 个历史章节到 chapter_index")


@cli.command("chapterize")
@click.option("--volume", "volume_index", type=int, required=True, help="卷号（必填）")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--target-words", default=2000, type=int, help="每章目标字数")
@click.option("--tolerance", default=300, type=int, help="字数偏差容忍")
@click.option("--allow-incomplete", is_flag=True, help="允许对未通过完整性校验的卷级粗稿强行切章")
def chapterize_cmd(volume_index: int, campaign_id: str, target_words: int, tolerance: int, allow_incomplete: bool):
    """把卷级细节粗稿切分为章节草稿（vol{NN}_skeleton.md → ch{NN}_draft.md）。"""
    camp = Campaign.load(campaign_id)
    state = load_state(camp.story_state_yaml)

    from trpg2novel.outline.io import load_volume_outline
    from trpg2novel.outline.lifecycle import find_volume, mark_closed
    from trpg2novel.chapterize.runner import chapterize_volume
    from trpg2novel.chapterize.writer import write_chapters
    import yaml as _yaml

    # 加载卷级细节粗稿
    skeleton_path = camp.chapters_dir / f"vol{volume_index:02d}_skeleton.md"
    if not skeleton_path.exists():
        click.echo(f"[ERROR] 找不到卷级细节粗稿文件：{skeleton_path}", err=True)
        return

    raw = skeleton_path.read_text(encoding="utf-8")
    skeleton_text, front_matter = _parse_skeleton_front_matter(raw)
    if not front_matter.get("complete", True) and not allow_incomplete:
        click.echo(
            "[ERROR] 卷级细节粗稿未通过完整性校验。请重试缺失/失败 scene，或显式传 --allow-incomplete 强行切章。",
            err=True,
        )
        return

    scene_offsets = front_matter.get("scene_offsets") or []
    if not scene_offsets:
        click.echo(f"[ERROR] 卷级细节粗稿 front-matter 缺少 scene_offsets。", err=True)
        return

    volume_outline = load_volume_outline(camp, volume_index, prefer_draft=False)
    if volume_outline is None:
        click.echo(f"[ERROR] 找不到 volume {volume_index} 的大纲。", err=True)
        return

    rec = find_volume(state, volume_index)
    if rec is None:
        click.echo(f"[ERROR] story_state 中没有 volume {volume_index} 记录。", err=True)
        return
    if rec.status not in ("drafting", "closed"):
        click.echo(f"[ERROR] volume {volume_index} 状态为 {rec.status}，需先 draft skeleton。", err=True)
        return

    replaced_indices: list[int] = []
    if rec.status == "closed":
        import re as _re_cleanup
        volume_entries = [
            entry for entry in state.chapter_index
            if int(entry.get("volume_index") or 0) == volume_index
            and entry.get("source") == "volume_skeleton"
        ]
        files_to_delete = {entry.get("file", "") for entry in volume_entries if entry.get("file")}
        for idx in rec.chapter_indices or []:
            files_to_delete.add(f"ch{idx:02d}_draft.md")
        for name in files_to_delete:
            m = _re_cleanup.match(r"ch(\d+)_draft\.md", name)
            if m:
                replaced_indices.append(int(m.group(1)))
            base = name.replace("_draft.md", "")
            for suffix in ("_draft.md", "_revised.md", "_polished.md", "_reviewed.md", "_anchors.json"):
                p = camp.chapters_dir / f"{base}{suffix}"
                if p.exists():
                    p.unlink()
            for entry in volume_entries:
                if entry.get("file") == name and entry.get("final_file"):
                    p = camp.chapters_dir / entry["final_file"]
                    if p.exists():
                        p.unlink()
        if volume_entries:
            state.chapter_index = [entry for entry in state.chapter_index if entry not in volume_entries]

    # 计算章节起始编号
    if replaced_indices:
        start_ch = min(replaced_indices)
    else:
        existing = sorted(camp.chapters_dir.glob("ch*_draft.md"))
        start_ch = 1
    if not replaced_indices and existing:
        import re as _re
        nums = []
        for p in existing:
            m = _re.match(r"ch(\d+)_draft\.md", p.name)
            if m:
                nums.append(int(m.group(1)))
        if nums:
            start_ch = max(nums) + 1

    settings = load_llm_settings()

    click.echo(f"⏳ 正在把 vol{volume_index:02d} 细节粗稿切分为章节草稿...")
    click.echo(f"  细节粗稿字数：{len(skeleton_text)}")
    click.echo(f"  目标章字数：{target_words} ±{tolerance}")
    click.echo(f"  起始章号：ch{start_ch:02d}")

    try:
        result = chapterize_volume(
            skeleton_text,
            scene_offsets,
            volume_outline,
            target_words=target_words,
            tolerance=tolerance,
            api_key=settings.detect.api_key,
            base_url=settings.detect.base_url,
            model=settings.detect.model,
            start_chapter_index=start_ch,
            progress_callback=lambda idx, total: click.echo(f"  → 第 {idx} 章切分完成"),
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    all_scenes, all_events_by_id = _load_scenes_for_campaign(camp)
    scenes_by_id = {s.id: s for s in all_scenes}

    paths = write_chapters(
        result,
        skeleton_text,
        camp.chapters_dir,
        start_chapter_index=start_ch,
        skeleton_source=skeleton_path.name,
        scenes_by_id=scenes_by_id,
        events_by_id=all_events_by_id,
    )

    chapter_indices = [start_ch + i for i in range(result.chapter_count)]
    recorded_files = {entry.get("file") for entry in state.chapter_index}
    for path, cut in zip(paths, result.cuts, strict=False):
        if path.name in recorded_files:
            continue
        state.chapter_index.append({
            "file": path.name,
            "title": cut.suggested_title or path.stem.replace("_draft", ""),
            "scene_ids": list(cut.scene_ids_covered),
            "focus": [],
            "last_summary": "",
            "ending_marker": "",
            "volume_index": volume_index,
            "source": "volume_skeleton",
        })
    rec = mark_closed(state, volume_index, chapter_indices=chapter_indices, word_count=result.skeleton_word_count)
    rec.skeleton_path = str(skeleton_path)
    state.current_volume_index = max(state.current_volume_index, volume_index)
    save_state(state, camp.story_state_yaml)

    click.echo(f"✓ 章节草稿已生成：{len(paths)} 章")
    for p in paths:
        click.echo(f"  → {p.name}")
        anchor_path = p.with_name(p.stem.replace("_draft", "") + "_anchors.json")
        if anchor_path.exists():
            click.echo(f"    素材锚点：{anchor_path.name}")
    if result.hard_cap_count:
        click.echo(f"  按目标字数兜底切分：{result.hard_cap_count}/{result.chapter_count}")
    else:
        click.echo("  切点质量：未使用字数兜底切分")
    if result.chapter_count >= 4 and result.hard_cap_ratio > 0.4:
        click.echo(f"  ⚠ 字数兜底切分占比 {result.hard_cap_ratio:.0%}，建议调整 target-words 或检查细节粗稿段落/scene 边界")


def _parse_skeleton_front_matter(raw: str) -> tuple[str, dict]:
    """解析卷级细节粗稿文件的 YAML front-matter，返回 (正文, front_matter_dict)。"""
    import yaml as _yaml
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            fm = _yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
            return body, fm
    return raw, {}


def _parse_chapter_draft_metadata(raw: str) -> dict:
    """解析 chNN_draft.md 顶部的 HTML metadata 注释。"""
    meta: dict = {}
    for line in raw.splitlines()[:10]:
        stripped = line.strip()
        if not (stripped.startswith("<!--") and stripped.endswith("-->")):
            continue
        body = stripped[4:-3].strip()
        for part in body.split("|"):
            key, _, value = part.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "volume_draft" and value.startswith("vol"):
                try:
                    meta["volume_index"] = int(value[3:])
                except ValueError:
                    pass
            elif key == "chapter_in_volume":
                try:
                    meta["chapter_index"] = int(value)
                except ValueError:
                    pass
            elif key == "total_chapters":
                try:
                    meta["total_chapters"] = int(value)
                except ValueError:
                    pass
            elif key == "skeleton_source":
                meta["skeleton_source"] = value
    return meta


@cli.command()
@click.argument("chapter_file", type=click.Path(exists=True))
@click.option("--last-summary", default="", help="上一章结尾摘要（可选）")
@click.option("--style-profile", default="", help="风格方案名或 YAML 路径（推荐）")
@click.option("--style-recipe", default="", help="旧版风格配方名或 YAML 路径（兼容）")
@click.option("--pov-mode", default="", help="叙事视角：third_limited / third_omniscient / first_person")
@click.option("--protagonist", default="", help="主角或主视角候选（可选）")
@click.option("--use-style-kb/--no-style-kb", default=True, help="是否启用独立风格知识库")
@click.option("--self-check/--no-self-check", default=False, help="是否运行轻量正文清理自检")
def polish(
    chapter_file: str,
    last_summary: str,
    style_profile: str,
    style_recipe: str,
    pov_mode: str,
    protagonist: str,
    use_style_kb: bool,
    self_check: bool,
):
    """[8] 对修订稿进行文学化成稿，生成 chXX_polished.md。"""
    import os
    draft_path = Path(chapter_file)
    # 优先使用 revised 版本，否则用 draft
    revised_path = draft_path.with_name(draft_path.name.replace("_draft", "_revised"))
    source_path = revised_path if revised_path.exists() else draft_path
    text = source_path.read_text(encoding="utf-8")
    click.echo(f"✓ 使用源文件：{source_path.name}（{len(text)} 字）")

    cfg = load_llm_settings()
    camp_id = os.environ.get("DEFAULT_CAMPAIGN_ID", "jl_zheng_zheng")
    camp = None
    try:
        camp = Campaign.load(camp_id)
        wv = load_worldview_for_campaign(camp)
        from trpg2novel.character import load_all_cards
        cards = load_all_cards(camp.character_cards_dir)
        pc_facts = {n: c.atomic_facts for n, c in cards.items() if c.atomic_facts}
    except Exception:
        wv = load_worldview("dnd5e")
        pc_facts = {}

    from trpg2novel.style import load_style_profile, load_style_recipe, profile_from_recipe
    profile = None
    recipe = None
    if camp is not None:
        if style_profile:
            profile = load_style_profile(style_profile, campaign=camp)
        elif style_recipe:
            recipe = load_style_recipe(style_recipe, campaign=camp)
            profile = profile_from_recipe(recipe)
        else:
            profile = load_style_profile(None, campaign=camp)
    else:
        recipe = load_style_recipe(style_recipe or None, campaign=camp)
        profile = profile_from_recipe(recipe)
    click.echo(f"✓ 风格方案：{profile.name}")

    # 尝试加载世界观 KB
    kb = None
    if camp is not None:
        try:
            from trpg2novel.rag import KnowledgeBase, load_kb_config
            kb_cfg = load_kb_config(camp.kb_config_yaml)
            if kb_cfg.is_configured():
                _kb = KnowledgeBase.open(camp.knowledge_base_dir, kb_cfg)
                if _kb.count_chunks() > 0:
                    kb = _kb
                    click.echo(f"✓ 世界观 RAG 就绪：{_kb.count_chunks()} 片段")
        except Exception as e:
            click.echo(f"[WARN] 加载世界观知识库失败，跳过 RAG：{e}")

    # 尝试加载风格 KB
    style_kb = None
    if camp is not None and use_style_kb and (profile.use_style_kb if profile is not None else True):
        try:
            from trpg2novel.rag import KnowledgeBase, load_kb_config
            style_kb_cfg = load_kb_config(camp.style_kb_config_yaml)
            if style_kb_cfg.is_configured():
                _style_kb = KnowledgeBase.open(camp.style_knowledge_base_dir, style_kb_cfg)
                if _style_kb.count_chunks() > 0:
                    style_kb = _style_kb
                    click.echo(f"✓ 风格 RAG 就绪：{_style_kb.count_chunks()} 片段")
        except Exception as e:
            click.echo(f"[WARN] 加载风格知识库失败，跳过 Style RAG：{e}")

    chapter_title = draft_path.stem.replace("_draft", "").replace("_", " ")

    # —— 卷级细节粗稿切出的章节草稿：尝试加载卷大纲，传递给 polish 用于扩写 ——
    volume_outline = None
    target_word_count = 0
    chapter_in_volume = 0
    total_chapters_in_volume = 0
    if camp is not None:
        body_text, fm = _parse_skeleton_front_matter(text)
        if fm:
            text = body_text  # 用剥离 front-matter 后的正文进行 polish
        else:
            fm = _parse_chapter_draft_metadata(text)
        vol_idx = fm.get("volume_index")
        ch_idx = fm.get("chapter_index")
        if vol_idx and ch_idx:
            try:
                from trpg2novel.outline.io import load_volume_outline
                volume_outline = load_volume_outline(camp, int(vol_idx), prefer_draft=False)
                if volume_outline:
                    # 统计本卷章节草稿数量
                    import re as _re2
                    vol_chapters = []
                    for p in camp.chapters_dir.glob("ch*_draft.md"):
                        raw_fm = p.read_text(encoding="utf-8")
                        p_fm = _parse_chapter_draft_metadata(raw_fm)
                        if p_fm.get("volume_index") == int(vol_idx):
                            m = _re2.match(r"ch(\d+)_draft\.md", p.name)
                            if m:
                                vol_chapters.append(int(m.group(1)))
                    chapter_in_volume = int(ch_idx)
                    total_chapters_in_volume = int(fm.get("total_chapters") or len(vol_chapters))
                    target_word_count = max(3500, min(4500, len(text) * 2))
                    click.echo(
                        f"✓ 卷大纲已加载：vol{vol_idx:02d}（第 {chapter_in_volume}/{total_chapters_in_volume} 章，"
                        f"目标 {target_word_count} 字）"
                    )
            except Exception as e:
                click.echo(f"[INFO] 无法加载卷大纲：{e}")

    anchors = None
    anchor_path = anchor_path_for_chapter(draft_path)
    if anchor_path.exists():
        try:
            anchors = load_anchor_file(anchor_path)
            total_anchor_items = sum(len(anchors.get(k) or []) for k in ("actions", "dialogues", "choices", "emotions"))
            click.echo(f"✓ 素材锚点已加载：{anchor_path.name}（{total_anchor_items} 条）")
        except Exception as e:
            click.echo(f"[WARN] 读取素材锚点失败，跳过：{e}")
    else:
        click.echo("[WARN] 未找到素材锚点文件；本章仍可润色，但保留跑团细节能力较弱。")

    if target_word_count <= 0:
        target_word_count = max(3500, min(4500, len(text) * 2))
        click.echo(f"✓ 润色目标字数：{target_word_count}（按章节草稿约 2 倍估算）")

    model_set = PolishModelSet(
        rewrite=cfg.polish_workflow.rewrite,
        check=cfg.polish_workflow.check,
    )

    try:
        polished = polish_chapter(
            text,
            worldview=wv,
            pc_facts=pc_facts or None,
            last_chapter_summary=last_summary,
            api_key=cfg.polish.api_key,
            base_url=cfg.polish.base_url,
            model=cfg.polish.model,
            kb=kb,
            chapter_title=chapter_title,
            style_profile=profile,
            style_recipe=recipe,
            style_kb=style_kb,
            pov_mode=pov_mode,
            protagonist=protagonist,
            model_set=model_set,
            run_self_check=self_check,
            volume_outline=volume_outline,
            target_word_count=target_word_count,
            chapter_in_volume=chapter_in_volume,
            total_chapters=total_chapters_in_volume,
            anchors=anchors,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    out_suffix = "_polished.md"
    if "_skeleton" in draft_path.name:
        out_suffix = "_polished.md"
    out = draft_path.with_name(
        draft_path.stem.replace("_draft", "").replace("_skeleton", "") + out_suffix
    )
    out.write_text(polished, encoding="utf-8")
    click.echo(f"✓ 润色完成：{out}（{len(polished)} 字）")


# ---------------------------------------------------------------------------
# outline 命令组（PR1a：scene 摘要基础设施）
# ---------------------------------------------------------------------------


@cli.group()
def outline():
    """大纲与卷规划相关命令。"""


def _load_scenes_for_campaign(camp: Campaign) -> tuple[list, dict[str, TaggedEvent]]:
    """加载某团全部 scenes 与 events_by_id（按 session 拼接）。"""
    from trpg2novel.segment.scene import Scene

    scenes: list[Scene] = []
    events_by_id: dict[str, TaggedEvent] = {}
    for sid in camp.list_sessions():
        scenes_path = camp.parsed_dir / f"{sid}.scenes.json"
        tagged_path = camp.parsed_dir / f"{sid}.tagged.json"
        if not scenes_path.exists() or not tagged_path.exists():
            continue
        for s in json.loads(scenes_path.read_text(encoding="utf-8")):
            scenes.append(Scene(**s))
        for e in json.loads(tagged_path.read_text(encoding="utf-8")):
            ev = TaggedEvent(**e)
            events_by_id[ev.id] = ev
    return scenes, events_by_id


@outline.command("scene-summaries")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--session", "session_ids", multiple=True, help="仅处理指定 session（可多次）")
@click.option("--force", is_flag=True, help="忽略缓存重新生成全部摘要")
@click.option("--missing-only", is_flag=True, help="仅生成缺失或 needs_review 的摘要")
def outline_scene_summaries(
    campaign_id: str,
    session_ids: tuple,
    force: bool,
    missing_only: bool,
):
    """为团内全部（或指定 session）的 scene 生成一句话摘要并缓存。

    输出：``data/campaigns/<id>/parsed/scene_summaries.json``
    """
    camp = Campaign.load(campaign_id)
    scenes, events_by_id = _load_scenes_for_campaign(camp)
    if session_ids:
        scenes = [s for s in scenes if s.session_id in session_ids]

    if not scenes:
        click.echo("[WARN] 没有找到任何 scene。先跑 segment。", err=True)
        return

    if missing_only:
        existing = load_summary_cache(camp.parsed_dir)
        scenes = [
            s for s in scenes
            if s.id not in existing
            or not existing[s.id].summary
            or existing[s.id].needs_review
        ]
        if not scenes:
            click.echo("✓ 全部 scene 摘要齐全且通过校验，无需重生成。")
            return

    settings = load_llm_settings()
    model_cfg = settings.detect  # 用便宜模型做摘要

    click.echo(f"→ 团 {camp.id}：准备生成 {len(scenes)} 条 scene 摘要（model={model_cfg.model}）")

    def _progress(idx: int, total: int, scene, summary) -> None:
        flag = "⚠ needs_review" if summary.needs_review else ""
        preview = summary.summary[:40].replace("\n", " ")
        click.echo(f"  [{idx}/{total}] {scene.id}: {preview} {flag}")

    batch_generate_summaries(
        scenes,
        events_by_id,
        campaign_parsed_dir=camp.parsed_dir,
        model_cfg=model_cfg,
        force=force,
        progress=_progress,
    )
    click.echo(f"✓ 缓存写入：{scene_summary_cache_path(camp.parsed_dir)}")




@outline.command("campaign")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--seed", default="", help="额外的种子说明文本")
@click.option("--regenerate", is_flag=True, help="忽略已有草稿，重新生成 campaign 大纲")
def outline_campaign(campaign_id: str, seed: str, regenerate: bool):
    """生成或重生成 campaign 级长期大纲。"""
    camp = Campaign.load(campaign_id)
    scenes, events_by_id = _load_scenes_for_campaign(camp)
    if not scenes:
        click.echo("[ERROR] 没有可用于生成 campaign 大纲的 scenes。请先 parse / classify / segment。", err=True)
        return

    state = load_state(camp.story_state_yaml)
    settings = load_llm_settings()
    from trpg2novel.outline.generate import generate_campaign_outline

    outline = generate_campaign_outline(
        camp,
        state,
        scenes,
        events_by_id,
        model_cfg=settings.review,
        seed_text=seed,
        force_regenerate=regenerate,
    )
    state.last_campaign_outline_update_sessions = sorted({s.session_id for s in scenes})
    save_state(state, camp.story_state_yaml)
    click.echo(f"✓ campaign 大纲已写入：{camp.root / 'outline' / 'campaign.yaml'}")
    click.echo(f"  title: {outline.title or '（未命名）'}")
    click.echo(f"  sessions: {', '.join(outline.based_on_sessions) if outline.based_on_sessions else '无'}")


@outline.command("volume")
@click.argument("volume_index", type=int)
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--regenerate", is_flag=True, help="忽略已有草稿，重新生成详细卷大纲")
@click.option("--last-summary", default="", help="上一卷摘要（可选）")
def outline_volume(campaign_id: str, volume_index: int, regenerate: bool, last_summary: str):
    """生成某卷的详细大纲草稿。"""
    camp = Campaign.load(campaign_id)
    all_scenes, all_events_by_id = _load_scenes_for_campaign(camp)
    if not all_scenes:
        click.echo("[ERROR] 没有可用于生成卷大纲的 scenes。请先 parse / classify / segment。", err=True)
        return

    state = load_state(camp.story_state_yaml)
    from trpg2novel.outline.io import load_volume_outline, volume_yaml_path
    from trpg2novel.outline.generate import generate_volume_outline
    from trpg2novel.outline.lifecycle import remaining_scene_ids_for_next_volume, register_volume_draft
    from trpg2novel.outline.io import load_campaign_outline

    campaign_outline = load_campaign_outline(camp)
    if campaign_outline is None:
        click.echo("[ERROR] 先运行 outline campaign，再生成卷大纲。", err=True)
        return

    existing = None if regenerate else load_volume_outline(camp, volume_index, prefer_draft=True)
    all_scene_map = {s.id: s for s in all_scenes}
    if existing and existing.based_on_scenes:
        scene_ids = [sid for sid in existing.based_on_scenes if sid in all_scene_map]
    else:
        scene_ids = remaining_scene_ids_for_next_volume([s.id for s in all_scenes], state)
        if not scene_ids:
            scene_ids = [s.id for s in all_scenes]

    scenes = [all_scene_map[sid] for sid in scene_ids if sid in all_scene_map]
    if not scenes:
        click.echo(f"[ERROR] 无法为 volume {volume_index} 找到可用 scenes。", err=True)
        return

    settings = load_llm_settings()
    prev_summary = last_summary
    if not prev_summary and volume_index > 1:
        prev_outline = load_volume_outline(camp, volume_index - 1, prefer_draft=False)
        if prev_outline:
            prev_summary = prev_outline.theme_summary or prev_outline.working_title

    outline = generate_volume_outline(
        camp,
        volume_index,
        scenes,
        all_events_by_id,
        state,
        campaign_outline,
        model_cfg=settings.review,
        last_volume_summary=prev_summary,
        force_regenerate=regenerate,
    )
    draft_path = volume_yaml_path(camp, volume_index, draft=True)
    register_volume_draft(
        state,
        volume_index=volume_index,
        scene_ids=[s.id for s in scenes],
        outline_path=str(draft_path),
        proposal_reasoning=outline.proposal_reasoning,
        status="draft",
    )
    state.current_volume_index = max(state.current_volume_index, volume_index)
    save_state(state, camp.story_state_yaml)
    click.echo(f"✓ 卷 {volume_index} 大纲已写入：{draft_path}")
    click.echo(f"  scenes: {len(scenes)}")
    click.echo(f"  title: {outline.working_title or '（未命名）'}")


@outline.command("confirm")
@click.argument("volume_index", type=int)
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
def outline_confirm(campaign_id: str, volume_index: int):
    """确认某卷并锁定 scene 范围。"""
    camp = Campaign.load(campaign_id)
    from datetime import datetime
    from trpg2novel.outline.io import load_volume_outline, volume_yaml_path, save_volume_outline
    from trpg2novel.outline.lifecycle import find_volume, promote_to_confirmed, register_volume_draft

    state = load_state(camp.story_state_yaml)
    outline = load_volume_outline(camp, volume_index, prefer_draft=True)
    if outline is None:
        click.echo(f"[ERROR] 找不到 volume {volume_index} 的草稿或正式大纲。", err=True)
        return

    rec = find_volume(state, volume_index)
    if rec is None:
        register_volume_draft(
            state,
            volume_index=volume_index,
            scene_ids=list(outline.based_on_scenes),
            outline_path=str(volume_yaml_path(camp, volume_index, draft=True)),
            proposal_reasoning=outline.proposal_reasoning,
            status=outline.status if outline.status in {"proposed", "draft"} else "draft",
        )

    outline.status = "confirmed"
    outline.user_confirmed = True
    outline.confirmed_at = datetime.now().isoformat(timespec="seconds")
    final_path = save_volume_outline(camp, outline, as_draft=False, snapshot=True)
    rec = promote_to_confirmed(state, volume_index)
    rec.outline_path = str(final_path)
    save_state(state, camp.story_state_yaml)
    click.echo(f"✓ 已确认 volume {volume_index} → {final_path}")


@outline.command("status")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
def outline_status(campaign_id: str):
    """查看 campaign / volume / pending 的当前状态。"""
    camp = Campaign.load(campaign_id)
    state = load_state(camp.story_state_yaml)
    campaign_outline = None
    try:
        from trpg2novel.outline.io import load_campaign_outline
        campaign_outline = load_campaign_outline(camp)
    except Exception:
        campaign_outline = None

    scenes, _ = _load_scenes_for_campaign(camp)
    all_scene_ids = [s.id for s in scenes]
    from trpg2novel.outline.lifecycle import classify_scenes, remaining_scene_ids_for_next_volume
    cls = classify_scenes(all_scene_ids, state)
    remaining = remaining_scene_ids_for_next_volume(all_scene_ids, state)

    click.echo(f"Campaign: {camp.id}")
    click.echo(f"  current_volume_index: {state.current_volume_index}")
    if campaign_outline:
        click.echo(f"  outline: {campaign_outline.title or '（未命名）'}")
        click.echo(f"  last_updated_at: {campaign_outline.last_updated_at or '—'}")
    click.echo(f"  scenes: total={len(all_scene_ids)} processed={len(cls['processed'])} draft={len(cls['in_draft_volumes'])} pending={len(cls['pending'])} unproposed={len(cls['unproposed'])}")
    click.echo(f"  remaining_for_next_volume: {len(remaining)}")
    if state.volumes:
        click.echo("Volumes:")
        for v in sorted(state.volumes, key=lambda x: x.volume_index):
            click.echo(
                f"  - vol{v.volume_index:02d} [{v.status}] scenes={len(v.scene_ids)} "
                f"chapters={len(v.chapter_indices)} words={v.word_count or '—'}"
            )
    else:
        click.echo("  （暂无卷记录）")
    if state.pending_pool:
        click.echo(f"Pending: {', '.join(state.pending_pool.scene_ids)}")
        click.echo(f"  reason: {state.pending_pool.reason}")
    else:
        click.echo("Pending: （空）")

@outline.command("campaign-revise")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--preview", is_flag=True, help="仅预览，不写盘")
def outline_campaign_revise(campaign_id: str, preview: bool):
    """增量修订 campaign 大纲：阵容同步 + LLM 叙事修订。"""
    camp = Campaign.load(campaign_id)
    scenes, events_by_id = _load_scenes_for_campaign(camp)
    state = load_state(camp.story_state_yaml)
    settings = load_llm_settings()

    from trpg2novel.outline.revise import propose_campaign_revision

    proposal = propose_campaign_revision(
        camp,
        state,
        scenes,
        events_by_id,
        model_cfg=settings.detect,
    )

    click.echo(f"Campaign: {camp.id}")
    click.echo(f"  new_sessions: {', '.join(proposal.new_sessions) if proposal.new_sessions else '（无）'}")
    click.echo(f"  roster_changes: {len(proposal.roster_changes)}")
    for ch in proposal.roster_changes:
        click.echo(f"    [{ch.get('change_type')}] {ch.get('name')}: {ch.get('description', '')}")
    click.echo(f"  arc_updates: {len(proposal.arc_updates)}")
    for arc in proposal.arc_updates:
        click.echo(f"    [{arc.get('arc_id')}] {arc.get('reason', '')}")
    click.echo(f"  narrative_notes: {len(proposal.narrative_notes)}")
    for note in proposal.narrative_notes:
        click.echo(f"    [{note.get('key')}] {note.get('summary', '')}")
    click.echo(f"  new_sessions_summary: {proposal.new_sessions_summary or '（无）'}")
    click.echo(f"  roster_impact_narrative: {proposal.roster_impact_narrative or '（无）'}")

    if preview or not proposal.has_any():
        if not proposal.has_any():
            click.echo("[INFO] 无修订建议，大纲已是最新。")
        return

    outline = load_campaign_outline(camp)
    if outline is None:
        click.echo("[ERROR] 找不到 campaign 大纲。", err=True)
        return
    outline.pending_revision = proposal.to_dict()
    from trpg2novel.outline.io import save_campaign_outline
    save_campaign_outline(camp, outline, snapshot=True)
    state.last_campaign_outline_update_sessions = camp.list_sessions()
    save_state(state, camp.story_state_yaml)
    click.echo("✓ 修订提议已写入 campaign.yaml（pending_revision 字段）")


@outline.command("campaign-apply")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--accept-arcs", default="", help="逗号分隔的 arc_id")
@click.option("--accept-notes", default="", help="逗号分隔的 narrative_note key")
@click.option("--accept-roster", default="", help="逗号分隔的角色名")
@click.option("--all", "accept_all", is_flag=True, help="接受全部修订")
def outline_campaign_apply(campaign_id: str, accept_arcs: str, accept_notes: str, accept_roster: str, accept_all: bool):
    """应用 pending_revision 中选定的修订项。"""
    camp = Campaign.load(campaign_id)
    outline = load_campaign_outline(camp)
    if outline is None or outline.pending_revision is None:
        click.echo("[INFO] 没有待处理的修订。")
        return

    from trpg2novel.outline.revise import CampaignRevisionProposal, apply_revision

    proposal = CampaignRevisionProposal(
        arc_updates=list(outline.pending_revision.get("arc_updates") or []),
        narrative_notes=list(outline.pending_revision.get("narrative_notes") or []),
        new_sessions_summary=outline.pending_revision.get("new_sessions_summary", ""),
        roster_impact_narrative=outline.pending_revision.get("roster_impact_narrative", ""),
        roster_changes=list(outline.pending_revision.get("roster_changes") or []),
        new_sessions=list(outline.pending_revision.get("new_sessions") or []),
    )

    if accept_all:
        accept_arc_ids = {a.get("arc_id", "") for a in proposal.arc_updates}
        accept_note_keys = {n.get("key", "") for n in proposal.narrative_notes}
        accept_roster_names = {ch.get("name", "") for ch in proposal.roster_changes}
    else:
        accept_arc_ids = {a.strip() for a in accept_arcs.split(",") if a.strip()}
        accept_note_keys = {n.strip() for n in accept_notes.split(",") if n.strip()}
        accept_roster_names = {r.strip() for r in accept_roster.split(",") if r.strip()}

    updated = apply_revision(
        camp,
        proposal,
        accept_arc_ids=accept_arc_ids,
        accept_narrative_keys=accept_note_keys,
        accept_roster_names=accept_roster_names,
    )
    click.echo(f"✓ 已应用修订：{camp.id}")
    click.echo(f"  title: {updated.title or '（未命名）'}")
    click.echo(f"  evolution_notes: {len(updated.evolution_notes)}")


@outline.command("volumes-propose")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--preview", is_flag=True, help="仅预览，不写盘")
@click.option("--hint", "hint_scene_id", default=None, help="强切提示 scene_id")
@click.option("--max-scenes-per-batch", default=30, type=int, help="每批 scene 上限")
def outline_volumes_propose(campaign_id: str, preview: bool, hint_scene_id: str | None, max_scenes_per_batch: int):
    """提议多卷边界。"""
    camp = Campaign.load(campaign_id)
    scenes, _ = _load_scenes_for_campaign(camp)
    if not scenes:
        click.echo("[ERROR] 没有可用于提议卷边界的 scenes。请先 parse / classify / segment。", err=True)
        return

    state = load_state(camp.story_state_yaml)
    campaign_outline = load_campaign_outline(camp)
    if campaign_outline is None:
        click.echo("[ERROR] 先运行 outline campaign，再提议卷边界。", err=True)
        return

    cls = classify_scenes([s.id for s in scenes], state)
    remaining_ids = cls["pending"] + cls["unproposed"]
    scene_map = {s.id: s for s in scenes}
    target_scenes = [scene_map[sid] for sid in remaining_ids if sid in scene_map]
    summaries = load_summary_cache(camp.parsed_dir)
    settings = load_llm_settings()
    from trpg2novel.outline.propose import propose_volumes, proposal_to_outline
    from trpg2novel.outline.io import save_volume_outline
    from trpg2novel.outline.lifecycle import register_volume_draft, set_pending_pool, clear_pending_pool

    result = propose_volumes(
        target_scenes,
        summaries,
        campaign_outline,
        state,
        max_scenes_per_batch=max_scenes_per_batch,
        model_cfg=settings.detect,
        hint_scene_id=hint_scene_id,
    )

    click.echo(f"Campaign: {camp.id}")
    click.echo(f"  scenes_in: {len(target_scenes)}")
    click.echo(f"  proposed_volumes: {len(result.proposed_volumes)}")
    click.echo(f"  pending_scenes: {len(result.pending_scenes)}")
    for idx, proposal in enumerate(result.proposed_volumes, start=1):
        click.echo(
            f"  - vol{idx:02d} {proposal.scene_id_range[0]} → {proposal.scene_id_range[1]} "
            f"({len(proposal.scene_ids)} scenes) {proposal.working_title}"
        )

    if preview:
        return

    next_index = max([v.volume_index for v in state.volumes], default=0) + 1
    for proposal in result.proposed_volumes:
        outline = proposal_to_outline(proposal, volume_index=next_index, scenes=[scene_map[sid] for sid in proposal.scene_ids if sid in scene_map])
        draft_path = save_volume_outline(camp, outline, as_draft=True, snapshot=True)
        register_volume_draft(
            state,
            volume_index=next_index,
            scene_ids=list(proposal.scene_ids),
            outline_path=str(draft_path),
            proposal_reasoning=proposal.reasoning,
            status="proposed",
        )
        click.echo(f"  ✓ proposed: vol{next_index:02d} → {draft_path}")
        next_index += 1

    if result.pending_scenes:
        set_pending_pool(state, result.pending_scenes, result.pending_reason or "尾段不足成卷")
    else:
        clear_pending_pool(state)
    save_state(state, camp.story_state_yaml)


@outline.command("volume-edit")
@click.argument("volume_index", type=int)
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--set-scenes", default="", help="逗号分隔的 scene_ids")
def outline_volume_edit(campaign_id: str, volume_index: int, set_scenes: str):
    """编辑一个 draft/proposed 卷的 scene 归属。"""
    camp = Campaign.load(campaign_id)
    from trpg2novel.outline.io import load_volume_outline, save_volume_outline
    from trpg2novel.outline.lifecycle import find_volume

    state = load_state(camp.story_state_yaml)
    rec = find_volume(state, volume_index)
    if rec is None:
        click.echo(f"[ERROR] 找不到卷 {volume_index}。", err=True)
        return

    outline = load_volume_outline(camp, volume_index, prefer_draft=True)
    if outline is None:
        click.echo(f"[ERROR] 找不到 volume {volume_index} 的大纲文件。", err=True)
        return

    scene_ids = [sid.strip() for sid in set_scenes.split(",") if sid.strip()]
    if not scene_ids:
        click.echo("[ERROR] --set-scenes 不能为空。", err=True)
        return

    outline.based_on_scenes = scene_ids
    outline.session_ids = []
    outline.scene_range = [scene_ids[0], scene_ids[-1]]
    save_volume_outline(camp, outline, as_draft=True, snapshot=True)
    rec.scene_ids = scene_ids
    save_state(state, camp.story_state_yaml)
    click.echo(f"✓ 已更新 vol{volume_index:02d} scenes：{len(scene_ids)}")


@outline.command("volumes-accept")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--all", "accept_all", is_flag=True, help="接受全部提议")
def outline_volumes_accept(campaign_id: str, accept_all: bool):
    """接受卷提议：把 proposed 状态卷变更为 draft。"""
    camp = Campaign.load(campaign_id)
    state = load_state(camp.story_state_yaml)

    changed = 0
    for rec in state.volumes:
        if rec.status == "proposed":
            rec.status = "draft"
            changed += 1

    if changed == 0:
        click.echo(f"[INFO] 无可接受的 proposed 卷：{camp.id}")
        return

    save_state(state, camp.story_state_yaml)
    click.echo(f"✓ 已接受卷提议：{camp.id}（{changed} 卷）")


@outline.command("show")
@click.option("--campaign", "campaign_id", default="jl_zheng_zheng", help="目标团 ID")
@click.option("--volume", "volume_index", type=int, default=0, help="显示某卷大纲；不传则显示 campaign 大纲")
@click.option("--draft/--final", "prefer_draft", default=True, help="卷大纲优先显示草稿还是正式版")
def outline_show(campaign_id: str, volume_index: int, prefer_draft: bool):
    """显示 campaign 或 volume 大纲内容。"""
    camp = Campaign.load(campaign_id)
    from dataclasses import asdict
    import json as _json
    from trpg2novel.outline.io import load_campaign_outline, load_volume_outline

    if volume_index > 0:
        obj = load_volume_outline(camp, volume_index, prefer_draft=prefer_draft)
        label = f"volume {volume_index}"
    else:
        obj = load_campaign_outline(camp)
        label = "campaign"
    if obj is None:
        click.echo(f"[WARN] 找不到 {label} 大纲。")
        return
    click.echo(_json.dumps(asdict(obj), ensure_ascii=False, indent=2))


def main():
    cli()


if __name__ == "__main__":
    main()

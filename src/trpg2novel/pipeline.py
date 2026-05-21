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
import sys
from pathlib import Path

import click

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
)
from trpg2novel.narrate.polish import polish_chapter


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


@cli.command()
@click.option("--auto-detect", is_flag=True, help="自动判断章节边界（默认）")
@click.option("--force", is_flag=True, help="强制将所有 pending 场景生成章节")
@click.option("--session", "session_ids", multiple=True, help="指定 session（可多次）")
@click.option("--last-summary", default="", help="上一章结尾摘要（供 LLM 参考）")
def draft(auto_detect: bool, force: bool, session_ids: tuple, last_summary: str):
    """[5+6] 检测章节边界并生成草稿。"""
    if not session_ids:
        state = load_state(META_DIR / "story_state.yaml")
        session_ids = tuple(state.session_log)
        if not session_ids:
            click.echo("[ERROR] 未指定 session，也未在 story_state.yaml 找到已处理 session", err=True)
            sys.exit(1)

    all_scenes = []
    all_events_by_id: dict = {}
    for sid in session_ids:
        _, by_id = _load_tagged(sid)
        all_events_by_id.update(by_id)
        scenes_path = PARSED_DIR / f"{sid}.scenes.json"
        if not scenes_path.exists():
            click.echo(f"[ERROR] {scenes_path} 不存在，请先运行 segment {sid}", err=True)
            sys.exit(1)
        from trpg2novel.segment.scene import Scene
        raw = json.loads(scenes_path.read_text(encoding="utf-8"))
        all_scenes.extend([Scene(**s) for s in raw])

    state = load_state(META_DIR / "story_state.yaml")
    cfg = load_llm_settings()

    # 加载默认团的 worldview（pipeline 暂未带 --campaign，先用默认）
    camp = None
    try:
        camp = Campaign.load("jl_zheng_zheng")
        worldview = load_worldview_for_campaign(camp)
    except Exception:
        worldview = load_worldview("dnd5e")

    if not force:
        click.echo("⏳ 正在判断章节边界…")
        result = detect_boundary(
            all_scenes,
            all_events_by_id,
            state,
            last_summary,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model_chapter_detect,
        )
        click.echo(f"→ 判断结果：{result.status}")
        click.echo(f"   原因：{result.reason}")
        if result.status != "enough_for_chapter":
            click.echo("   提示：材料不足以成章，等下一场后重试（或使用 --force 强制生成）")
            return
        chapter_title = result.chapter_title_suggestion or "无题"
        focus = result.focus_characters
    else:
        chapter_title = "强制生成章节"
        focus = []
        click.echo("⚡ 强制生成模式，跳过边界检测")

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

    click.echo(f"⏳ 正在生成草稿：{chapter_title} …")
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
        all_scenes,
        all_events_by_id,
        state,
        nofiyad_facts,
        chapter_title,
        focus,
        last_chapter_summary=last_summary,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        model=cfg.model_draft,
        worldview=worldview,
        pc_facts=pc_facts if pc_count > 0 else None,
        kb=kb,
    )

    idx = len(list(CHAPTERS_DIR.glob("ch*.md"))) + 1
    out = CHAPTERS_DIR / f"ch{idx:02d}_draft.md"
    save_chapter_draft(chapter, out)
    words = len(chapter.draft_text)
    click.echo(f"✓ 草稿已生成：{out}（约 {words} 字）")


@cli.command()
@click.argument("chapter_file", type=click.Path(exists=True))
@click.option("--last-summary", default="", help="上一章结尾摘要（可选）")
def polish(chapter_file: str, last_summary: str):
    """[8] 对修订稿进行 LLM 润色，生成 chXX_polished.md。"""
    draft_path = Path(chapter_file)
    # 优先使用 revised 版本，否则用 draft
    revised_path = draft_path.with_name(draft_path.name.replace("_draft", "_revised"))
    source_path = revised_path if revised_path.exists() else draft_path
    text = source_path.read_text(encoding="utf-8")
    click.echo(f"✓ 使用源文件：{source_path.name}（{len(text)} 字）")

    cfg = load_llm_settings()
    try:
        camp = Campaign.load("jl_zheng_zheng")
        wv = load_worldview_for_campaign(camp)
        from trpg2novel.character import load_all_cards
        cards = load_all_cards(camp.character_cards_dir)
        pc_facts = {n: c.atomic_facts for n, c in cards.items() if c.atomic_facts}
    except Exception:
        wv = load_worldview("dnd5e")
        pc_facts = {}

    click.echo(f"⏳ 润色中，使用模型 {cfg.polish.model} …")
    polished = polish_chapter(
        text,
        worldview=wv,
        pc_facts=pc_facts or None,
        last_chapter_summary=last_summary,
        api_key=cfg.polish.api_key,
        base_url=cfg.polish.base_url,
        model=cfg.polish.model,
    )
    out = draft_path.with_name(draft_path.stem.replace("_draft", "") + "_polished.md")
    out.write_text(polished, encoding="utf-8")
    click.echo(f"✓ 润色完成：{out}（{len(polished)} 字）")


def main():
    cli()



if __name__ == "__main__":
    main()

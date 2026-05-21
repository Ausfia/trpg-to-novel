"""一次性迁移脚本：把 v1 扁平 data/ 布局迁移到 v2 多团布局。

迁移目标：
    data/raw_logs/         → data/campaigns/jl_zheng_zheng/raw_logs/
    data/parsed/           → data/campaigns/jl_zheng_zheng/parsed/
    data/meta/*.yaml       → data/campaigns/jl_zheng_zheng/*.yaml
    data/chapters/         → data/campaigns/jl_zheng_zheng/chapters/
    诺菲雅.xlsx (项目根)   → data/campaigns/jl_zheng_zheng/character_cards/

幂等：目标文件已存在则跳过；可重复运行。

用法：
    python scripts/migrate_v1_to_v2.py
    python scripts/migrate_v1_to_v2.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from trpg2novel.campaign import Campaign
from trpg2novel.config import DATA_DIR


CAMPAIGN_ID = "jl_zheng_zheng"
CAMPAIGN_NAME = "巨龙僭政"
CAMPAIGN_SYSTEM = "dnd5e"
CAMPAIGN_PC_LIST = ["雷恩", "丹德莱", "比阿特丽丝", "艾尔莉洁", "诺菲雅", "泰洛尔"]


def _move(src: Path, dst: Path, dry_run: bool) -> str:
    """搬运单个文件。返回状态描述。"""
    if not src.exists():
        return f"  - SKIP {src.relative_to(_ROOT)} （源不存在）"
    if dst.exists():
        return f"  - SKIP {src.relative_to(_ROOT)} → {dst.relative_to(_ROOT)} （目标已存在）"
    if dry_run:
        return f"  - [dry-run] MOVE {src.relative_to(_ROOT)} → {dst.relative_to(_ROOT)}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"  - MOVE {src.relative_to(_ROOT)} → {dst.relative_to(_ROOT)}"


def main():
    parser = argparse.ArgumentParser(description="迁移 v1 数据到 v2 多团布局")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不实际搬运")
    args = parser.parse_args()

    print(f"==> 目标团：{CAMPAIGN_ID}（{CAMPAIGN_NAME}）")

    # 1. 确保 campaign 目录与 campaign.yaml 存在
    camp_root = DATA_DIR / "campaigns" / CAMPAIGN_ID
    if camp_root.exists() and (camp_root / "campaign.yaml").exists():
        print(f"==> Campaign 已存在：{camp_root.relative_to(_ROOT)}")
        camp = Campaign.load(CAMPAIGN_ID)
    else:
        if args.dry_run:
            print(f"==> [dry-run] 将创建 Campaign：{camp_root.relative_to(_ROOT)}")
            # 仍构造一个对象以便后续路径计算
            camp = Campaign(
                id=CAMPAIGN_ID,
                root=camp_root,
                name=CAMPAIGN_NAME,
                system=CAMPAIGN_SYSTEM,
                pc_list=list(CAMPAIGN_PC_LIST),
            )
        else:
            print(f"==> 创建 Campaign：{camp_root.relative_to(_ROOT)}")
            camp = Campaign.create(
                campaign_id=CAMPAIGN_ID,
                name=CAMPAIGN_NAME,
                system=CAMPAIGN_SYSTEM,
                pc_list=CAMPAIGN_PC_LIST,
            )

    # 2. 计算迁移计划
    plan: list[tuple[Path, Path]] = []

    old_raw = DATA_DIR / "raw_logs"
    if old_raw.exists():
        for src in old_raw.iterdir():
            if src.is_file() and src.name != ".gitkeep":
                plan.append((src, camp.raw_logs_dir / src.name))

    old_parsed = DATA_DIR / "parsed"
    if old_parsed.exists():
        for src in old_parsed.iterdir():
            if src.is_file() and src.name != ".gitkeep":
                plan.append((src, camp.parsed_dir / src.name))

    old_meta = DATA_DIR / "meta"
    if (old_meta / "players.yaml").exists():
        plan.append((old_meta / "players.yaml", camp.players_yaml))
    if (old_meta / "story_state.yaml").exists():
        plan.append((old_meta / "story_state.yaml", camp.story_state_yaml))

    old_chapters = DATA_DIR / "chapters"
    if old_chapters.exists():
        for src in old_chapters.iterdir():
            if src.is_file() and src.name != ".gitkeep":
                plan.append((src, camp.chapters_dir / src.name))

    old_pending = DATA_DIR / "pending"
    if old_pending.exists():
        for src in old_pending.iterdir():
            if src.is_file() and src.name != ".gitkeep":
                plan.append((src, camp.pending_dir / src.name))

    old_cards = DATA_DIR / "character_cards"
    if old_cards.exists():
        for src in old_cards.iterdir():
            if src.is_file() and src.name != ".gitkeep":
                plan.append((src, camp.character_cards_dir / src.name))

    # 项目根目录下的 诺菲雅.xlsx → character_cards/
    root_xlsx = _ROOT / "诺菲雅.xlsx"
    if root_xlsx.exists():
        plan.append((root_xlsx, camp.character_cards_dir / "诺菲雅.xlsx"))

    # 3. 执行
    print(f"\n==> 迁移计划（{len(plan)} 项）：")
    moved = 0
    skipped = 0
    for src, dst in plan:
        line = _move(src, dst, args.dry_run)
        print(line)
        if "MOVE" in line and "dry-run" not in line:
            moved += 1
        elif "SKIP" in line:
            skipped += 1

    # 4. 总结
    print(f"\n==> 完成：搬运 {moved} 项，跳过 {skipped} 项")
    if args.dry_run:
        print("==> 这是 dry-run，未实际修改任何文件。去掉 --dry-run 再次运行即可执行。")
    else:
        print(f"==> Campaign 根目录：{camp.root}")
        print("==> 下一步：用 P1② 重构 config.py / pipeline.py / UI 以使用 Campaign。")


if __name__ == "__main__":
    main()

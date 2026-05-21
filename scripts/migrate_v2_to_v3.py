"""一次性迁移脚本：v2 → v3。

v3 新增内容：
    data/campaigns/<id>/knowledge_base/
        sources/           # 用户上传的世界观原文（.md / .txt）
        kb_config.yaml     # embedding 模型 + 分块/检索参数（api_key 留空待填）

迁移动作：
    1. 为每个 campaign 创建 knowledge_base/sources/ 目录。
    2. 若存在 campaigns/<id>/worldview.md（v2 直接注入用），且内容非空模板，复制到
       knowledge_base/sources/worldview.md（保留原文件向后兼容，不删除）。
    3. 若不存在 kb_config.yaml，生成模板（api_key 留空）。

幂等：目标文件已存在则跳过。

用法：
    python scripts/migrate_v2_to_v3.py
    python scripts/migrate_v2_to_v3.py --dry-run
    python scripts/migrate_v2_to_v3.py --campaign jl_zheng_zheng
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
from trpg2novel.rag.config import KBConfig, save_kb_config


# 把"几乎空的 worldview.md 模板"视为无内容（避免把示例提示词喂给 embedder）
_TEMPLATE_HINTS = (
    "在此自由记录本团的世界观",
    "（在此",
)


def _is_template_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    # 去掉所有标题/空行后剩余正文不到 50 字符，视为空模板
    body_lines = [
        ln for ln in stripped.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    body = "\n".join(body_lines).strip()
    if len(body) < 50:
        return True
    return any(hint in body for hint in _TEMPLATE_HINTS) and len(body) < 200


def _migrate_one(camp: Campaign, dry_run: bool) -> dict[str, int]:
    stats = {"sources_copied": 0, "kb_config_created": 0, "skipped": 0}

    kb_dir = camp.knowledge_base_dir
    sources_dir = kb_dir / "sources"
    if not sources_dir.exists():
        if dry_run:
            print(f"  - [dry-run] MKDIR {sources_dir.relative_to(_ROOT)}")
        else:
            sources_dir.mkdir(parents=True, exist_ok=True)
            print(f"  - MKDIR {sources_dir.relative_to(_ROOT)}")
    else:
        print(f"  - SKIP {sources_dir.relative_to(_ROOT)} （目录已存在）")

    # 1. worldview.md → sources/worldview.md
    src_wv = camp.root / "worldview.md"
    dst_wv = sources_dir / "worldview.md"
    if src_wv.exists():
        text = src_wv.read_text(encoding="utf-8")
        if _is_template_only(text):
            print(f"  - SKIP {src_wv.relative_to(_ROOT)} （内容为空/仅模板）")
            stats["skipped"] += 1
        elif dst_wv.exists():
            print(f"  - SKIP {dst_wv.relative_to(_ROOT)} （目标已存在）")
            stats["skipped"] += 1
        else:
            if dry_run:
                print(f"  - [dry-run] COPY {src_wv.relative_to(_ROOT)} → {dst_wv.relative_to(_ROOT)}")
            else:
                shutil.copy2(str(src_wv), str(dst_wv))
                print(f"  - COPY {src_wv.relative_to(_ROOT)} → {dst_wv.relative_to(_ROOT)}")
            stats["sources_copied"] += 1
    else:
        print(f"  - SKIP worldview.md （源不存在）")

    # 2. kb_config.yaml
    kb_cfg_path = camp.kb_config_yaml
    if kb_cfg_path.exists():
        print(f"  - SKIP {kb_cfg_path.relative_to(_ROOT)} （已存在）")
        stats["skipped"] += 1
    else:
        if dry_run:
            print(f"  - [dry-run] CREATE {kb_cfg_path.relative_to(_ROOT)}")
        else:
            save_kb_config(KBConfig(), kb_cfg_path)
            print(f"  - CREATE {kb_cfg_path.relative_to(_ROOT)}（api_key 留空，待用户在 UI 中填写）")
        stats["kb_config_created"] += 1

    return stats


def _list_campaigns(only: str | None) -> list[Campaign]:
    camps_root = DATA_DIR / "campaigns"
    if not camps_root.exists():
        return []
    out: list[Campaign] = []
    for child in sorted(camps_root.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "campaign.yaml").exists():
            continue
        if only and child.name != only:
            continue
        try:
            out.append(Campaign.load(child.name))
        except Exception as e:
            print(f"[WARN] 跳过 {child.name}：{e}")
    return out


def main():
    parser = argparse.ArgumentParser(description="迁移 v2 → v3：为每个 campaign 创建 knowledge_base/")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不实际修改")
    parser.add_argument("--campaign", default=None, help="只迁移指定 campaign ID（默认全部）")
    args = parser.parse_args()

    camps = _list_campaigns(args.campaign)
    if not camps:
        print("==> 未找到任何 campaign（data/campaigns/ 下没有合法目录）")
        return

    print(f"==> 计划迁移 {len(camps)} 个 campaign：{', '.join(c.id for c in camps)}")
    totals = {"sources_copied": 0, "kb_config_created": 0, "skipped": 0}
    for camp in camps:
        print(f"\n==> [{camp.id}] {camp.name}")
        stats = _migrate_one(camp, args.dry_run)
        for k, v in stats.items():
            totals[k] += v

    print(
        f"\n==> 完成：复制 {totals['sources_copied']} 份 source，"
        f"新建 {totals['kb_config_created']} 个 kb_config.yaml，跳过 {totals['skipped']} 项"
    )
    if args.dry_run:
        print("==> 这是 dry-run，未实际修改任何文件。去掉 --dry-run 再次运行即可执行。")
        return

    print("\n==> 下一步：")
    print("    1. 打开 WebUI → 知识库 Tab，填入 embedding 服务的 api_key / base_url / model。")
    print("    2. 在 sources/ 下补充你想注入的世界观原文（.md / .txt）。")
    print("    3. 点击「重建索引」按钮，让 RAG 生效。")


if __name__ == "__main__":
    main()

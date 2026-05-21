"""Campaign — 多团目录抽象层。

每个团对应 data/campaigns/<id>/ 下的一个目录。Campaign 对象统一提供路径计算，
替代 v1 里散在 config.py 的全局路径常量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from trpg2novel.config import DATA_DIR


CAMPAIGNS_DIR = DATA_DIR / "campaigns"
SYSTEMS_DIR = DATA_DIR / "systems"


@dataclass
class Campaign:
    """一个跑团（战役/团）。"""

    id: str
    root: Path
    name: str = ""
    system: str = "dnd5e"
    created_at: str = ""
    pc_list: list[str] = field(default_factory=list)
    notes: str = ""

    # ---------- 路径（统一从 root 派生）----------
    @property
    def campaign_yaml(self) -> Path:
        return self.root / "campaign.yaml"

    @property
    def worldview_md(self) -> Path:
        return self.root / "worldview.md"

    @property
    def players_yaml(self) -> Path:
        return self.root / "players.yaml"

    @property
    def story_state_yaml(self) -> Path:
        return self.root / "story_state.yaml"

    @property
    def chapter_index_yaml(self) -> Path:
        return self.root / "chapter_index.yaml"

    @property
    def raw_logs_dir(self) -> Path:
        return self.root / "raw_logs"

    @property
    def character_cards_dir(self) -> Path:
        return self.root / "character_cards"

    @property
    def parsed_dir(self) -> Path:
        return self.root / "parsed"

    @property
    def pending_dir(self) -> Path:
        return self.root / "pending"

    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    @property
    def knowledge_base_dir(self) -> Path:
        return self.root / "knowledge_base"

    @property
    def kb_config_yaml(self) -> Path:
        return self.knowledge_base_dir / "kb_config.yaml"

    # ---------- 派生信息 ----------
    def list_sessions(self) -> list[str]:
        """已 segment 完的场次（按 scenes.json 存在判定）。"""
        return sorted(p.stem.split(".")[0] for p in self.parsed_dir.glob("*.scenes.json"))

    def list_raw_logs(self) -> list[Path]:
        return sorted(self.raw_logs_dir.glob("*.md"))

    def list_chapters(self) -> list[Path]:
        return sorted(self.chapters_dir.glob("ch*_draft.md"))

    def next_session_id(self) -> str:
        """根据 raw_logs/ 已有 .md 数量推一个 s{NN}。"""
        existing = [p.stem for p in self.list_raw_logs()]
        n = len(existing) + 1
        return f"s{n:02d}"

    # ---------- 读写 ----------
    def save_meta(self) -> None:
        """把 campaign 元数据写回 campaign.yaml。"""
        self.root.mkdir(parents=True, exist_ok=True)
        data = {
            "id": self.id,
            "name": self.name,
            "system": self.system,
            "created_at": self.created_at,
            "pc_list": list(self.pc_list),
            "notes": self.notes,
        }
        self.campaign_yaml.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def ensure_dirs(self) -> None:
        for d in (
            self.raw_logs_dir,
            self.character_cards_dir,
            self.parsed_dir,
            self.pending_dir,
            self.chapters_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ---------- 工厂方法 ----------
    @classmethod
    def load(cls, campaign_id: str) -> "Campaign":
        root = CAMPAIGNS_DIR / campaign_id
        meta_path = root / "campaign.yaml"
        if not meta_path.exists():
            raise FileNotFoundError(f"找不到团：{meta_path}")
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        return cls(
            id=data.get("id", campaign_id),
            root=root,
            name=data.get("name", campaign_id),
            system=data.get("system", "dnd5e"),
            created_at=data.get("created_at", ""),
            pc_list=list(data.get("pc_list", [])),
            notes=data.get("notes", ""),
        )

    @classmethod
    def create(
        cls,
        campaign_id: str,
        name: str,
        system: str = "dnd5e",
        pc_list: list[str] | None = None,
    ) -> "Campaign":
        root = CAMPAIGNS_DIR / campaign_id
        camp = cls(
            id=campaign_id,
            root=root,
            name=name,
            system=system,
            created_at=datetime.now().isoformat(timespec="seconds"),
            pc_list=list(pc_list or []),
        )
        camp.ensure_dirs()
        camp.save_meta()
        # 占位 worldview.md
        if not camp.worldview_md.exists():
            camp.worldview_md.write_text(
                f"# {name} 世界观备忘\n\n（在此自由记录本团的世界观、势力、地点、历史。"
                "这些文字会作为参考注入到章节起草 prompt 中。）\n",
                encoding="utf-8",
            )
        return camp

    @classmethod
    def list_all(cls) -> list["Campaign"]:
        if not CAMPAIGNS_DIR.exists():
            return []
        out: list[Campaign] = []
        for sub in sorted(CAMPAIGNS_DIR.iterdir()):
            if sub.is_dir() and (sub / "campaign.yaml").exists():
                try:
                    out.append(cls.load(sub.name))
                except Exception:
                    continue
        return out

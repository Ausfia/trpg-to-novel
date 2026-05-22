"""trpg-to-novel — Streamlit 入口（瘦身骨架）。

只做四件事：
1. `st.set_page_config` 全局配置
2. 注入主题 CSS
3. 渲染顶栏（logo + 团选择 + 健康徽章）
4. 用 `st.navigation` 装配多页

每个具体页面放在 `ui/pages/` 下，共用工具放在 `ui/shared.py`。
启动：`python -m streamlit run ui/app.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from ui.shared import inject_theme, render_topbar  # noqa: E402


st.set_page_config(
    page_title="trpg-to-novel · 跑团 → 小说",
    page_icon="🎲",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "trpg-to-novel — 把跑团日志炼成小说稿子。\n\nhttps://github.com/your-org/trpg-to-novel",
    },
)

inject_theme()
render_topbar()


# ---------------------------------------------------------------------------
# 导航装配
# ---------------------------------------------------------------------------

# 使用绝对路径，避免 Streamlit 以主脚本目录为基准解析出错
_PAGES_DIR = Path(__file__).parent / "pages"


def _page(file: str, *, title: str, icon: str, default: bool = False) -> st.Page:
    return st.Page(_PAGES_DIR / file, title=title, icon=icon, default=default)


nav = st.navigation(
    {
        "📊 总览": [
            _page("dashboard.py", title="仪表盘", icon="📊", default=True),
        ],
        "✍️ 工作流": [
            _page("pipeline.py", title="解析流程", icon="✍️"),
            _page("review.py", title="章节审稿", icon="📖"),
            _page("polish.py", title="润色对比", icon="✨"),
        ],
        "📚 资料库": [
            _page("cards.py", title="人物卡", icon="🎭"),
            _page("worldview.py", title="世界观", icon="🌍"),
            _page("kb.py", title="知识库", icon="📚"),
        ],
        "⚙️ 设置": [
            _page("campaigns.py", title="团管理", icon="🏛️"),
            _page("llm_config.py", title="LLM 配置", icon="⚙️"),
        ],
    }
)

nav.run()

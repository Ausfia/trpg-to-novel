"""🏛️ 团管理 — 团列表/新建/删除 + players.yaml 表单编辑。"""

from __future__ import annotations

import re
import time

import streamlit as st
import yaml

from ui.shared import require_campaign

try:
    from trpg2novel.campaign import Campaign
    from trpg2novel.config import SYSTEMS_DIR
except ImportError as e:
    st.error(f"模块加载失败：{e}")
    st.stop()

st.title("🏛️ 团管理")
st.caption("新建/切换/删除团，以及编辑团的 players.yaml 人员配置。")

all_camps = Campaign.list_all()
camp_ids = [c.id for c in all_camps]

# ---------------------------------------------------------------------------
# Tab: 团列表 / 新建 / Players
# ---------------------------------------------------------------------------

tab_list, tab_new, tab_players = st.tabs(["📋 团列表", "➕ 新建团", "👥 Players 配置"])


# ===== Tab 1: 团列表 =====

with tab_list:
    if not all_camps:
        st.info("还没有任何团。到「➕ 新建团」标签创建第一个。")
    else:
        for camp in all_camps:
            is_current = st.session_state.get("selected_campaign_id") == camp.id
            cols = st.columns([4, 2, 1])
            with cols[0]:
                flag = " ← 当前" if is_current else ""
                st.markdown(f"**{camp.name}**{flag}  `{camp.id}`  —  {camp.system}")
            with cols[1]:
                if not is_current:
                    if st.button("切换到此团", key=f"switch_{camp.id}", width="stretch"):
                        st.session_state["selected_campaign_id"] = camp.id
                        st.rerun()
                else:
                    st.markdown('<div style="margin-top:8px"><em>（当前团）</em></div>', unsafe_allow_html=True)
            with cols[2]:
                if not is_current:
                    if st.button("🗑", key=f"del_{camp.id}", help="删除此团（需二次确认）"):
                        st.session_state["del_camp_confirm"] = camp.id

            if st.session_state.get("del_camp_confirm") == camp.id:
                st.warning(
                    f"⚠ 即将删除团 **{camp.name}** (`{camp.id}`) 及其所有文件，不可撤销！"
                )
                dc1, dc2, _ = st.columns([1, 1, 4])
                if dc1.button("确认删除", type="primary", key=f"del_camp_ok_{camp.id}"):
                    try:
                        import shutil
                        shutil.rmtree(camp.root)
                        st.session_state.pop("del_camp_confirm", None)
                        if st.session_state.get("selected_campaign_id") == camp.id:
                            st.session_state.pop("selected_campaign_id", None)
                        st.success(f"已删除：{camp.name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"删除失败：{e}")
                if dc2.button("取消", key=f"del_camp_cancel_{camp.id}"):
                    st.session_state.pop("del_camp_confirm", None)
                    st.rerun()


# ===== Tab 2: 新建团 =====

with tab_new:
    with st.form("form_new_campaign"):
        new_name = st.text_input("团名称 *", placeholder="巨龙僭政 2")
        new_system = st.selectbox("规则系统", ["dnd5e"])
        st.caption("团 ID 由系统根据名称自动生成（字母/数字/下划线），可在下方预览。")
        submitted = st.form_submit_button("创建", type="primary")

    # 名称预览自动 ID
    if new_name.strip():
        # 中文→拼音首字母取不到，退而用 unicode 编号兜底；取英数 + _
        raw = re.sub(r"[^\w一-鿿]", "_", new_name.strip().lower())
        # 去掉连续下划线
        auto_id = re.sub(r"_+", "_", raw).strip("_")
        # 追加毫秒后 4 位保唯一
        auto_id = f"{auto_id}_{int(time.time() * 1000) % 10000:04d}"
        st.caption(f"预计 ID：`{auto_id}`")

    if submitted:
        if not new_name.strip():
            st.error("名称不能为空")
        else:
            raw2 = re.sub(r"[^\w一-鿿]", "_", new_name.strip().lower())
            base_id = re.sub(r"_+", "_", raw2).strip("_")
            gen_id = f"{base_id}_{int(time.time() * 1000) % 10000:04d}"
            try:
                Campaign.create(
                    campaign_id=gen_id,
                    name=new_name.strip(),
                    system=new_system,
                )
                st.session_state["selected_campaign_id"] = gen_id
                st.success(f"已创建团：{new_name.strip()}（ID: {gen_id}）")
                st.rerun()
            except Exception as e:
                st.error(f"创建失败：{e}")


# ===== Tab 3: Players 配置 =====

with tab_players:
    camp = require_campaign()
    if camp is None:
        st.stop()

    st.markdown(f"当前团：**{camp.name}** `{camp.id}`")
    st.caption("players.yaml 记录参与本团的 PC 列表、DM handle 与骰娘 handles，用于 parse/classify 阶段分类事件。")

    # 读取/初始化 session_state
    sk_players = f"players_form_{camp.id}_players"
    sk_dm = f"players_form_{camp.id}_dm"
    sk_bots = f"players_form_{camp.id}_bots"
    sk_loaded = f"players_form_{camp.id}_loaded"

    if not st.session_state.get(sk_loaded, False):
        if camp.players_yaml.exists():
            try:
                pdata = yaml.safe_load(camp.players_yaml.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                pdata = {}
        else:
            pdata = {}
        st.session_state[sk_players] = [
            {
                "name": p.get("name", ""),
                "role": p.get("role", "pc"),
                "aliases": ", ".join(p.get("aliases") or []),
                "user_is": bool(p.get("user_is", False)),
            }
            for p in (pdata.get("players") or [])
        ]
        st.session_state[sk_dm] = (pdata.get("dm") or {}).get("handle", "")
        st.session_state[sk_bots] = ", ".join(pdata.get("known_bots") or [])
        st.session_state[sk_loaded] = True

    if st.button("↺ 从文件重新加载", key=f"btn_reload_players_{camp.id}"):
        st.session_state[sk_loaded] = False
        st.rerun()

    st.markdown("**PC 列表**")
    players: list[dict] = st.session_state[sk_players]

    to_delete = -1
    for i, p in enumerate(players):
        cols = st.columns([3, 2, 4, 1, 1])
        p["name"] = cols[0].text_input(
            "姓名", value=p["name"], key=f"pl_name_{camp.id}_{i}", label_visibility="collapsed",
            placeholder="角色名",
        )
        p["role"] = cols[1].selectbox(
            "角色", ["pc", "npc"],
            index=0 if p["role"] == "pc" else 1,
            key=f"pl_role_{camp.id}_{i}",
            label_visibility="collapsed",
        )
        p["aliases"] = cols[2].text_input(
            "别名（逗号分隔）", value=p["aliases"], key=f"pl_alias_{camp.id}_{i}",
            label_visibility="collapsed", placeholder="别名1, 别名2",
        )
        p["user_is"] = cols[3].checkbox(
            "我", value=p["user_is"], key=f"pl_user_{camp.id}_{i}",
            help="是否是你自己扮演的角色",
        )
        if cols[4].button("✕", key=f"pl_del_{camp.id}_{i}", help="删除这一行"):
            to_delete = i

    if to_delete >= 0:
        players.pop(to_delete)
        st.rerun()

    if st.button("+ 添加 PC", key=f"btn_add_pc_{camp.id}"):
        players.append({"name": "", "role": "pc", "aliases": "", "user_is": False})
        st.rerun()

    st.markdown("**DM**")
    st.session_state[sk_dm] = st.text_input(
        "DM 在日志中的发言人 handle",
        value=st.session_state[sk_dm],
        key=f"pl_dm_input_{camp.id}",
        placeholder="如 DM、地下城主",
    )

    st.markdown("**已知骰娘 handles**（逗号分隔）")
    st.session_state[sk_bots] = st.text_input(
        "known_bots",
        value=st.session_state[sk_bots],
        key=f"pl_bots_input_{camp.id}",
        label_visibility="collapsed",
        placeholder="如 JCC-Dice, Saki",
        help="用于 parse 时过滤骰娘输出",
    )

    if st.button("保存 players.yaml", key=f"btn_save_players_form_{camp.id}", type="primary"):
        out_players = []
        for p in players:
            name_v = (p["name"] or "").strip()
            if not name_v:
                continue
            aliases = [a.strip() for a in (p["aliases"] or "").split(",") if a.strip()]
            entry: dict = {"name": name_v, "role": p["role"]}
            if aliases:
                entry["aliases"] = aliases
            if p["user_is"]:
                entry["user_is"] = True
            out_players.append(entry)

        out_bots = [b.strip() for b in (st.session_state[sk_bots] or "").split(",") if b.strip()]
        out = {
            "players": out_players,
            "dm": {"handle": (st.session_state[sk_dm] or "").strip()},
            "known_bots": out_bots,
        }
        camp.players_yaml.write_text(
            yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        st.success(
            f"已保存：{camp.players_yaml.name}"
            f"（{len(out_players)} 个角色 / {len(out_bots)} 个骰娘）"
        )

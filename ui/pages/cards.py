"""🎭 人物卡管理 — 人物卡列表 / 表单编辑 / xlsx 导入 / 删除。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
import yaml

from ui.shared import require_campaign

st.title("🎭 人物卡管理")
st.caption("新建/编辑/删除 PC 卡，或从旧 xlsx 批量导入。")

camp = require_campaign()
if camp is None:
    st.stop()

try:
    from trpg2novel.character.card_loader import (
        card_to_dict,
        import_from_xlsx,
        load_all_cards,
    )
except ImportError as e:
    st.error(f"人物卡模块加载失败：{e}")
    st.stop()

cards = load_all_cards(camp.character_cards_dir)

left_col, right_col = st.columns([1, 2], gap="large")


# ---------------------------------------------------------------------------
# 左列：卡片列表 + 工具
# ---------------------------------------------------------------------------

with left_col:
    st.markdown("#### 已有卡片")
    if not cards:
        st.caption("暂无人物卡（YAML 格式）")
    for pc_name, card in cards.items():
        c1, c2 = st.columns([3, 1])
        race_class = ""
        if hasattr(card, "race") and card.race:
            race_class += card.race
        if hasattr(card, "class_name") and card.class_name:
            race_class += card.class_name
        c1.markdown(f"**{pc_name}**" + (f" ({race_class})" if race_class else ""))
        if c2.button("编辑", key=f"edit_{pc_name}"):
            st.session_state["card_edit_name"] = pc_name
            st.session_state["card_edit_data"] = card_to_dict(card)
            st.rerun()

    st.markdown("---")
    if st.button("+ 新建人物卡", use_container_width=True, key="btn_new_card"):
        st.session_state["card_edit_name"] = None
        st.session_state["card_edit_data"] = {}
        st.rerun()

    st.markdown("#### 从 xlsx 导入（一次性）")
    xlsx_up = st.file_uploader("上传 .xlsx 人物卡", type=["xlsx"], key="xlsx_import")
    if xlsx_up:
        if st.button("解析并预填表单", key="btn_parse_xlsx"):
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
                tf.write(xlsx_up.read())
                tmp_path = Path(tf.name)
            try:
                d = import_from_xlsx(tmp_path)
                st.session_state["card_edit_name"] = None
                st.session_state["card_edit_data"] = d
                st.success("已解析，请在右侧表单中检查并保存。")
            except Exception as e:
                st.error(f"解析失败：{e}")
            finally:
                tmp_path.unlink(missing_ok=True)

    if cards:
        st.markdown("#### 删除")
        del_name = st.selectbox("要删除的卡", list(cards.keys()), key="del_card_name")
        if st.button("确认删除", key="btn_del_card", type="secondary"):
            yaml_path = camp.character_cards_dir / f"{del_name}.yaml"
            if yaml_path.exists():
                yaml_path.unlink()
                if st.session_state.get("card_edit_name") == del_name:
                    st.session_state.pop("card_edit_name", None)
                    st.session_state.pop("card_edit_data", None)
                st.success(f"已删除：{del_name}")
                st.rerun()


# ---------------------------------------------------------------------------
# 右列：编辑表单
# ---------------------------------------------------------------------------

with right_col:
    if "card_edit_data" not in st.session_state:
        st.info("点击左侧「+ 新建人物卡」或选一张卡编辑。")
    else:
        edit_name = st.session_state.get("card_edit_name")
        data: dict = dict(st.session_state.get("card_edit_data", {}))
        title = f"编辑：{edit_name}" if edit_name else "新建人物卡"
        st.markdown(f"#### {title}")

        with st.form("card_form"):
            st.markdown("**基本信息**")
            r1, r2 = st.columns(2)
            name = r1.text_input("角色名 *", value=data.get("name", ""))
            player_handle = r2.text_input("玩家 handle（可选）", value=data.get("player_handle", ""))
            r3, r4, r5 = st.columns(3)
            race = r3.text_input("种族", value=data.get("race", ""))
            class_name = r4.text_input("职业", value=data.get("class", data.get("class_name", "")))
            age = r5.text_input("年龄", value=data.get("age", ""))
            r6, r7 = st.columns(2)
            gender = r6.text_input("性别", value=data.get("gender", ""))
            homeland = r7.text_input("故乡", value=data.get("homeland", ""))

            st.markdown("**外貌与气质**")
            appearance = st.text_area("外貌描述", value=data.get("appearance", ""), height=100)
            personality = st.text_area("个性", value=data.get("personality", ""), height=60)
            ideal_val = st.text_input("理念", value=data.get("ideal", ""))
            bond = st.text_input("羁绊", value=data.get("bond", ""))
            flaw = st.text_input("缺陷", value=data.get("flaw", ""))

            st.markdown("**背景故事**")
            background_story = st.text_area("背景故事", value=data.get("background_story", ""), height=150)

            st.markdown("**关键特征 key_traits**（每行一条，注入起草 prompt 的核心）")
            kt_default = "\n".join(data.get("key_traits") or [])
            kt_text = st.text_area(
                "关键特征（每行一条）",
                value=kt_default,
                height=140,
                help="建议 6–10 条简短陈述，描述对叙事最关键的事实",
            )

            st.markdown("**台词样例 voice_examples**（可选，每行一句）")
            ve_default = "\n".join(data.get("voice_examples") or [])
            ve_text = st.text_area("台词样例", value=ve_default, height=80)

            st.markdown("---")
            st.markdown("**角色退出设置**")
            _left_val = data.get("left_after_session") or ""
            _exit_val = data.get("exit_story") or ""
            if _left_val:
                st.markdown(
                    '<span class="tn-badge tn-badge-warn">⚠ 已退团</span>  '
                    f'退出场次：`{_left_val}`',
                    unsafe_allow_html=True,
                )
            re1, re2 = st.columns([1, 2])
            left_after_session = re1.text_input(
                "退出场次（填写后该角色在此场次之后不再参与 Draft）",
                value=_left_val,
                placeholder="如：s05",
                help="在某场结束后该角色离队；留空表示仍在团",
            )
            exit_story = re2.text_input(
                "离场方向（LLM 参考此方向为角色安排结局）",
                value=_exit_val,
                placeholder="如：与队伍告别后独自踏上寻亲之路",
            )

            submitted = st.form_submit_button("保存人物卡", type="primary")

        if submitted:
            if not name.strip():
                st.error("角色名不能为空")
            else:
                key_traits = [ln.strip() for ln in kt_text.splitlines() if ln.strip()]
                voice_examples = [ln.strip() for ln in ve_text.splitlines() if ln.strip()]
                save_dict: dict = {
                    "name": name.strip(),
                    "player_handle": player_handle.strip(),
                    "race": race.strip(),
                    "class": class_name.strip(),
                    "age": age.strip(),
                    "gender": gender.strip(),
                    "homeland": homeland.strip(),
                    "appearance": appearance.strip(),
                    "personality": personality.strip(),
                    "ideal": ideal_val.strip(),
                    "bond": bond.strip(),
                    "flaw": flaw.strip(),
                    "background_story": background_story.strip(),
                    "key_traits": key_traits,
                    "voice_examples": voice_examples,
                }
                if left_after_session.strip():
                    save_dict["left_after_session"] = left_after_session.strip()
                if exit_story.strip():
                    save_dict["exit_story"] = exit_story.strip()
                camp.character_cards_dir.mkdir(parents=True, exist_ok=True)
                save_path = camp.character_cards_dir / f"{name.strip()}.yaml"
                if edit_name and edit_name != name.strip():
                    old_path = camp.character_cards_dir / f"{edit_name}.yaml"
                    if old_path.exists():
                        old_path.unlink()
                save_path.write_text(
                    yaml.dump(save_dict, allow_unicode=True, sort_keys=False, default_flow_style=False),
                    encoding="utf-8",
                )
                st.session_state.pop("card_edit_name", None)
                st.session_state.pop("card_edit_data", None)
                st.success(f"已保存：{save_path.name}")
                st.rerun()

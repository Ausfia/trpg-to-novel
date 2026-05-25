"""🎭 人物卡管理 — 人物卡列表 / 表单编辑 / xlsx 导入 / 删除。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
import yaml

from ui.shared import read_vision_config, require_campaign

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

_PORTRAIT_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_PORTRAIT_PROMPT = (
    "请详细描述图片中角色的外貌特征，适合作为奇幻小说创作的参考素材。"
    "按以下要素描述：\n"
    "- 面部：肤色、眼睛（颜色/形状）、眉型、整体轮廓\n"
    "- 发型与发色：颜色、长短、造型风格\n"
    "- 服饰与装备：衣物款式、颜色、材质感，武器或特殊道具\n"
    "- 体型与气质：身材、整体给人的感觉\n"
    "- 特殊细节：纹身、疤痕、饰品、非人类特征等\n"
    "只做客观描述，不加评价，直接用第三人称叙述，使用中文，200–400字。"
)


def _find_portrait(name: str) -> Path | None:
    for ext in _PORTRAIT_EXTS:
        p = camp.character_cards_dir / f"{name}_portrait{ext}"
        if p.exists():
            return p
    return None


def _save_portrait(name: str, data: bytes, ext: str) -> Path:
    for old_ext in _PORTRAIT_EXTS:
        old = camp.character_cards_dir / f"{name}_portrait{old_ext}"
        if old.exists():
            old.unlink()
    path = camp.character_cards_dir / f"{name}_portrait{ext}"
    path.write_bytes(data)
    return path

left_col, right_col = st.columns([1, 2], gap="large")


# ---------------------------------------------------------------------------
# 左列：卡片列表 + 工具
# ---------------------------------------------------------------------------

with left_col:
    st.markdown("#### 已有卡片")
    if not cards:
        st.caption("暂无人物卡（YAML 格式）")
    for pc_name, card in cards.items():
        portrait = _find_portrait(pc_name)
        c_img, c_info, c_btn = st.columns([1, 3, 1])
        if portrait:
            c_img.image(str(portrait), width="stretch")
        else:
            c_img.markdown(
                '<div style="width:100%;aspect-ratio:1;background:var(--color-bg-soft);'
                'border-radius:8px;display:flex;align-items:center;justify-content:center;'
                'font-size:24px">🧑</div>',
                unsafe_allow_html=True,
            )
        race_class = ""
        if hasattr(card, "race") and card.race:
            race_class += card.race
        if hasattr(card, "class_name") and card.class_name:
            race_class += " " + card.class_name
        status_badges = ""
        if card.first_appearance_session:
            status_badges += ' <span class="tn-badge" style="font-size:10px;background:#555">未入场</span>'
        if card.left_after_session:
            status_badges += ' <span class="tn-badge tn-badge-warn" style="font-size:10px">退团</span>'
        c_info.markdown(
            f"**{pc_name}**{status_badges}" + (f"  \n{race_class.strip()}" if race_class else ""),
            unsafe_allow_html=True,
        )
        if c_btn.button("编辑", key=f"edit_{pc_name}"):
            st.session_state["card_edit_name"] = pc_name
            st.session_state["card_edit_data"] = card_to_dict(card)
            st.rerun()

    st.markdown("---")
    if st.button("+ 新建人物卡", width="stretch", key="btn_new_card"):
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

        # ---- 读取存档里 LLM 用的 config（detect 优先，fallback draft）----
        def _pick_llm_cfg() -> dict | None:
            from ui.shared import read_env, stage_value
            env = read_env()
            for stage in ("detect", "draft"):
                ak = stage_value(env, stage, "api_key")
                if ak.strip():
                    return {
                        "api_key": ak,
                        "base_url": stage_value(env, stage, "base_url"),
                        "model": stage_value(env, stage, "model"),
                    }
            return None

        def _do_generate_traits(card_dict: dict) -> list[str]:
            cfg = _pick_llm_cfg()
            if not cfg:
                raise RuntimeError("请先在「⚙️ LLM 配置」页配置至少一个阶段的 API Key。")
            from trpg2novel.character.traits_gen import generate_key_traits
            kb = None
            try:
                from trpg2novel.rag import KnowledgeBase, load_kb_config
                kb_cfg = load_kb_config(camp.kb_config_yaml)
                if kb_cfg.is_configured():
                    _kb = KnowledgeBase.open(camp.knowledge_base_dir, kb_cfg)
                    if _kb.count_chunks() > 0:
                        kb = _kb
            except Exception:
                pass
            return generate_key_traits(card_dict, cfg["api_key"], cfg["base_url"], cfg["model"], kb=kb)

        # ---- 关键特征只读展示（保存后自动更新）----
        cur_traits: list[str] = data.get("key_traits") or []
        kt_col1, kt_col2 = st.columns([4, 1])
        kt_col1.markdown("**🔑 关键特征**（自动生成，保存人物卡时更新）")
        if cur_traits:
            for _t in cur_traits:
                st.markdown(f"- {_t}")
        else:
            st.caption("尚未生成，保存人物卡后自动调用 LLM 生成。")
        if edit_name and kt_col2.button("↺ 重新生成", key=f"btn_regen_traits_{edit_name}", width="stretch"):
            _yaml_p = camp.character_cards_dir / f"{edit_name}.yaml"
            if _yaml_p.exists():
                with st.spinner("生成关键特征中…"):
                    try:
                        _cdata = yaml.safe_load(_yaml_p.read_text(encoding="utf-8")) or {}
                        new_traits = _do_generate_traits(_cdata)
                        _cdata["key_traits"] = new_traits
                        _yaml_p.write_text(
                            yaml.dump(_cdata, allow_unicode=True, sort_keys=False, default_flow_style=False),
                            encoding="utf-8",
                        )
                        st.session_state["card_edit_data"] = _cdata
                        st.success(f"已生成 {len(new_traits)} 条关键特征")
                        st.rerun()
                    except Exception as _e:
                        st.error(f"生成失败：{_e}")
        st.markdown("---")

        with st.form("card_form"):
            st.markdown("**基本信息**")
            r1, r2 = st.columns(2)
            name = r1.text_input("角色名 *", value=data.get("name", ""))

            # 别名字段（替代旧的 player_handle）
            aliases_default = "\n".join(data.get("aliases") or [])
            aliases_text = r2.text_area(
                "别名（可选，每行一个）",
                value=aliases_default,
                height=60,
                help="填入玩家在群聊中可能使用的昵称，每行一个。解析时会自动识别这些别名。"
            )

            r3, r4, r5, r6 = st.columns(4)
            race = r3.text_input("种族", value=data.get("race", ""))
            subrace = r4.text_input("亚种（可选）", value=data.get("subrace", ""))
            class_name = r5.text_input("职业", value=data.get("class", data.get("class_name", "")))
            subclass = r6.text_input("子职（可选）", value=data.get("subclass", ""))
            r7, r8, r9 = st.columns(3)
            age = r7.text_input("年龄", value=data.get("age", ""))
            gender = r8.text_input("性别", value=data.get("gender", ""))
            homeland = r9.text_input("故乡", value=data.get("homeland", ""))

            st.markdown("**外貌与气质**")
            appearance = st.text_area("外貌描述", value=data.get("appearance", ""), height=100)
            personality = st.text_area("个性", value=data.get("personality", ""), height=60)
            ideal_val = st.text_input("理念", value=data.get("ideal", ""))
            bond = st.text_input("羁绊", value=data.get("bond", ""))
            flaw = st.text_input("缺陷", value=data.get("flaw", ""))

            st.markdown("**背景故事**")
            background_story = st.text_area("背景故事", value=data.get("background_story", ""), height=150)

            special_background = st.text_area(
                "DM 特殊背景（可选）",
                value=data.get("special_background", ""),
                height=80,
                help="DM 专属额外设定，注入 LLM prompt 并影响关键特征生成，不在公开文档展示",
            )

            st.markdown("**台词样例 voice_examples**（可选，每行一句）")
            ve_default = "\n".join(data.get("voice_examples") or [])
            ve_text = st.text_area("台词样例", value=ve_default, height=80,
                                   help="每行一条例句，帮助 AI 模仿角色说话风格")

            st.markdown("---")
            st.markdown("**角色入场设置**")
            _first_val = data.get("first_appearance_session") or ""
            if _first_val:
                st.markdown(
                    '<span class="tn-badge" style="background:#555">📥 待入场</span>  '
                    f'入场场次：`{_first_val}`',
                    unsafe_allow_html=True,
                )
            fe1, _ = st.columns([1, 2])
            first_appearance_session = fe1.text_input(
                "入场场次（该角色自此场次起才参与故事；之前场次自动标记为未入场）",
                value=_first_val,
                placeholder="如：s04",
                help="留空表示角色从 s01 即在场",
            )

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
                voice_examples = [ln.strip() for ln in ve_text.splitlines() if ln.strip()]
                aliases = [ln.strip() for ln in aliases_text.splitlines() if ln.strip()]
                new_name = name.strip()
                save_dict: dict = {
                    "name": new_name,
                    "aliases": aliases,
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
                    "key_traits": data.get("key_traits") or [],  # carry over until regen
                    "voice_examples": voice_examples,
                }
                if subrace.strip():
                    save_dict["subrace"] = subrace.strip()
                if subclass.strip():
                    save_dict["subclass"] = subclass.strip()
                if special_background.strip():
                    save_dict["special_background"] = special_background.strip()
                if first_appearance_session.strip():
                    save_dict["first_appearance_session"] = first_appearance_session.strip()
                if left_after_session.strip():
                    save_dict["left_after_session"] = left_after_session.strip()
                if exit_story.strip():
                    save_dict["exit_story"] = exit_story.strip()
                # Preserve appearance_ai
                if edit_name:
                    _ex_yaml = camp.character_cards_dir / f"{edit_name}.yaml"
                    if _ex_yaml.exists():
                        _ex = yaml.safe_load(_ex_yaml.read_text(encoding="utf-8")) or {}
                        if _ex.get("appearance_ai"):
                            save_dict["appearance_ai"] = _ex["appearance_ai"]
                camp.character_cards_dir.mkdir(parents=True, exist_ok=True)
                save_path = camp.character_cards_dir / f"{new_name}.yaml"
                if edit_name and edit_name != new_name:
                    old_path = camp.character_cards_dir / f"{edit_name}.yaml"
                    if old_path.exists():
                        old_path.unlink()
                    for ext in _PORTRAIT_EXTS:
                        old_p = camp.character_cards_dir / f"{edit_name}_portrait{ext}"
                        if old_p.exists():
                            old_p.rename(camp.character_cards_dir / f"{new_name}_portrait{ext}")
                save_path.write_text(
                    yaml.dump(save_dict, allow_unicode=True, sort_keys=False, default_flow_style=False),
                    encoding="utf-8",
                )
                st.session_state["card_edit_name"] = new_name
                st.session_state["card_edit_data"] = save_dict
                st.success(f"已保存：{save_path.name}")
                st.rerun()

        # ---- 立绘管理（仅已保存的卡片）----
        if edit_name:
            st.markdown("---")
            st.markdown("#### 🖼️ 角色立绘")
            portrait_path = _find_portrait(edit_name)
            img_col, tool_col = st.columns([1, 2])

            with img_col:
                if portrait_path:
                    st.image(str(portrait_path), caption=edit_name, width="stretch")
                    # Show whether appearance_ai exists
                    yaml_path_for_ai = camp.character_cards_dir / f"{edit_name}.yaml"
                    if yaml_path_for_ai.exists():
                        _card_data = yaml.safe_load(yaml_path_for_ai.read_text(encoding="utf-8")) or {}
                        if _card_data.get("appearance_ai"):
                            st.markdown(
                                '<span class="tn-badge tn-badge-ok">✔ 外貌已识图</span>',
                                unsafe_allow_html=True,
                            )
                        else:
                            st.caption("尚未识图")
                else:
                    st.markdown(
                        '<div style="text-align:center;padding:24px;background:var(--color-bg-soft);'
                        'border-radius:12px;color:var(--color-text-soft)">暂无立绘</div>',
                        unsafe_allow_html=True,
                    )

            with tool_col:
                new_portrait = st.file_uploader(
                    "上传立绘",
                    type=["png", "jpg", "jpeg", "webp"],
                    key=f"portrait_up_{edit_name}",
                    help="支持 PNG / JPG / WEBP，建议分辨率 ≥ 512×512",
                )
                if new_portrait:
                    ext = Path(new_portrait.name).suffix.lower() or ".png"
                    if st.button("保存图片", key=f"btn_save_portrait_{edit_name}", width="stretch"):
                        camp.character_cards_dir.mkdir(parents=True, exist_ok=True)
                        _save_portrait(edit_name, new_portrait.read(), ext)
                        st.success("立绘已保存")
                        st.rerun()

                st.markdown("**AI 外貌识别**")
                vcfg = read_vision_config()
                if not vcfg["api_key"].strip() or not vcfg["model"].strip():
                    st.warning("请先在「⚙️ LLM 配置」页配置识图模型（支持 Vision 输入的模型）。")
                elif not portrait_path:
                    st.caption("请先上传立绘后再分析外貌。")
                else:
                    st.caption("识图结果将存入人物卡 YAML，Draft 时自动注入 prompt，不在表单显示。")
                    if st.button(
                        "🔍 分析外貌",
                        key=f"btn_analyze_{edit_name}",
                        width="stretch",
                        type="primary",
                    ):
                        with st.spinner("调用识图模型中，请稍候…"):
                            try:
                                from trpg2novel.llm.client import chat_vision, make_client
                                img_bytes = portrait_path.read_bytes()
                                mime = (
                                    "image/png" if portrait_path.suffix == ".png"
                                    else "image/webp" if portrait_path.suffix == ".webp"
                                    else "image/jpeg"
                                )
                                client = make_client(vcfg["api_key"], vcfg["base_url"])
                                desc = chat_vision(client, vcfg["model"], img_bytes, _PORTRAIT_PROMPT, mime)
                                # Save appearance_ai to YAML
                                _yaml_p = camp.character_cards_dir / f"{edit_name}.yaml"
                                _cdata = yaml.safe_load(_yaml_p.read_text(encoding="utf-8")) or {} if _yaml_p.exists() else {}
                                _cdata["appearance_ai"] = desc
                                _yaml_p.write_text(
                                    yaml.dump(_cdata, allow_unicode=True, sort_keys=False, default_flow_style=False),
                                    encoding="utf-8",
                                )
                                st.success("外貌描述已生成并保存，下次 Draft 时自动注入 prompt。")
                                st.rerun()
                            except Exception as _e:
                                st.error(f"识图失败：{_e}")

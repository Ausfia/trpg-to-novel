"""测试 md_loader 的核心规则：行头识别、多行归并、反转义、orphan 块归属。"""

from __future__ import annotations

from trpg2novel.parse.md_loader import parse_lines, unescape


def test_unescape_basic():
    assert unescape(r"\<二阶堂希罗\>") == "<二阶堂希罗>"
    assert unescape(r"\[1\]") == "[1]"
    assert unescape(r"\_(¦3」∠)\_") == "_(¦3」∠)_"
    assert unescape(r"\@比阿特丽丝") == "@比阿特丽丝"


def test_single_inline_message():
    lines = [
        r"20:30:12 \<狗dm\>: ------------------------------------------------",
        "",
        r"20:30:37 \<狗dm\>: 至绿镇，这个坐落于剑湾南部的小镇",
    ]
    events = list(parse_lines(lines, "s01"))
    assert len(events) == 2
    assert events[0].timestamp == "20:30:12"
    assert events[0].speaker == "狗dm"
    assert events[0].body == "------------------------------------------------"
    assert events[1].body.startswith("至绿镇")


def test_multiline_message_with_empty_header():
    """行头后内联为空，内容在下一行。"""
    lines = [
        r"20:30:37 \<狗dm\>:",
        "至绿镇，这个坐落于剑湾南部的小镇乃是由一位自称绿色原野女王的半身人游荡者",
        "",
        r"20:30:51 \<狗dm\>: 下一条",
    ]
    events = list(parse_lines(lines, "s01"))
    assert len(events) == 2
    assert events[0].body == "至绿镇，这个坐落于剑湾南部的小镇乃是由一位自称绿色原野女王的半身人游荡者"


def test_orphan_block_belongs_to_previous_message():
    """位于两个行头之间、被空行分隔的"记录已经开启"应附在前一条消息。"""
    lines = [
        r"20:30:07 \<二阶堂希罗\>: 魔女审判，现在开庭。",
        "",
        "记录已经开启",
        "",
        r"20:30:12 \<狗dm\>: ----",
    ]
    events = list(parse_lines(lines, "s01"))
    assert len(events) == 2
    # 第一条应包含两段，用空行分隔
    assert "魔女审判，现在开庭。" in events[0].body
    assert "记录已经开启" in events[0].body
    assert events[0].flags.get("is_record_meta") is True


def test_image_placeholder_flag():
    lines = [
        r"20:39:11 \<狗dm\>: \[1\]",
        "",
        r"20:39:31 \<狗dm\>: 普通对话",
    ]
    events = list(parse_lines(lines, "s01"))
    assert events[0].flags.get("is_image_placeholder") is True
    assert events[0].body == "[1]"
    assert events[1].flags.get("is_image_placeholder") is not True


def test_speaker_with_special_chars():
    """骰娘名字含全角括号和 emoji 也要正确捕获。"""
    lines = [
        r"20:30:07 \<二阶堂希罗（请看标签❗\>: 魔女审判，现在开庭。",
    ]
    events = list(parse_lines(lines, "s01"))
    assert events[0].speaker == "二阶堂希罗（请看标签❗"


def test_event_ids_sequential():
    lines = [
        r"20:30:07 \<a\>: x",
        r"20:30:08 \<b\>: y",
        r"20:30:09 \<c\>: z",
    ]
    events = list(parse_lines(lines, "s01"))
    assert [e.id for e in events] == ["s01-0001", "s01-0002", "s01-0003"]


def test_paragraph_break_preserved_in_multiline():
    """中间空行作为段落分隔保留。"""
    lines = [
        r"20:30:37 \<狗dm\>:",
        "段落一",
        "",
        "段落二",
        "",
        r"20:30:51 \<下一个\>: 收尾",
    ]
    events = list(parse_lines(lines, "s01"))
    assert events[0].body == "段落一\n\n段落二"

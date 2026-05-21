"""临时手工 spot-check 工具：检查解析产物的几条特殊样本。"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

events = json.loads(Path("data/parsed/s01.events.json").read_text(encoding="utf-8"))


def show(e: dict) -> None:
    print(f"  {e['id']} {e['timestamp']} <{e['speaker']}> flags={e['flags']}")
    print(f"    body: {e['body'][:200]!r}")
    print()


print("=== 第一条（骰娘开场 + 记录已开启 应合并到同一条） ===")
show(events[0])

print("=== 第二条（狗dm 分隔线） ===")
show(events[1])

print("=== 含 [1] 图片占位的第一条 ===")
for e in events:
    if e["flags"].get("is_image_placeholder"):
        show(e)
        break

print("=== DM 多行长叙述（body 含换行）的第一条 ===")
for e in events:
    if e["speaker"] == "狗dm" and "\n" in e["body"] and len(e["body"]) > 50:
        show(e)
        break

print("=== 含全角 OOC（）的 PC 行 ===")
for e in events:
    if (
        "（" in e["body"]
        and e["speaker"] not in ("狗dm", "二阶堂希罗（请看标签❗")
    ):
        show(e)
        break

print("=== 含战斗轮转模板的 ===")
for e in events:
    if "戏份结束了" in e["body"]:
        show(e)
        break

print(f"=== 总计 {len(events)} 条 ===")
print(
    f"含 is_image_placeholder: {sum(1 for e in events if e['flags'].get('is_image_placeholder'))}"
)
print(
    f"含 is_record_meta:       {sum(1 for e in events if e['flags'].get('is_record_meta'))}"
)

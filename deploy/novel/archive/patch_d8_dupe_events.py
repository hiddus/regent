"""缺陷 8：结算把已有事件原样回吐，extend 重复累加，喂出后续整串级联故障。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "core/src/regent/novel/application/direction.py"

EDITS: list[tuple[str, str, str]] = []


def sub(label: str, old: str, new: str) -> None:
    EDITS.append((label, old, new))


# --------------------------------------------------------------- 1. 去重函数
sub(
    "_extend_events",
    "def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:\n",
    "def _extend_events(take: dict[str, Any], events: list[Any]) -> None:\n"
    '    """把本轮事件并入 take；**同一句陈述**已入场的不再重复入场。\n'
    "\n"
    "    结算请求会把已有事件一并给模型看（它必须基于既有事实判定结果），模型于是\n"
    "    把看过的事件原样回吐，``extend`` 就把它们重复累加：真机样本里一个 6 事件的\n"
    "    场景跑完 3 个节拍变成 22 条，其中三组完全相同。重复事件会喂出重复正文、\n"
    "    触发规则冲突、迫使导演重演，最后卡死整章——而根因只是一句 ``extend``。\n"
    '    """\n'
    '    seen = {str(e.get("statement", "")) for e in take["events"]}\n'
    "    for event in events:\n"
    '        dumped = event.model_dump(mode="json")\n'
    '        key = str(dumped.get("statement", ""))\n'
    "        if not key or key in seen:\n"
    "            continue\n"
    "        seen.add(key)\n"
    '        take["events"].append(dumped)\n'
    "\n"
    "\n"
    "def _check_brief(brief: SceneBrief, cast: dict[str, Any]) -> None:\n",
)

# ------------------------------------------------------------------ 2. 调用点
sub(
    "extend call site",
    '        take["events"].extend(e.model_dump(mode="json") for e in result.events)\n',
    "        _extend_events(take, result.events)\n",
)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    for label, old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"[FAIL] 锚点 {label} 命中 {count} 次（期望 1）")
            return 1
        text = text.replace(old, new, 1)
    try:
        compile(text, str(TARGET), "exec")
    except SyntaxError as exc:
        print(f"[FAIL] 语法错误 {exc}")
        return 1
    TARGET.write_text(text, encoding="utf-8")
    print(f"[OK] {TARGET.name} 已改写（{len(EDITS)} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

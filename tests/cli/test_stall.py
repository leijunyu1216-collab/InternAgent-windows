"""Offline checks for the live region's stall hint.

The first real run printed its header and then said nothing for a minute, and
the reader's conclusion was "hung CLI" — so the quiet case is a display feature
with its own tests, not a lucky accident of timing.
"""
import io
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

from rich.cells import cell_len
from rich.console import Console

import cli_live

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def new_view(width=100):
    buf = io.StringIO()
    console = Console(file=buf, width=width, color_system=None)
    return cli_live.LiveView(console), buf


def lines_of(view):
    """The live region as plain lines, the way the terminal would show them."""
    group = view.render()
    out = io.StringIO()
    console = Console(file=out, width=view.console.width, color_system=None)
    console.print(group)
    return [l for l in out.getvalue().split("\n") if l.strip()], out.getvalue()


print("== 1. 默认不打扰 ==")
view, _buf = new_view()
check(view._stall_hint() is None, "刚建好时没有提示")
view.feed({"event": "workflow_start", "agent": "x", "data": {
    "question": "问题", "mode": "qa", "model": "m"}})
check(view._stall_hint() is None, "收到事件后仍然安静")
lines, text = lines_of(view)
check("无事件" not in text, "安静时渲染里没有提示行")

print("== 2. 空等：一次调用都没发出 ==")
view, _buf = new_view()
view.t_event = time.monotonic() - (cli_live.STALL_SECS + 15)
hint = view._stall_hint()
shown = int(re.search(r"(\d+)s", hint).group(1)) if hint else -1
check(cli_live.STALL_SECS + 13 <= shown <= cli_live.STALL_SECS + 16,
      f"提示里的秒数是真实静默时长（{shown}）")
check(hint and "无事件" in hint, "说明是彻底没有事件")
check(hint and "base_url" in hint and "模型名" in hint, "给出可核对的东西")
lines, text = lines_of(view)
check(any("无事件" in l for l in lines), "提示出现在实时区域里")
check(lines[-1].strip().startswith("已用"), "提示在页脚之上而不是最后")

print("== 3. 卡在模型调用里 ==")
view, _buf = new_view()
view.feed({"event": "model_call_start", "agent": "x", "data": {
    "call": 1, "stage": "全局规划", "detail": "生成执行图"}})
view.t_event = time.monotonic() - (cli_live.STALL_SECS + 5)
hint = view._stall_hint()
check(hint and "无新字符" in hint, "有调用在飞时换成‘连接仍在’的说法")
check(hint and "base_url" not in hint, "不再让人去查配置——那不是这个卡点")

print("== 4. 事件一到就消音 ==")
view.feed({"event": "model_call_chunk", "agent": "x",
           "data": {"call": 1, "text": "内容", "chars": 2}})
check(view._stall_hint() is None, "chunk 也刷新静默计时")
view.t_event = time.monotonic() - (cli_live.STALL_SECS + 5)
check(view._stall_hint() is not None, "重新静默后又出现")
view.feed({"event": "model_call_end", "agent": "x", "data": {
    "call": 1, "secs": 1.0, "chars": 10, "text": "", "reasoning": "",
    "stage": "全局规划", "detail": "生成执行图"}})
check(view._stall_hint() is None, "调用结束后计时被新事件刷新")

print("== 5. 窄终端里不折行 ==")
for width in (40, 60, 100):
    view, _buf = new_view(width=width)
    view.t_event = time.monotonic() - (cli_live.STALL_SECS + 40)
    lines, text = lines_of(view)
    hit = [l for l in lines if "无事件" in l or "可能卡在" in l or "Ctrl+C" in l]
    check(all(cell_len(l) <= width for l in hit),
          f"{width} 列时提示行不超宽（{[cell_len(l) for l in hit]}）")
    check(bool(hit), f"{width} 列时提示仍在")

print("== 6. CLI_STALL_SECS 生效 ==")
src = open(os.path.join(os.path.dirname(cli_live.__file__), "cli_live.py"),
           encoding="utf-8").read()
check('CLI_STALL_SECS' in src and 'STALL_SECS = float(os.environ.get' in src,
      "阈值可由环境变量调，不用改代码")
os.environ.pop("CLI_STALL_SECS", None)
check(cli_live.STALL_SECS >= 20,
      f"默认阈值不低于 20s，避免把正常的检索间隙当成卡死（{cli_live.STALL_SECS}）")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过")

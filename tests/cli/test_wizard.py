"""Offline checks for the wizard's choice widgets (cli_ui) and the arrow-key
path the real terminal takes.

No console, no API key: the key reader is injected, so a selection is just a
list of keys. test_repl.py covers the numbered fallback by driving cmd_run with
a scripted stdin; this suite covers what that one cannot — redraw, cursor
movement, filtering, paging, and the two-column alignment under CJK labels.
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

from rich.cells import cell_len
from rich.console import Console

import cli_ui
import internagent_cli as ic

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Za-z]|\x1b[=>]|\r")
CURSOR_UP = re.compile(r"\x1b\[\d*A")


def frames(raw):
    """Split a Live transcript into chunks at each cursor-up.

    rich erases a redrawn region line by line, so this is one chunk per *line*
    rather than per frame — which is why the checks below filter for the text
    they care about instead of indexing a frame number.
    """
    return [ANSI.sub("", part) for part in CURSOR_UP.split(raw)]


def keys(seq):
    """A stand-in for win_key_reader/posix_key_reader: the contract is action
    names ("up", "enter", "backspace") or a single printable character, which is
    what those two map the raw bytes to. The mapping itself is checked in
    section 9 against a fake msvcrt."""
    it = iter(seq)
    return lambda: next(it, "enter")


def run_select(seq, options=None, **kw):
    """Return (index, cancelled, raw) for one arrow-key selection."""
    buf = io.StringIO()
    console = Console(file=buf, width=kw.pop("width", 80), force_terminal=True)
    kw.setdefault("read_key", keys(seq) if isinstance(seq, list) else seq)
    i, cancelled = cli_ui.select(console, "模型供应商",
                                 options if options is not None else OPTS, **kw)
    return i, cancelled, buf.getvalue()


def drawn(raw):
    """The frames that are the list itself, not the ✓ line that replaces it."""
    return [f for f in frames(raw) if "官方端点" in f]


OPTS = [("deepseek", "官方端点 · V3 / R1"),
        ("qwen", "阿里百炼 dashscope"),
        ("moonshot", "Kimi · 长上下文"),
        ("custom", "手填 base_url")]

print("== 1. 方向键与回车 ==")
i, cancelled, raw = run_select(["down", "down", "enter"])
check(i == 2 and not cancelled, f"↓↓ 选中第 3 项，实得 {i}")
check(any("› moonshot" in f for f in drawn(raw)), "光标停在选中项上")
check("✓ 模型供应商  moonshot" in ANSI.sub("", raw), "答完折叠成一行 ✓")
check(len(drawn(raw)) >= 3,
      f"每按一次重画一帧（Live.stop 收尾时还会再刷一次），实得 {len(drawn(raw))}")

i, cancelled, raw = run_select(["up"])
check(i == 0, f"光标不会越过第一项，实得 {i}")
i, cancelled, raw = run_select(["down"] * 20)
check(i == 3, f"光标不会越过最后一项（默认回车落在 {i}）")

print("== 2. 数字直达 / Esc / Ctrl+C ==")
i, _c, raw = run_select(["3"])
check(i == 2, f"9 项以内可以按数字直达，实得 {i}")
i, cancelled, raw = run_select(["esc"])
check(i == -1 and cancelled, "Esc 返回 cancelled，由调用方决定怎么退出")
try:
    run_select(["ctrl-c"])
    check(False, "Ctrl+C 应该抛出 KeyboardInterrupt")
except KeyboardInterrupt:
    check(True, "Ctrl+C 抛出 KeyboardInterrupt，而不是静默返回")
i, _c, raw = run_select(["9"])
check(i == 0, f"超出范围的数字不移动光标，回车仍落在默认项 {i}")

print("== 3. 两列对齐（中文标签）==")
CJK = [("deepseek", "官方端点"), ("本地模型", "手填 base_url · Ollama"),
       ("siliconflow", "聚合开源模型")]
for width in (60, 80, 140):
    i, _c, raw = run_select(["enter"], options=CJK, width=width)
    rows = [l for l in frames(raw)[0].split("\n") if "官方端点" in l
            or "手填" in l or "聚合" in l]
    offsets = {cell_len(l.split(d)[0]) for l, d in
               zip(rows, ("官方端点", "手填 base_url", "聚合开源模型"))}
    check(len(offsets) == 1, f"{width} 列下三行的第二列起点同一格：{sorted(offsets)}")
    check(all(cell_len(l) <= width for l in rows),
          f"{width} 列下没有一行超宽")

print("== 4. 字母过滤 ==")
MANY = [(f"model-{i:02d}", f"第 {i} 个") for i in range(1, 26)]
i, _c, raw = run_select(list("model-01") + ["enter"],
                        options=MANY, filterable=True)
check(i == 0, f"过滤到 model-01 后回车选中它，实得 {MANY[max(i, 0)][0]}")
last = frames(raw)[-1]
check("model-01" in last and "model-25" not in last, "过滤后只显示匹配项")
i, _c, raw = run_select(list("zz") + ["enter", "backspace", "backspace", "enter"],
                        options=MANY, filterable=True)
check("没有匹配项" in raw, "无匹配时明说，而不是显示一个空列表")
check(i == 0, f"退格恢复列表后回车选第一项，实得 {i}")

print("== 5. 长列表翻页 ==")
i, _c, raw = run_select(["enter"], options=MANY)
first = frames(raw)[0]
check(first.count("\n") <= 15, f"默认只画一屏（{first.count(chr(10))} 行）")
check("共 25 项" in first, "剩下的项说一句有多少")
i, _c, raw = run_select(["end", "enter"], options=MANY)
check(i == 24, f"End 跳到最后一项，实得 {i}")
i, _c, raw = run_select(["pgdn", "enter"], options=MANY)
check(i == 12, f"PgDn 走一屏，实得 {i}")

print("== 6. 数字回退（没有方向键时）==")


def typed(seq):
    it = iter(seq)
    return lambda msg, default="": next(it, default)


def numbered(seq, default=0, options=None, **kw):
    buf = io.StringIO()
    console = Console(file=buf, width=80)
    kw.setdefault("ask", typed(seq))
    i, cancelled = cli_ui.select(console, "模型供应商",
                                 options if options is not None else OPTS,
                                 default=default, read_key=None, **kw)
    return i, cancelled, buf.getvalue()


i, _c, out = numbered([])
check(i == 0, "直接回车用默认项")
i, _c, out = numbered(["moonshot"])
check(i == 2, f"打名字也能选中，实得 {i}")
i, _c, out = numbered(["9", "zzz", "2"])
check(i == 1, "先打错两次会被指出来，然后接受正确的")
check("没有第 9 项" in out and "列表里没有 zzz" in out, "两种错法各有各的说法")
i, _c, out = numbered(["", "", ""], default=2)
check(i == 2, "默认项就是上次记住的那一项")

print("== 7. 确认卡 ==")
# The card cuts values instead of wrapping them, so a row that does not fit loses
# its tail — which for the budget row meant losing the last digit of a cap ("图层
# ≤1…"). Hence the compact form and this width-60 render.
ns = argparse.Namespace(max_workers=None, max_tool_calls=None, max_layers=None)
budget = ic._budget_summary("qa", ns, compact=True)
buf = io.StringIO()
console = Console(file=buf, width=60)
cli_ui.card(console, "准备开始",
            [("供应商", "deepseek · https://api.deepseek.com/v1"),
             ("模型", "deepseek-chat"),
             ("模式", "complex"),
             ("预算", budget),
             ("单轮上限", "600s"),
             ("附件", "一个被截断的很长的文件名用来验证不会撑破边框.txt")],
            footer="Enter 开始 · m 换一个模型 · q 退出")
lines = [l for l in ANSI.sub("", buf.getvalue()).split("\n") if l]
check(all(cell_len(l) <= 60 for l in lines),
      f"卡片里没有一行超过终端宽度：{max(cell_len(l) for l in lines)}")
check("…" in buf.getvalue(), "放不下的值被截断而不是折行")
check("Enter 开始" in buf.getvalue(), "脚注在框外，不会被边框切掉")
budget_line = [l for l in lines if "并发" in l][0]
check("…" not in budget_line,
      f"预算那一行在 60 列里完整地放得下：{budget_line.strip()}")
check(budget in budget_line, "预算行原样落在卡片上，数字没被切掉")

print("== 8. 端点模型列表里挑 ==")
buf = io.StringIO()
console = Console(file=buf, width=80, force_terminal=True)
picked = ic._pick_model(console, "端点没有这个模型名", ["deepseek-chat",
                        "deepseek-reasoner"], "deepseek-flsh",
                        read_key=keys(["up", "enter"]))
check(picked == "deepseek-reasoner", f"从列表里换掉了打错的模型名，实得 {picked}")
buf = io.StringIO()
console = Console(file=buf, width=80, force_terminal=True)
picked = ic._pick_model(console, "端点没有这个模型名", ["a", "b"],
                        "my-custom-model", read_key=keys(["esc"]))
check(picked == "my-custom-model", "Esc 保留原输入：/models 不全的端点不该拦人")
buf = io.StringIO()
console = Console(file=buf, width=80, force_terminal=True)
picked = ic._pick_model(console, "端点没有这个模型名", ["a", "b"], "typed-name",
                        read_key=keys(["down", "enter"]))
check(picked == "typed-name",
      "默认光标停在「仍然用」上，往下按不会跑出列表")
buf = io.StringIO()
console = Console(file=buf, width=80)
picked = ic._pick_model(console, "端点没有这个模型名", ["a", "b"], "typed",
                        read_key=None, ask=typed(["3"]))
check(picked == "typed", "数字路径下选「仍然用」同样保留原输入")

print("== 9. 键盘从哪来 ==")
check(ic._wizard_reader() is None or callable(ic._wizard_reader()),
      "_wizard_reader 返回一个读键函数或 None")
real_stdin = sys.stdin
try:
    sys.stdin = io.StringIO("")          # 被管道接走时不是 tty
    check(ic._wizard_reader() is None, "stdin 不是 tty 时退回数字，绝不阻塞在读键上")
finally:
    sys.stdin = real_stdin

if os.name == "nt":
    import msvcrt
    real_getwch = msvcrt.getwch
    try:
        # Each extended key costs two reads: the 0xE0/0x00 prefix, then the
        # scan code. Six reads below consume these seven bytes plus one.
        seq = iter(["\xe0", "P", "\r", "\x00", "H", "\x03", "q"])
        msvcrt.getwch = lambda: next(seq, "q")
        rk = cli_ui.win_key_reader()
        got = [rk() for _ in range(5)]
        check(got == ["down", "enter", "up", "ctrl-c", "q"],
              f"msvcrt 的前缀字节被翻成方向键：{got}")
    finally:
        msvcrt.getwch = real_getwch
else:
    check(True, f"（{os.name} 上跳过 msvcrt 解码，POSIX 走 termios）")

print("== 10. 供应商表与模式表自洽 ==")
check(all(p.base is None or p.base.startswith("https://")
          for p in ic.PROVIDERS.values()), "预设 base_url 都是 https")
check(all(p.note for p in ic.PROVIDERS.values()),
      "每家都有一句说明，向导第二列不留空")
check(ic.PROVIDERS["custom"].base is None and ic.PROVIDERS["custom"].prefix == "openai",
      "custom 没有预设地址，litellm 前缀按 openai 兼容处理")
for mode, note in ic.MODES:
    path = os.path.join(ic.REPO, "internagent", "mas", "agents", "dr_agents",
                        f"config_{mode}.yaml")
    check(os.path.isfile(path), f"模式 {mode} 的配置文件真的存在（{note[:10]}…）")
check([m for m, _ in ic.MODES] == ["qa", "simple", "complex"],
      "向导给出的三种模式与 scripted 分支认的取值一致")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败")
    sys.exit(1)
print("全部通过")

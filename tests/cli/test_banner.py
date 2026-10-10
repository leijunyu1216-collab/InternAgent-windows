"""Offline checks for the slab startup banner."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import io


from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

import internagent_cli as ic

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def render(text, width=80):
    buf = io.StringIO()
    Console(file=buf, width=width, color_system=None).print(text)
    return buf.getvalue()


b = ic._banner(["INTERN", "AGENT"], 80)
check(b is not None, "80 列时画出 logo")
out = render(b)
lines = [l for l in out.split("\n") if l.strip()]
check(len(lines) == 12, f"两行字共 12 行（含阴影与空行），实得 {len(lines)}")
check(max(cell_len(l) for l in out.split("\n")) <= 38,
      f"最宽 {max(cell_len(l) for l in out.split(chr(10)))} 列，不超过 38")
check("INTERN" not in out and "█" in out, "是块字而非普通文本")

# every glyph row must be the same length or the grid mis-indexes
for ch, glyph in ic.BANNER_FONT.items():
    check(len({len(r) for r in glyph}) == 1, f"字形 {ch} 各行等宽")
    check(all(len(r) == len(glyph[0]) for r in glyph), f"字形 {ch} 行数为 {len(glyph)}")
heights = {len(g) for g in ic.BANNER_FONT.values()}
check(heights == {5}, "所有字形同高 5 行")

# letters must not fuse: with the shadow at +1 cell, a 2-cell gap stays open
one = ic._banner(["NI"], 80)
o = render(one).split("\n")
col_gaps = [c for c in range(40) if all(not (len(r) > c and r[c] == "█") for r in o)]
check(len(col_gaps) >= 1, "N 与 I 之间有完全空白的列（阴影没有把字糊在一起）")

check(ic._banner(["INTERN", "AGENT"], 30) is None, "终端太窄时返回 None，交给回退面板")
check(ic._banner(["INTERN", "AGENT"], 40) is not None, "40 列刚好放得下")
check(ic._banner(["量子"], 200) is None, "字库里没有的字形返回 None")
check(ic._banner(["", "  "], 200) is None, "空文本返回 None 而不是崩")

# single line works too
single = ic._banner(["INTERN"], 80)
check(single is not None and len([l for l in render(single).split("\n") if l.strip()]) == 6,
      "单行 logo 6 行高（5 + 阴影）")

check(ic._darker("#4ec9b0") == "#205449", f"阴影色由主色压暗得到：{ic._darker('#4ec9b0')}")
check(ic._darker("red") == "dim", "非十六进制主色时阴影退回 dim 样式")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败")
    sys.exit(1)
print("全部通过")

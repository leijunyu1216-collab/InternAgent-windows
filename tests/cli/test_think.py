"""Offline checks for the collapsed-thinking display + /think expansion."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import io
import tempfile


from rich.cells import cell_len
from rich.console import Console

import cli_live

WIDTH = 100
FAILS = []


def check(cond, msg):
    if cond:
        print(f"  ok  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL {msg}")


def flatten(s):
    """Join wrapped console output back into comparable prose: drop the box
    gutter and all whitespace so a line-wrapped CJK string can be matched."""
    return "".join(s.replace("│", "").split())


def new_console():
    buf = io.StringIO()
    return Console(file=buf, width=WIDTH, force_terminal=False,
                   color_system=None), buf


# A reasoning blob shaped like a real deepseek-reasoner chain of thought: many
# paragraphs, opening by restating the task and ending with the decision.
EARLY = "The user wants a survey of retrieval-augmented generation evaluation."
MID = ("I should consider whether to split by retrieval quality or by "
       "end-to-end utility. Both are defensible. Let me weigh coverage.")
DECISION = ("最终决定：把评测拆成三条子任务，分别覆盖检索质量、生成忠实度与端到端效用，"
            "并优先使用 2024 年之后的公开基准，避免只引用综述类文献。")
REASON = "\n\n".join([EARLY] + [MID] * 12 + [DECISION])
CONTENT = "本次汇总覆盖了 12 篇文献，主要结论是评测口径不统一。"


def record(event, **data):
    return {"seq": 1, "t": 0.0, "wall": 0.0, "tid": 1, "event": event,
            "agent": "test", "data": data}


print("== 1. 折叠显示 + 归档 ==")
tmpdir = tempfile.mkdtemp(prefix="thinktest")
archive_path = os.path.join(tmpdir, "logs", "think_test.md")
archive = cli_live.ThinkArchive(archive_path)
console, buf = new_console()
view = cli_live.LiveView(console, archive)

view.feed(record("model_call_start", call=1, stage="全局规划",
                 detail="生成执行图 · 第 1/2 轮", model="deepseek-flash",
                 prompt="...", stream=True))
view.feed(record("model_call_chunk", call=1, stage="全局规划", kind="reasoning",
                 chars=len(REASON), text=REASON))
view.feed(record("model_call_end", call=1, stage="全局规划",
                 detail="生成执行图 · 第 1/2 轮", secs=11.8,
                 chars=len(REASON) + len(CONTENT), text=CONTENT,
                 reasoning=REASON))
out = buf.getvalue()
print(out)
# Compare with whitespace stripped: the excerpt is wrapped across gutter lines.
flat = flatten(out)

check("#1" in out, "永久行带思考编号 #1")
check("/think 1 展开" in out, "折叠提示指向 /think 1")
check(flatten(DECISION) in flat, "屏上保留的是结尾的结论")
check("surveyofretrieval-augmented" not in flat, "屏上没有开头的任务复述")
check("前" in out and "字已折叠" in out, "报告了折叠掉的字数")
check(f"{len(REASON) + len(CONTENT)} 字" in out, "字数统计照旧")
check(all(cell_len(line) <= WIDTH for line in out.split("\n")),
      "所有行都不超出终端宽度（按显示单元算）")

saved = open(archive_path, encoding="utf-8").read()
check(REASON in saved, "归档文件里有完整思考")
check(CONTENT in saved, "归档文件里有完整输出")
check(saved.count("## #1 ") == 1, "归档只写了一次")
check(len(archive.entries) == 1 and archive.entries[0]["id"] == 1,
      "内存里一条记录")

print("== 2. cap=0 全折叠 ==")
console, buf = new_console()
view = cli_live.LiveView(console, None)
view._say_thinking(REASON, cap=0)
out = buf.getvalue()
check(DECISION not in out and "The user" not in out, "cap=0 时正文完全不打印")
check(f"{len(REASON)} 字已折叠" in out.replace("\n", ""), "cap=0 仍报告折叠了多少字")

print("== 3. /think 列表与展开 ==")
console, buf = new_console()
cli_live.list_thinking(console, archive.entries, archive.path)
listing = buf.getvalue()
print(listing)
check("#1" in listing and "全局规划" in listing, "列表显示编号与阶段")
check("/think 编号 展开全部" in listing, "列表给出用法提示")

console, buf = new_console()
cli_live.show_thinking(console, archive.entries[0])
full = buf.getvalue()
full_flat = flatten(full)
check(flatten(DECISION) in full_flat, "展开后有结论")
check(flatten(EARLY) in full_flat, "展开后有被折叠的开头")
check("── 思考" in full and "── 输出" in full, "展开分别标出思考与输出")
check(flatten(CONTENT) in full_flat, "展开里有输出正文")
check(all(cell_len(line) <= WIDTH for line in full.split("\n")),
      "展开内容也不超宽")

console, buf = new_console()
cli_live.show_thinking(console, archive.entries[0], limit=200)
part = buf.getvalue()
check("已按 200 字截断" in part, "带字数上限时说明被截断")
check(len(part) < len(full), "截断确实更短")

print("== 4. 无归档时不崩 ==")
console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(record("model_call_start", call=2, stage="答案合成", detail="问答合成",
                 model="m", prompt="", stream=True))
view.feed(record("model_call_end", call=2, stage="答案合成", detail="问答合成",
                 secs=3.0, chars=50, text="答案正文", reasoning=""))
out = buf.getvalue()
check("◆" in out and "#" not in out, "没有归档就不显示编号")

print("== 5. 节点内并行调用各自编号 ==")
console, buf = new_console()
archive2 = cli_live.ThinkArchive(None)
view = cli_live.LiveView(console, archive2)
view.feed(record("get_current_execution_nodes", node_ids=["n1", "n2"]))
for i, nid in enumerate(("n1", "n2"), start=3):
    view.feed(record("model_call_start", call=i, stage="节点执行",
                     detail="选择下一步", node_id=nid, subtask_id=1,
                     model="m", prompt="", stream=True))
    view.feed(record("model_call_end", call=i, stage="节点执行",
                     detail="选择下一步", node_id=nid, subtask_id=1,
                     secs=2.0, chars=900, text="", reasoning="思考" * 450))
out = buf.getvalue()
print(out)
check("节点 n1" in out and "节点 n2" in out, "两条永久行标明各自节点")
check("#1" in out and "#2" in out, "各自编号 #1 #2")
check("前 500 字已折叠" in out, "单段超长也报告折叠字数")
check(archive2.path is None and len(archive2.entries) == 2,
      "path=None 时只在内存里归档")

print("== 6. 磁盘不可写时降级 ==")
blocker = os.path.join(tmpdir, "blocker")
open(blocker, "w").close()  # a regular file where a directory is required
bad = cli_live.ThinkArchive(os.path.join(blocker, "sub", "t.md"))
rid = bad.add("头", 1.0, 10, "思考", "")
check(rid == 1 and bad.path is None, "写不进去就放弃文件、保留内存记录")
check(len(bad.entries) == 1, "记录仍在")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过")

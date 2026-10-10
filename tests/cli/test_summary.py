"""Offline checks for turn totals, token accounting and error translation."""
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
import internagent_cli as ic

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def new_console(width=100):
    buf = io.StringIO()
    return Console(file=buf, width=width, color_system=None), buf


def record(event, **data):
    return {"seq": 1, "t": 0.0, "wall": 0.0, "tid": 1, "event": event,
            "agent": "test", "data": data}


def end(call, stage, detail, usage, node=None):
    ident = {"node_id": node} if node else {}
    return record("model_call_end", call=call, stage=stage, detail=detail,
                  secs=2.0, chars=100, text="结果", reasoning="",
                  usage=usage, **ident)


print("== 1. 分阶段 token 累计 ==")
console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(record("get_current_execution_nodes", node_ids=["n1", "n2"]))
view.feed(end(1, "全局规划", "生成执行图", {"prompt": 900, "completion": 300, "total": 1200}))
view.feed(end(2, "节点执行", "选择下一步", {"prompt": 200, "completion": 50, "total": 250}, node="n1"))
view.feed(end(3, "节点执行", "汇总子任务", {"prompt": 400, "completion": 100, "total": 500}, node="n1"))
s = view.summary()
check(s["usage"]["total"] == 1950, f"合计 {s['usage']['total']} == 1950")
check(s["usage"]["prompt"] == 1500 and s["usage"]["completion"] == 450, "输入/输出分别累计")
check(s["usage_by_stage"]["节点执行"]["total"] == 750, "节点执行阶段 750")
check(s["usage_by_stage"]["节点执行"]["calls"] == 2, "该阶段 2 次调用")
check(s["usage_seen"] is True, "标记为已上报")
check("1950 tokens" in view._footer(), "页脚显示 token")

print("== 2. 端点不上报用量 ==")
console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(end(1, "全局规划", "生成执行图", {}))
s = view.summary()
check(s["usage_seen"] is False and s["usage"]["total"] == 0, "未上报则为 0，不编造")
check("tokens" not in view._footer(), "页脚不显示 token 段")

print("== 3. 小结行 ==")
console, buf = new_console()
cli_live.print_summary(console, {
    "secs": 83.0, "layers": 2, "model_calls": 9, "tool_calls": 14, "refs": 7,
    "usage": {"prompt": 1500, "completion": 450, "total": 1950},
    "usage_seen": True, "usage_by_stage": {}, "error": None}, think_count=6)
line = buf.getvalue()
print(line)
check("1m23s" in line, "耗时格式化")
check("2 层" in line and "9 次模型调用" in line and "14 次工具调用" in line, "各计数都在")
check("1500入/450出" in line, "token 显示输入/输出")
check("7 条引用" in line and "6 条思考" in line, "引用与思考条数")

console, buf = new_console()
cli_live.print_summary(console, {
    "secs": 5.0, "layers": 0, "model_calls": 1, "tool_calls": 0, "refs": 0,
    "usage": {}, "usage_seen": False, "usage_by_stage": {}, "error": None})
line = buf.getvalue()
check("0 层" not in line and "工具调用" not in line, "qa 模式不显示为 0 的项")

console, buf = new_console()
cli_live.print_summary(console, None)
check(buf.getvalue() == "", "summary=None 时不打印任何东西")

print("== 4. /usage 表格 ==")
console, buf = new_console()
cli_live.print_usage(console, {
    "答案合成": {"prompt": 5000, "completion": 2000, "total": 7000, "calls": 5},
    "全局规划": {"prompt": 900, "completion": 300, "total": 1200, "calls": 1},
}, {"prompt": 5900, "completion": 2300, "total": 8200}, True)
table = buf.getvalue()
print(table)
check(table.index("答案合成") < table.index("全局规划"), "按用量从大到小排")
check("8200" in table and "合计" in table, "有合计行")
rows = [l.rstrip("\n") for l in table.split("\n") if l.strip()]
from rich.cells import cell_len
# ljust() pads by characters, so a CJK stage name silently shifts the columns
# after it. Every row must occupy the same number of *display cells*.
check(len(set(cell_len(l) for l in rows)) == 1,
      f"各行显示宽度一致: {sorted(set(cell_len(l) for l in rows))}")
nums = [l for l in rows if l.rstrip()[-1].isdigit()]
check(len(set(cell_len(l.rstrip()) for l in nums)) == 1, "数字行右边缘同列")
check("7000" in table and "1200" in table, "每阶段合计在表内")

console, buf = new_console()
cli_live.print_usage(console, {}, {}, False)
check("未上报" in buf.getvalue(), "端点不上报时说明原因而不是给 0")

console, buf = new_console()
cli_live.print_usage(console, {
    "qa": {"prompt": 1, "completion": 2, "total": 3, "calls": 1},
    "答案合成与引用整理": {"prompt": 5000, "completion": 2000, "total": 7000,
                    "calls": 5},
}, {"prompt": 5001, "completion": 2002, "total": 7003}, True)
mixed = [l.rstrip("\n") for l in buf.getvalue().split("\n") if l.strip()]
check(len(set(cell_len(l) for l in mixed)) == 1,
      "长短/中英混排的阶段名也不移列")
check(mixed[-1].rstrip().endswith("7003"), "合计行给出会话总量")

print("== 5. merge_usage 跨轮累计 ==")
turn1 = {"secs": 10.0, "model_calls": 3, "tool_calls": 2, "usage": {"total": 100, "prompt": 60, "completion": 40},
         "usage_seen": True, "usage_by_stage": {"全局规划": {"prompt": 60, "completion": 40, "total": 100, "calls": 1}}}
turn2 = {"secs": 5.0, "model_calls": 1, "tool_calls": 0, "usage": {"total": 50, "prompt": 30, "completion": 20},
         "usage_seen": True, "usage_by_stage": {"全局规划": {"prompt": 30, "completion": 20, "total": 50, "calls": 1}}}
acc = cli_live.merge_usage(None, turn1)
acc = cli_live.merge_usage(acc, turn2)
check(acc["usage"]["total"] == 150 and acc["turns"] == 2, "两轮合计 150 tokens / 2 轮")
check(acc["usage_by_stage"]["全局规划"]["calls"] == 2, "同一阶段调用数累加")
check(acc["secs"] == 15.0 and acc["model_calls"] == 4, "耗时与调用数累加")

print("== 6. 错误翻译 ==")
cases = [
    ("AuthenticationError: Error code: 401 - {'error': {'message': 'Invalid API Key'}}", "API key 无效"),
    ("RateLimitError: Error code: 429 - Too Many Requests", "限流"),
    ("BadRequestError: The model 'deepseek-flsh' does not exist", "模型名不存在"),
    ("APIConnectionError: Connection refused by host", "连不上 base_url"),
    ("APITimeoutError: Request timed out.", "超时"),
    ("insufficient balance in account", "余额"),
    ("This model's maximum context length is 8192 tokens", "上下文长度"),
]
for raw, expect in cases:
    got = ic._explain(RuntimeError(raw))
    check(expect in got, f"'{raw[:44]}…' → 含「{expect}」")
    check(raw[:40] in got or "原始错误" in got, f"    同时保留原始错误")

plain = ic._explain(ValueError("something unusual happened"))
check("ValueError" in plain and "原始错误" not in plain, "没命中启发式时原样给出类型+信息")
check(len(ic._explain(ValueError("x" * 5000))) < 500, "超长信息被截断")

print("== 7. /save 写文件 ==")
tmp = tempfile.mkdtemp(prefix="save")
path = os.path.join(tmp, "sub", "a_%Y.md")
console, buf = new_console()
ic._write_answer(path, "回答正文", console)
written = [f for f in os.listdir(os.path.join(tmp, "sub"))]
check(len(written) == 1 and written[0].endswith(".md") and "%Y" not in written[0],
      f"strftime 模板被展开：{written}")
check(open(os.path.join(tmp, "sub", written[0]), encoding="utf-8").read() == "回答正文",
      "内容正确写入")
plain_path = os.path.join(tmp, "plain.md")
ic._write_answer(plain_path, "第二段", console)
check(open(plain_path, encoding="utf-8").read() == "第二段", "普通路径直接写入")
check("已写入" in buf.getvalue(), "给出写入确认")

print("== 参考文献：事件形状与 /refs 排版 ==")
console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(record("get_cited_references", references={
    "1": {"title": "中文很长的文献标题" * 12, "url": "https://example.test/a",
          "type": "paper"},
    "2": {"title": "Second paper", "url": "https://example.test/b"}}))
s = view.summary()
check(s["refs"] == 2, "dict 形状（synthesizer 实际发的形状）计数为 2")
check([r["tag"] for r in s["references"]] == ["1", "2"], "编号取 dict 的 key")
check(s["references"][0]["url"] == "https://example.test/a",
      "URL 与标题跟着 summary 交给调用方")
check("/refs 查看" in buf.getvalue(), "实时界面上提示可以用 /refs")

console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(record("get_cited_references", references=[{"id": 1}, {"id": 2}]))
check(view.summary()["refs"] == 2, "list 形状同样计数，缺字段不抛异常")
console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(record("get_cited_references", references=[]))
check("没有解析出" in buf.getvalue(), "空列表说明没解析出来，而不是打印 0 条")

console, buf = new_console(width=60)
cli_live.print_references(console, [
    {"tag": "1", "title": "中文标题" * 30, "url": "https://example.test/" + "x" * 90},
    {"tag": "12", "title": "Short English title", "url": ""}], limit=0)
lines = [l for l in buf.getvalue().split("\n") if l.strip()]
print("\n".join(lines))
check(all(cell_len(l) <= 60 for l in lines),
      "60 列下标题和长 URL 都不越界")
check(any(l.strip().startswith("[1]") for l in lines), "编号打在标题行首")
check(any("https://example.test/" in l for l in lines), "URL 单独成行")
check(any(l.startswith(" " * 7) and l.strip() for l in lines),
      "续行缩进 = 2 格边距 + 5 格编号列，不会顶着编号那列")

print("== 8. 同一个工具调用重复出现时被点名 ==")
# This is the shape a runaway takes: the model re-issues an identical search
# until a ceiling stops it. Before the layer-cap fix there was no ceiling.
console, buf = new_console()
view = cli_live.LiveView(console, None)


def tool(node, name, query):
    return record("get_subtask_process", node_id=node, subtask_id=node,
                  process={"tool_name": name, "arguments": {"query": query}})


view.feed(tool("n1", "search_arxiv", "GNN drug discovery"))
view.feed(tool("n1", "search_arxiv", "GNN drug discovery"))
quiet = buf.getvalue()
check("原地打转" not in quiet, "两次相同的调用还说得过去，先不吭声")
for _ in range(3):
    view.feed(tool("n1", "search_arxiv", "GNN drug discovery"))
view.feed(tool("n1", "search_arxiv", "另一件事"))
view.feed(tool("n2", "search_arxiv", "GNN drug discovery"))
loud = buf.getvalue()
check("已用相同参数第 3 次调用 search_arxiv" in loud, "第三次同样参数时警告")
check("原地打转" in loud and "Ctrl+C" in loud, "警告说清后果和怎么办")
check("GNN drug discovery" in loud, "警告里带上是哪个参数")
s = view.summary()
# n1 的重复那一条共 5 次 → 4 次是白跑的；换参数的和另一个节点的各算一次正常调用。
check(s["tool_calls"] == 7, f"工具调用总数 {s['tool_calls']} == 7")
check(s["repeated_tool_calls"] == 4, f"重复次数 {s['repeated_tool_calls']} == 4")
check("其中 4 次是重复调用" in view._footer(), "跑动时的页脚也在计")

console, buf = new_console()
view = cli_live.LiveView(console, None)
view.feed(tool("n1", "search_web", "a"))
view.feed(tool("n2", "search_web", "a"))
view.feed(tool("n3", "search_web", "a"))
check("原地打转" not in buf.getvalue(),
      "三个不同节点查同一个词是正常并行，不该报警")
check(view.summary()["repeated_tool_calls"] == 0, "跨节点同名不算重复")

console, buf = new_console()
cli_live.print_summary(console, {
    "secs": 120.0, "layers": 3, "model_calls": 40, "tool_calls": 18,
    "repeated_tool_calls": 9, "refs": 0, "usage": {}, "usage_seen": False,
    "usage_by_stage": {}, "error": None})
line = buf.getvalue()
print(line)
check("18 次工具调用（9 次重复）" in line, "小结里把重复次数摊开")
check("原地打转" in line, "小结后面跟一句怎么处理")

console, buf = new_console()
cli_live.print_summary(console, {
    "secs": 30.0, "layers": 1, "model_calls": 5, "tool_calls": 2,
    "repeated_tool_calls": 1, "refs": 0, "usage": {}, "usage_seen": False,
    "usage_by_stage": {}, "error": None})
line = buf.getvalue()
check("2 次工具调用（1 次重复）" in line, "个别重复照样标出来")
check("原地打转" not in line, "但不足以升格成警告")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过")

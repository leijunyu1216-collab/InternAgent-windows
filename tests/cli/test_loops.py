"""Offline checks for the loops that used to keep calling tools forever.

These drive the real dr_agents classes with scripted models and counting fake
tools, because every bug here only shows at loop level: the planner re-building
a cyclic graph forever, and the executor re-issuing a tool call it had already
made in the same subtask.

Importing that tree needs the project dependencies (loguru, tiktoken, the tool
packages…), which a CLI-only install does not have, so the whole suite skips
rather than fails when the import does not work.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DR = os.path.join(REPO, "internagent", "mas", "agents", "dr_agents")

sys.path.insert(0, DR)

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class _Silent:
    def __getattr__(self, _name):
        return lambda *a, **k: None


try:
    from agents.task.execution_agent import ExecutionAgent
    from agents.global_planner_agent import GlobalPlannerAgent
except Exception as exc:                                    # noqa: BLE001
    print(f"跳过：dr_agents 在本机不可导入（{type(exc).__name__}: {exc}）")
    print("     这不影响 CLI 界面自检；装上 requirements.txt 后再跑这一套。")
    sys.exit(0)

# Deliberately not named like a search tool: _is_search_tool() would then pull
# the reference-manager path into every assertion below.
TOOL = {"name": "fetch_records", "description": "d",
        "required_parameters": ["query"],
        "parameters": {"query": {"type": "string"}}}


def call(name, args):
    """One OpenAI-shaped tool call, as the vendor returns it."""
    return {"id": "c", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def turn(tool_calls=(), content=""):
    return {"role": "assistant", "content": content, "tool_calls": list(tool_calls)}


class FakeModel:
    """Answers from a scripted list; the last entry repeats, so a loop that
    should have ended shows up as a wrong call count instead of a hung test."""

    def __init__(self, script):
        self.script = list(script)
        self.asks = 0
        self.messages_seen = []

    def generate_with_tools(self, messages=None, tools=None):
        self.asks += 1
        self.messages_seen.append(list(messages or []))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return {"choices": [{"message": item}]}


class FakeTools:
    def __init__(self, raises=()):
        self.calls = []
        self.raises = set(raises)

    def call_tool(self, name, **arguments):
        key = json.dumps(arguments, sort_keys=True)
        self.calls.append((name, key))
        if key in self.raises:
            raise RuntimeError("429 too many requests")
        return {"hits": [arguments.get("query", "")]}

    def list_tools(self):
        return [TOOL["name"]]

    def get_simple_tool_info(self, tool):
        return tool

    def get_tool_info(self, name):
        return TOOL


def probe_agent(script, raises=(), max_tool_calls=5):
    agent = ExecutionAgent.__new__(ExecutionAgent)
    agent._logger = _Silent()
    agent.model = agent.summarizer_model = "fake"
    agent.model_kwargs = {}
    agent.config = {}
    agent.tools = [TOOL]
    agent.tool_map = {TOOL["name"]: TOOL}
    agent.tool_manager = FakeTools(raises)
    agent.model_instance = FakeModel(script)
    agent.reference_manager = _Silent()
    agent.execution_prompt = ("{task} {query} {history_subtasks} {subtask} "
                              "{knowledge_info} {file_path}")
    agent.summary_prompt = "{subtask} {messages} {knowledge_info}"
    agent.need_memory = False
    agent.memory_prompt = None
    agent.base_retrieve_score_threshold = 0.3
    agent.append_retrieve_score_threshold = 0.65
    agent.max_tool_calls = max_tool_calls
    agent.node_id = "1"
    agent.subtask_id = 1
    agent._tool_results = {}
    agent.events = []
    agent.send_redis_event = lambda name, payload: agent.events.append(name)
    agent._generate_subtask_response = lambda *a, **k: json.dumps(
        {"success": True, "summary": "done"})
    return agent


def run(agent):
    return agent.execute(
        subtask="找一找 X", query="总任务",
        context={"task": "总任务", "node_id": "1", "subtask_id": 1})


print("== 1. 同一子任务里完全相同的调用只真发一次 ==")
a = probe_agent([turn([call("fetch_records", {"query": "gnn"})]),
                 turn([call("fetch_records", {"query": "gnn"}),
                       call("fetch_records", {"query": "gnn 2024"})]),
                 turn(content="收工")])
res = run(a)
check(res.get("completed") is True, f"子任务正常收尾（{res.get('error')}）")
check(len(a.tool_manager.calls) == 2,
      f"两个查询各真发一次（{len(a.tool_manager.calls)}）")
check([c[1] for c in a.tool_manager.calls].count(json.dumps({"query": "gnn"}, sort_keys=True)) == 1,
      "重复的 gnn 没有第二次打到工具上")
check(a.model_instance.asks == 3,
      f"混着新调用的那一轮之后还能继续（问了 {a.model_instance.asks} 轮）")
check(a.events.count("get_subtask_process") == 2,
      "只有真正执行的那两次上报了工具事件")
tool_msgs = [m for m in res["messages"] if m.get("role") == "tool"]
check(len(tool_msgs) == 3, "复用的那一次照样给模型一条工具消息，不会留下空洞")

print("== 2. 整轮都是重复 → 立刻结束，不再烧完预算 ==")
a = probe_agent([turn([call("fetch_records", {"query": "gnn"})]),
                 turn([call("fetch_records", {"query": "gnn"})])],
                max_tool_calls=5)
run(a)
check(a.model_instance.asks == 2,
      f"第 2 轮就停了，模型被问 {a.model_instance.asks} 轮（预算 5）")
check(len(a.tool_manager.calls) == 1, "真发 1 次工具")

print("== 3. 换了参数的同类调用不会被误合并 ==")
a = probe_agent([turn([call("fetch_records", {"query": "gnn"})]),
                 turn([call("fetch_records", {"query": "gnn 2024"})]),
                 turn(content="收工")])
run(a)
check(len(a.tool_manager.calls) == 2,
      f"两次都真发了（{len(a.tool_manager.calls)}）")

print("== 4. 参数顺序不同、内容相同，仍算重复 ==")
a = probe_agent([turn([call("fetch_records", {"query": "x", "limit": 5})]),
                 turn([call("fetch_records", {"limit": 5, "query": "x"})]),
                 turn(content="收工")])
run(a)
check(len(a.tool_manager.calls) == 1, "dict 的键顺序不影响指纹")

print("== 5. 工具报错不再被记成调用成功 ==")
boom = json.dumps({"query": "boom"}, sort_keys=True)
a = probe_agent([turn([call("fetch_records", {"query": "boom"})])], raises=[boom])
res = run(a)
check(a.model_instance.asks == 1,
      f"整批失败即结束子任务，模型被问 {a.model_instance.asks} 轮")
tool_msgs = [m for m in res["messages"] if m.get("role") == "tool"]
check(bool(tool_msgs) and str(tool_msgs[-1]["content"]).startswith("Error"),
      f"模型看到的工具消息写着 Error（{tool_msgs[-1]['content'] if tool_msgs else '无'}）")
check(res.get("tool_calls") == 1, "失败的那一轮计入预算，不会被悄悄抹掉")

print("== 6. 缓存只在子任务内生效 ==")
a = probe_agent([turn([call("fetch_records", {"query": "gnn"})]),
                 turn(content="ok")])
run(a)
a.model_instance.script = [turn([call("fetch_records", {"query": "gnn"})]),
                           turn(content="ok")]
run(a)
check(len(a.tool_manager.calls) == 2,
      f"下一个子任务重新真发（{len(a.tool_manager.calls)} 次）")

# ---------------------------------------------------------------- planner --

CYCLE = {"nodes": [{"node_id": "n1", "task": "a", "type": "search"},
                   {"node_id": "n2", "task": "b", "type": "search"}],
         "edges": [{"from": "n1", "to": "n2", "relationship": "r"},
                   {"from": "n2", "to": "n1", "relationship": "r"}]}
MALFORMED = {"nodes": [{"id": "n1", "task": "a", "type": "search"}], "edges": []}
OK_PLAN = {"nodes": [{"node_id": "n1", "task": "a", "type": "search"},
                     {"node_id": "task", "task": "ans", "type": "answer"}],
           "edges": [{"from": "n1", "to": "task", "relationship": "r"}]}


class FakePlanner:
    """Stands in for execute_one_step: hands back a scripted plan and records
    what the loop believed the current graph was on each pass."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.graphs_seen = []
        self.hard_cap = 40          # a runaway loop must fail, not hang

    def __call__(self, graph_json, *args, **kwargs):
        self.calls += 1
        self.graphs_seen.append(graph_json)
        if self.calls > self.hard_cap:
            raise AssertionError("planner 调用次数超过硬上限")
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def probe_planner(script):
    p = GlobalPlannerAgent.__new__(GlobalPlannerAgent)
    p.logger = _Silent()
    p.max_iter = 2
    p.max_retries = 1
    p.max_nodes = 8
    p.need_memory = False
    p.memory_prompt = None
    p.tool_manager = FakeTools()
    p.model_instance = None
    fake = FakePlanner(script)
    p.execute_one_step = fake
    return p, fake


print("== 7. 带环的计划最多重建两次，然后交出 None 而不是无限重规划 ==")
p, fake = probe_planner([CYCLE])
out = p.execute("研究一下 GNN")
check(fake.calls == 6, f"2 轮 × 3 次重建 = 6 次模型调用，实际 {fake.calls}")
check(out is None and p.graph is None, "失败时不留下半吊子的图")
check("null" not in fake.graphs_seen,
      "重规划时发给模型的是种子计划，不是 json.dumps(None)")

print("== 8. 字段名不对的计划算「无效计划」，不再抛 KeyError ==")
p, fake = probe_planner([MALFORMED])
try:
    out = p.execute("研究一下 GNN")
    crashed = None
except Exception as exc:                                # noqa: BLE001
    out, crashed = "crashed", exc
check(crashed is None, f"没有异常逃出 execute（{crashed}）")
check(out is None and p.graph is None, "同样按失败收尾")

print("== 9. 坏一次之后修好了就继续 ==")
p, fake = probe_planner([CYCLE, OK_PLAN])
p.execute("研究一下 GNN")
check(p.graph is not None and len(p.graph.nodes) == 2,
      f"拿到无环计划就建图成功（{p.graph is not None}）")
check(fake.calls == 2, f"一轮规划 2 次调用（{fake.calls}）")

print("== 10. 多轮会话里不会沿用上一轮的图 ==")
p, fake = probe_planner([OK_PLAN])
p.execute("第一问")
first = p.graph
p.execute_one_step = FakePlanner([RuntimeError("401 invalid api key")])
p.execute("第二问")
check(first is not None, "第一问确实建出了图")
check(p.graph is None, "第二问失败后 graph 被清空，而不是沿用第一问的")


print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过")

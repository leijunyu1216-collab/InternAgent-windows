"""Record a realistic event stream with a fake upstream, then replay it via the
`replay` subcommand. Exercises acquire() -> recorder -> replay -> LiveView with
no API key."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import time
import types


from openai.resources.chat.completions.completions import Completions

TRUE_ORIGINAL = Completions.create
OUT = os.path.join(HERE, "fake_run.jsonl")


def chunk(content=None, reasoning=None, finish=None):
    delta = types.SimpleNamespace(content=content, reasoning_content=reasoning,
                                  tool_calls=None)
    return types.SimpleNamespace(
        choices=[] if not (content or reasoning or finish) else [
            types.SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None, model="deepseek-flash", id="fake")


REASON = ("用户的问题横跨方法与应用两侧，先把范围收窄到近五年。\n"
          "第一步应当检索综述以建立子领域框架，否则后面各节点会重复检索同样的内容。\n"
          "评测口径是否可比是一个隐藏风险，需要单独安排一个节点去核实。")
PLAN = ('{"nodes": [{"id": "1", "task": "检索综述"}, '
        '{"id": "2", "task": "分子性质预测"}, {"id": "3", "task": "评测口径核实"}]}')

SCRIPT = [("reasoning", REASON), ("content", PLAN)]


def fake_create(self, *args, **kwargs):
    def gen():
        for kind, text in SCRIPT:
            step = max(1, len(text) // 6)
            for i in range(0, len(text), step):
                piece = text[i:i + step]
                yield chunk(**{kind: piece})
                time.sleep(0.01)
        yield chunk(finish="stop")
    return gen()


Completions.create = fake_create

import cli_events
from dr_agents.agents.global_planner_agent import GlobalPlannerAgent
from dr_agents.agents.execution_agent import ExecutionAgent

open(OUT, "w", encoding="utf-8").close()
planner = GlobalPlannerAgent()
node2 = ExecutionAgent(node_id="2", subtask_id=2, task_id="fake-task")

with cli_events.EventRecorder(OUT) as rec:
    rec.mark("workflow_start", question="近五年图神经网络在药物发现中的进展与开放问题？",
             mode="qa", model="deepseek-flash")
    planner.plan("q", max_iter=10)
    rec.mark("get_subtasks", subtasks=[
        {"task": "检索近五年 GNN 药物发现综述"},
        {"task": "分子性质预测代表工作与基准"},
        {"task": "核实各子领域评测口径是否可比"}])
    rec.mark("get_current_execution_nodes", node_ids=["1", "2", "3"])
    node2.plan_subtask("分子性质预测")
    rec.mark("get_subtask_process", node_id="2", subtask_id=2,
             process={"tool_name": "search_arxiv",
                      "arguments": {"query": "molecular property prediction GNN"}})
    rec.mark("get_node_result", node_id="2",
             result={"success": True, "summary": "整理 9 个代表工作与 4 个基准"})
    rec.mark("start_answer_node", node_id="answer")
    rec.mark("workflow_end", answer_chars=4200)

Completions.create = TRUE_ORIGINAL

# The recording is committed as a fixture and the recorder stamps the path it was
# handed into its first event, so normalise it to repo-relative: regenerating the
# file must not put a machine-specific directory back into the repository.
import json

with open(OUT, encoding="utf-8") as fh:
    _lines = fh.read().splitlines(True)
_head = json.loads(_lines[0])
if isinstance(_head.get("data"), dict) and "path" in _head["data"]:
    _repo = os.path.dirname(os.path.dirname(HERE))
    _head["data"]["path"] = os.path.relpath(OUT, _repo).replace(os.sep, "/")
    _lines[0] = json.dumps(_head, ensure_ascii=False) + "\n"
with open(OUT, "w", encoding="utf-8", newline="") as fh:
    fh.writelines(_lines)

print("recorded ->", OUT)
for name, n in cli_events.summarize(OUT)["counts"]:
    print(f"  {n:>4}  {name}")

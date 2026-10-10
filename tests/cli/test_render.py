"""Render a synthetic-but-realistic qa run through LiveView and dump the text."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import io
import time


from rich.console import Console

import cli_live

buf = io.StringIO()
console = Console(file=buf, width=100, force_terminal=False, color_system=None)
view = cli_live.LiveView(console)

Q = "近五年图神经网络在药物发现中的主要进展与开放问题有哪些？"
REASON = ("用户想了解 GNN 在药物发现中的进展。需要先界定时间范围（近五年）与子领域："
          "分子性质预测、逆合成、蛋白质-配体对接。\n"
          "第一步应该检索综述类文献建立框架，第二步再针对每个子领域找代表性工作，"
          "第三步对比它们报告的基准数据集与指标是否可比。\n"
          "开放问题部分需要单独检索，因为综述往往滞后。")
PLAN = ('{"nodes": [{"id": "1", "task": "检索 GNN 药物发现综述"}, '
        '{"id": "2", "task": "分子性质预测代表工作"}, '
        '{"id": "3", "task": "逆合成与对接方法"}]}')

seq = []


def add(event, **data):
    seq.append({"event": event, "agent": None, "data": data,
                "t": len(seq) * 0.1, "tid": 1})


add("workflow_start", question=Q, mode="qa", model="deepseek-flash")
add("model_call_start", call=1, stage="全局规划", detail="生成执行图 · 第 1/10 轮",
    prompt=Q, stream=True)
add("model_call_chunk", call=1, stage="全局规划", kind="reasoning",
    text=REASON[:120], chars=120)
CHECKPOINT_1 = len(seq)
add("model_call_chunk", call=1, stage="全局规划", kind="reasoning",
    text=REASON[120:], chars=len(REASON) - 120)
add("model_call_chunk", call=1, stage="全局规划", kind="content",
    text=PLAN, chars=len(PLAN))
add("model_call_end", call=1, stage="全局规划", detail="生成执行图 · 第 1/10 轮",
    secs=18.4, chars=len(REASON) + len(PLAN), text=PLAN, reasoning=REASON)
add("get_subtasks", subtasks=[
    {"task": "检索近五年 GNN 药物发现综述，建立子领域框架"},
    {"task": "分子性质预测：代表性工作与基准"},
    {"task": "逆合成规划与蛋白质-配体对接方法对比"},
    {"task": "开放问题：数据稀缺、可解释性、评测不一致"}])
add("get_current_execution_nodes", node_ids=["1", "2", "3"])
add("model_call_start", call=2, stage="节点执行", detail="选择下一步",
    node_id="1", subtask_id=1, stream=True)
add("model_call_end", call=2, stage="节点执行", detail="选择下一步",
    secs=4.1, chars=210, text='{"tool": "search_web", "query": "GNN drug discovery review"}',
    reasoning="", node_id="1", subtask_id=1)
add("get_subtask_process", node_id="1", subtask_id=1, process={
    "tool_name": "search_web",
    "arguments": {"query": "graph neural network drug discovery review 2021-2025"}})
add("model_call_start", call=3, stage="节点执行", detail="选择下一步",
    node_id="2", subtask_id=2, stream=True)
add("model_call_chunk", call=3, stage="节点执行", kind="reasoning",
    text="性质预测的基准很多，先限定 MoleculeNet 与 OGB 两套，再逐个查代表工作的报告数字。",
    chars=48)
CHECKPOINT_2 = len(seq)
add("model_call_end", call=3, stage="节点执行", detail="选择下一步", secs=6.7,
    chars=180, text='{"tool": "read_paper"}', reasoning="", node_id="2", subtask_id=2)
add("get_subtask_process", node_id="2", subtask_id=2, process={
    "tool_name": "search_arxiv",
    "arguments": {"query": "molecular property prediction GNN benchmark", "limit": 20}})
add("get_subtask_result", node_id="1", subtask_id=1, result={
    "success": True, "summary": "找到 14 篇综述，覆盖三个子领域"})
add("get_node_result", node_id="1", result={
    "success": True, "summary": "找到 14 篇综述，覆盖三个子领域"})
add("get_subtask_result", node_id="2", subtask_id=2, result={
    "success": True, "summary": "整理 9 个代表性工作与 4 个基准数据集"})
add("get_node_result", node_id="2", result={
    "success": True, "summary": "整理 9 个代表性工作与 4 个基准数据集"})
add("get_node_result", node_id="3", result={
    "success": False, "summary": "对接方法检索超时，仅得到 2 篇"})
add("get_coordinator_modification", modifications=[{"type": "add_node"}])
add("get_graph_after_coordinator", node_ids=["4"])
add("start_answer_node", node_id="answer")
add("model_call_start", call=9, stage="答案合成", detail="生成提纲", stream=True)
add("model_call_chunk", call=9, stage="答案合成", kind="content",
    text=("# 图神经网络在药物发现中的进展\n\n## 一、子领域划分\n\n"
          "近五年的工作大致沿三条线推进：分子性质预测、逆合成规划、以及蛋白质-配体对接。"
          "三者的图表示方式并不统一，这也是后续比较基准时的主要障碍。"), chars=110)
CHECKPOINT_3 = len(seq)
add("model_call_end", call=9, stage="答案合成", detail="生成提纲", secs=22.9,
    chars=4200, text="# 图神经网络在药物发现中的进展\n\n（正文 4200 字）",
    reasoning="")
add("get_cited_references", references=[{"id": i} for i in range(1, 9)])
add("model_call_start", call=10, stage="协调器", detail="调整执行图", stream=True)
add("model_call_error", call=10, stage="协调器", detail="调整执行图", secs=3.2,
    error="APIError", message="429 Too Many Requests: rate limit exceeded")
LONG = ("这一节需要判断三个子领域的评测口径是否可比。" * 40)
add("model_call_start", call=11, stage="答案合成", detail="撰写章节", stream=True)
add("model_call_end", call=11, stage="答案合成", detail="撰写章节", secs=31.5,
    chars=len(LONG) + 900, text="", reasoning=LONG + "末尾还有一段补充说明。")
add("workflow_end", answer_chars=4200)


def dump_live(tag):
    live = Console(width=100, force_terminal=True, color_system="standard",
                   file=io.StringIO())
    live.print(view.render())
    print(f"\n----- 活动区快照 {tag} " + "-" * 46)
    print(live.file.getvalue().rstrip())


for i, rec in enumerate(seq):
    if i == CHECKPOINT_1:
        dump_live("① 规划中，模型正在输出思考")
    if i == CHECKPOINT_2:
        dump_live("② 三节点并行执行")
    if i == CHECKPOINT_3:
        dump_live("③ 合成答案，正文流式追加")
    view.feed(rec)

print("\n----- 滚动区（永久输出） " + "-" * 46)
print(buf.getvalue().rstrip())

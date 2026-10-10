"""Offline checks for how the CLI overrides the workflow's YAML config.

DRAgent merges overrides with a *shallow* dict.update (dr_agent.py:137), so every
override has to carry the whole section it touches. Both bugs this suite pins came
from forgetting that: --model once deleted the per-stage model choices, and a
nested path like execution.max_tool_calls once wrote into the wrong dict.
"""
import argparse
import atexit
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import internagent_cli as ic

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ns(**kw):
    base = dict(model=None, mode="qa", max_workers=None,
                max_tool_calls=None, max_layers=None)
    base.update(kw)
    return argparse.Namespace(**base)


TMP = tempfile.mkdtemp(prefix="internagent_config_")
atexit.register(shutil.rmtree, TMP, True)

print("== 1. 读的是本机真实的那份 config ==")
ge = ic._config_section("qa", "global_execution")
check(ge.get("max_workers") == 10, "global_execution.max_workers 读到了")
check(isinstance(ge.get("execution"), dict), "嵌套的 execution 段读到了")
check(ic._config_section("qa", "nosuchsection") == {},
      "段不存在时给空字典，不报错")
check(ic._config_section("不存在的模式", "main") == {},
      "config 文件不存在时同样给空字典")

print("\n== 2. 嵌套路径写在该写的地方 ==")
merged = ic._section_override("qa", "global_execution",
                              **{"max_workers": 2,
                                 "execution.max_tool_calls": 1})
check(merged["max_workers"] == 2, "平铺的键改掉了")
check(merged["execution"]["max_tool_calls"] == 1,
      "execution.max_tool_calls 改的是 execution 子段，不是别处")
check("execution" not in merged["execution"],
      "没有把子段套进自己（链式赋值曾经造成这个）")
check(all(k in merged["execution"] for k in ("max_tool_calls", "need_memory", "tools")),
      "execution 段的兄弟键（need_memory/tools）还在")
check("planner" in merged and "need_memory" in merged,
      "global_execution 段的兄弟键还在")
check(merged is not ge, "改出来的是新字典，没有写回 config 文件")
with open(os.path.join(ic.REPO, "internagent", "mas", "agents",
                       "dr_agents", "config_qa.yaml"), encoding="utf-8") as fh:
    check("max_workers: 10" in fh.read(), "config 文件本身没被改动")

print("\n== 3. 只给一个键时不抹掉整段 ==")
one = ic._section_override("qa", "global_execution", **{"max_workers": 3})
check(one["max_workers"] == 3 and one["execution"]["max_tool_calls"] == 5,
      "改 max_workers 不会顺手把工具轮数上限也改掉")

print("\n== 4. --model 仍只改默认模型 ==")
model = ic._model_override("qa", "deepseek-chat")
check(model["default_model"] == "deepseek-chat", "默认模型换掉了")
for key in ("global_planner_model", "synthesizer_model",
            "global_execution_model", "task_agent_model", "coordinator_model"):
    check(key in model, f"{key} 没有被丢掉")

print("\n== 5. 成本闸门的开关 ==")
check(ic._budget_overrides("qa", ns()) == {},
      "一个 --max-* 都没给时不发送任何覆盖")
both = ic._budget_overrides("qa", ns(max_workers=2, max_layers=3))
check(sorted(both) == ["global_execution", "main"],
      "给了哪几段就只覆盖哪几段")
check(both["main"]["max_iter"] == 3 and both["main"]["enable_coordinator"] is False,
      "main 段里 enable_coordinator 保住了")
check(ic._budget_overrides("qa", ns(max_tool_calls=4))["global_execution"]
      ["execution"]["max_tool_calls"] == 4,
      "--max-tool-calls 单独给也生效")

print("\n== 6. 界面上报的是真正生效的数字 ==")
line = ic._budget_summary("qa", ns(max_workers=2, max_tool_calls=1, max_layers=3))
check("≤2" in line and "≤1" in line and "≤3" in line,
      "三个自定值都出现在提示里")
default_line = ic._budget_summary("qa", ns())
check("≤10" in default_line and "≤5" in default_line,
      "没给参数时回落到 config 的默认值，不是 0 也不是空白")

print("\n== 7. --max-* 的取值校验 ==")
check(ic._pos_int("7") == 7, "正整数通过")
for bad in ("0", "-1", "abc"):
    try:
        ic._pos_int(bad)
        check(False, f"{bad!r} 应当被拒绝")
    except argparse.ArgumentTypeError:
        check(True, f"{bad!r} 被 argparse 拒绝")

print("\n== 8. 覆盖真的走到底层 agent ==")
try:
    os.environ.setdefault("OPENAI_API_KEY", "sk-offline-check")
    os.environ.setdefault("OPENAI_API_BASE_URL", "http://127.0.0.1:9/v1")
    from internagent.mas.agents.dr_agent import DRAgent
    agent = DRAgent(model="gpt-4o-mini", config={
        "mode": "qa",
        "workflow_config": ic._budget_overrides(
            "qa", ns(max_workers=2, max_tool_calls=1, max_layers=3))})
    wf = agent.workflow
    check(wf is not None, "工作流构建成功")
    check(wf.global_execution_agent.max_workers == 2,
          "GlobalExecutionAgent 用的是覆盖后的并发数")
    check(wf.config["global_execution"]["execution"]["max_tool_calls"] == 1,
          "TaskWorkflow 拿到的执行段里，工具轮数上限是覆盖后的值")
    check(wf.config["main"]["max_iter"] == 3, "图层上限走的是覆盖后的 main 段")
    check(wf.config["global_execution"]["planner"]["max_subtasks"] == 2,
          "同一份 workflow_config 的其它段仍然完整")
    # The last hop: TaskWorkflow hands global_execution.execution to
    # ExecutionAgent, which reads max_tool_calls off it (execution_agent.py:101).
    DR = os.path.join(ic.REPO, "internagent", "mas", "agents", "dr_agents")
    sys.path.insert(0, DR)
    from agents.task.execution_agent import ExecutionAgent
    worker = ExecutionAgent(model="gpt-4o-mini", tool_mode="test",
                            config=wf.config["global_execution"]["execution"])
    check(worker.max_tool_calls == 1,
          "执行器真正用的工具轮数上限就是闸门给的值")
except Exception as exc:                                    # noqa: BLE001
    print(f"  skip  本机无法导入 dr_agents（{type(exc).__name__}: {exc}）")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for msg in FAILS:
        print("  - " + msg)
    sys.exit(1)
print("配置覆盖自检全部通过")

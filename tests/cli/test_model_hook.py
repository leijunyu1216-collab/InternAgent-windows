"""Offline harness for the cli_events model hook: fake upstream, real patch."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import time
import types


import cli_events
from openai.resources.chat.completions.completions import Completions

TRUE_ORIGINAL = Completions.create
SCENARIO = {"mode": "text"}
CALLS = []


def chunk(content=None, reasoning=None, tool_calls=None, finish=None,
          usage=None, model=None, id=None):
    empty = not any((content, reasoning, tool_calls, finish))
    delta = types.SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    return types.SimpleNamespace(
        choices=[] if empty else [types.SimpleNamespace(
            delta=delta, finish_reason=finish)],
        usage=usage, model=model, id=id)


def tc(index, id=None, name=None, arguments=None):
    return types.SimpleNamespace(
        index=index, id=id, type="function",
        function=types.SimpleNamespace(name=name, arguments=arguments))


def fake_original(self, *args, **kwargs):
    CALLS.append(dict(kwargs))
    mode = SCENARIO["mode"]
    if mode == "text":
        assert kwargs.get("stream") is True, "plain text should be streamed"
        return iter([
            chunk(reasoning="想一想，", model="deepseek-flash", id="r1"),
            chunk(reasoning="先查文献。"),
            chunk(content="好的，"),
            chunk(content="我将分三步。", finish="stop"),
            chunk(usage=types.SimpleNamespace(total_tokens=42)),
        ])
    if mode == "tools":
        assert not kwargs.get("stream"), "tool calls must stay non-streaming"
        msg = types.SimpleNamespace(
            content=None, tool_calls=[types.SimpleNamespace(
                id="c1", function=types.SimpleNamespace(
                    name="search_web", arguments='{"q":"x"}'))])
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="tool_calls")],
            usage=None, model="deepseek-flash", id="t1")
    if mode == "delta_tools":
        assert kwargs.get("stream") is True
        return iter([
            chunk(tool_calls=[tc(0, id="c9", name="search_", arguments='{"q"')]),
            chunk(tool_calls=[tc(0, name="web", arguments=':"y"}')]),
            chunk(finish="tool_calls"),
        ])
    if mode == "reject_opts":
        if kwargs.get("stream_options"):
            raise RuntimeError("400 Bad Request: unknown parameter stream_options")
        assert kwargs.get("stream") is True, "streaming must survive the fallback"
        return iter([chunk(content="有流式但没有用量", finish="stop")])
    if mode == "reject_stream":
        if kwargs.get("stream"):
            raise RuntimeError("400 Bad Request: stream is not supported")
        msg = types.SimpleNamespace(content="非流式结果", tool_calls=None)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")],
            usage=None, model="deepseek-flash", id="n1")
    if mode == "cancel":
        def gen():
            yield chunk(content="前半段")
            cli_events.request_cancel()
            yield chunk(content="不该出现")
        return gen()
    raise AssertionError(mode)


EVENTS = []
Completions.create = fake_original
cli_events._T0 = time.monotonic()
cli_events.subscribe(EVENTS.append)
cli_events._install_model_hook()

failures = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {extra}"))
    if not cond:
        failures.append(label)


def ev(name):
    return [e for e in EVENTS if e["event"] == name]


def reset():
    del EVENTS[:]
    del CALLS[:]
    cli_events.clear_cancel()


from dr_agents.agents.global_planner_agent import GlobalPlannerAgent
from dr_agents.agents.execution_agent import ExecutionAgent

# ---------------------------------------------------------------- case A
print("\n[A] plain text call from the planner -> streamed, reasoning captured")
reset()
SCENARIO["mode"] = "text"
planner = GlobalPlannerAgent()
# plan() -> execute_one_step() -> _generate_plan() -> create: the stage comes
# from the innermost frame, the round counter from two frames up.
resp = planner.plan("q", max_iter=10)
msg = resp.choices[0].message
check("content reassembled", msg.content == "好的，我将分三步。", msg.content)
check("reasoning reassembled", msg.reasoning_content == "想一想，先查文献。",
      msg.reasoning_content)
check("tool_calls is None", msg.tool_calls is None)
check("usage passthrough", getattr(resp.usage, "total_tokens", None) == 42)
check("model/id passthrough", resp.model == "deepseek-flash" and resp.id == "r1")
check("finish_reason", resp.choices[0].finish_reason == "stop")
start = ev("model_call_start")
check("one start event", len(start) == 1)
check("stage from stack walk", start and start[0]["data"]["stage"] == "全局规划",
      start and start[0]["data"])
check("call site labelled, not a raw def name",
      start and start[0]["data"]["detail"].startswith("生成执行图"),
      start and start[0]["data"]["detail"])
check("round counter found two frames up",
      start and "3/10" in start[0]["data"]["detail"],
      start and start[0]["data"]["detail"])
check("prompt preview", start and start[0]["data"]["prompt"] == "请规划研究步骤",
      start and start[0]["data"]["prompt"])
check("stream flag on", start and start[0]["data"]["stream"] is True)
chunks = ev("model_call_chunk")
check("chunks emitted", len(chunks) >= 2, len(chunks))
check("reasoning tagged", any(c["data"]["kind"] == "reasoning" for c in chunks))
check("content tagged", any(c["data"]["kind"] == "content" for c in chunks))
check("chunk text lossless",
      "".join(c["data"]["text"] for c in chunks if c["data"]["kind"] == "reasoning")
      == "想一想，先查文献。")
end = ev("model_call_end")
check("one end event", len(end) == 1)
check("end carries text", end and end[0]["data"]["text"] == "好的，我将分三步。")
check("end carries reasoning", end and end[0]["data"]["reasoning"] == "想一想，先查文献。")
check("end carries secs", end and isinstance(end[0]["data"]["secs"], float))
check("stream actually requested", CALLS and CALLS[0].get("stream") is True)

# ---------------------------------------------------------------- case B
print("\n[B] tool call -> left non-streaming, response untouched")
reset()
SCENARIO["mode"] = "tools"
agent = ExecutionAgent(node_id="node_3", subtask_id=1, task_id="t")
resp = agent.call_tool_model([{"role": "user", "content": "hi"}],
                             [{"type": "function", "function": {"name": "search_web"}}])
check("tool_calls preserved", resp.choices[0].message.tool_calls[0].id == "c1")
start = ev("model_call_start")
check("stream flag off", start and start[0]["data"]["stream"] is False)
check("node identity attached", start and start[0]["data"]["node_id"] == "node_3",
      start and start[0]["data"])
check("subtask identity attached", start and start[0]["data"]["subtask_id"] == 1)
check("stage is node execution", start and start[0]["data"]["stage"] == "节点执行")
check("no chunk events", not ev("model_call_chunk"))
check("end event still fires", len(ev("model_call_end")) == 1)
check("upstream not restreamed", CALLS and not CALLS[0].get("stream"))

# ---------------------------------------------------------------- case C
print("\n[C] provider rejects stream -> falls back once, then stays off")
reset()
SCENARIO["mode"] = "reject_stream"
cli_events._STREAM_OK = True
resp = planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
check("fallback returned content", resp.choices[0].message.content == "非流式结果",
      resp.choices[0].message.content)
check("fallback announced", len(ev("model_stream_disabled")) == 1)
check("stream disabled globally", cli_events._STREAM_OK is False)
check("upstream hit twice", len(CALLS) == 2, len(CALLS))
reset()
resp = planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
check("later call not restreamed", len(CALLS) == 1 and not CALLS[0].get("stream"))
check("no second fallback event", not ev("model_stream_disabled"))
cli_events._STREAM_OK = True

# ---------------------------------------------------------------- case D
print("\n[D] tool_calls arriving as stream deltas are reassembled")
reset()
SCENARIO["mode"] = "delta_tools"
resp = planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
calls = resp.choices[0].message.tool_calls
check("one reassembled tool call", calls is not None and len(calls) == 1, calls)
check("name concatenated", calls and calls[0].function.name == "search_web",
      calls and calls[0].function.name)
check("arguments concatenated",
      calls and calls[0].function.arguments == '{"q":"y"}',
      calls and calls[0].function.arguments)
check("id kept", calls and calls[0].id == "c9")

# ---------------------------------------------------------------- case E
print("\n[E] Ctrl+C mid-stream -> CliCancelled, and it is not an Exception")
reset()
SCENARIO["mode"] = "cancel"
raised = None
try:
    planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
except BaseException as exc:
    raised = exc
check("raised CliCancelled", isinstance(raised, cli_events.CliCancelled), raised)
check("not catchable by except Exception", not isinstance(raised, Exception))
check("cancel event emitted", len(ev("model_call_cancelled")) == 1)
check("no end event after cancel", not ev("model_call_end"))

swallowed = False
cli_events.clear_cancel()
try:
    cli_events.request_cancel()
    try:
        planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
    except Exception:
        swallowed = True
except BaseException:
    pass
check("an `except Exception` retry loop cannot swallow it", not swallowed)
cli_events.clear_cancel()

# ---------------------------------------------------------------- case F
print("\n[F] pre-call cancel short-circuits before touching the network")
reset()
SCENARIO["mode"] = "text"
cli_events.request_cancel()
raised = None
try:
    planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
except BaseException as exc:
    raised = exc
check("raised before any upstream call", not CALLS, CALLS)
check("is CliCancelled", isinstance(raised, cli_events.CliCancelled))
cli_events.clear_cancel()

# ---------------------------------------------------------------- case H
print("\n[H] usage reported, and stream_options rejected without losing stream")
reset()
SCENARIO["mode"] = "text"
cli_events._STREAM_OPTS = True
planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
end = ev("model_call_end")[0]["data"]
check("usage carried on the end event", end.get("usage") == {"total": 42}, end.get("usage"))
check("stream_options asked for once",
      CALLS[0].get("stream_options") == {"include_usage": True}, CALLS[0].keys())

reset()
SCENARIO["mode"] = "reject_opts"
cli_events._STREAM_OPTS = True
cli_events._STREAM_OK = True
resp = planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
check("content still returned", resp.choices[0].message.content == "有流式但没有用量",
      resp.choices[0].message.content)
check("only the two attempts", len(CALLS) == 2, len(CALLS))
check("second attempt drops the field", "stream_options" not in CALLS[1])
check("streaming stays enabled", cli_events._STREAM_OK is True)
check("opts disabled for good", cli_events._STREAM_OPTS is False)
check("no stream-disabled event", not ev("model_stream_disabled"))
end = ev("model_call_end")[0]["data"]
check("usage simply absent, not zero", end.get("usage") == {}, end.get("usage"))

reset()
SCENARIO["mode"] = "text"
planner.execute_one_step("{}", "q", current_iter=1, max_iter=10)
check("later calls stop sending the rejected field",
      "stream_options" not in CALLS[0], CALLS[0].keys())

# ---------------------------------------------------------------- case G
print("\n[G] unknown caller still labels the call")
reset()
SCENARIO["mode"] = "text"
resp = Completions.create(object(), model="m",
                          messages=[{"role": "user", "content": "x"}], stream=False)
check("generic stage", ev("model_call_start")[0]["data"]["stage"] == "模型调用",
      ev("model_call_start")[0]["data"])
check("still returns content", resp.choices[0].message.content == "好的，我将分三步。")

# ---------------------------------------------------------------- teardown
Completions.create = TRUE_ORIGINAL
cli_events.unsubscribe(EVENTS.append)
cli_events._T0 = None

print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)

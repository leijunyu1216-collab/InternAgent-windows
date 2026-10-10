"""Ctrl+C through run_with_live: does the workflow's is_executing guard free up?

That guard (global_execution_agent.py:95) is only released by execute_graph's
finally:, and the old code abandoned the worker thread on interrupt, so the
next REPL turn died with 图正在执行中. Cancellation has to reach the model call
as a BaseException for the finally to run.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import io
import signal
import threading
import time
import types


from rich.console import Console

import cli_events
import cli_live
from openai.resources.chat.completions.completions import Completions

TRUE_ORIGINAL = Completions.create
failures = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {extra}"))
    if not cond:
        failures.append(label)


def chunk(content=None, finish=None):
    delta = types.SimpleNamespace(content=content, reasoning_content=None,
                                  tool_calls=None)
    return types.SimpleNamespace(
        choices=[] if not (content or finish) else [types.SimpleNamespace(
            delta=delta, finish_reason=finish)],
        usage=None, model="m", id="i")


class Guard:
    """Mimics GlobalExecutionAgent: the flag clears only in finally."""

    def __init__(self):
        self.is_executing = False
        self.entered = False

    def execute_graph(self):
        if self.is_executing:
            raise RuntimeError("图正在执行中，请等待当前执行完成")
        self.is_executing = True
        self.entered = True
        try:
            # What the real one does inside: a model call wrapped in a retry
            # loop that swallows Exception.
            for attempt in range(3):
                try:
                    return Completions.create(
                        object(), model="deepseek-flash",
                        messages=[{"role": "user", "content": "q"}], stream=False)
                except Exception:
                    continue
            return None
        finally:
            self.is_executing = False


def slow_stream(self, *args, **kwargs):
    def gen():
        for i in range(400):
            yield chunk(content=f"片段{i}。")
            time.sleep(0.03)
        yield chunk(finish="stop")
    return gen()


def stuck(self, *args, **kwargs):
    # A tool blocked in a socket read: nothing to cancel cooperatively.
    time.sleep(30)
    return chunk(finish="stop")


Completions.create = slow_stream
cli_events._T0 = time.monotonic()
cli_events._install_model_hook()

print("\n[H] Ctrl+C during a streaming model call unwinds the workflow")
guard = Guard()
console = Console(file=io.StringIO(), width=100, force_terminal=False,
                  color_system=None)
threading.Timer(0.8, lambda: signal.raise_signal(signal.SIGINT)).start()
t0 = time.monotonic()
run = cli_live.run_with_live(console, guard.execute_graph)
result, error, interrupted, abandoned = run[:4]
took = time.monotonic() - t0
check("reported interrupted", interrupted is True)
check("did not have to abandon the thread", abandoned is False)
check("worker raised CliCancelled", isinstance(error, cli_events.CliCancelled),
      error)
check("is_executing released", guard.is_executing is False)
check("unwound promptly, not after the 30s stream", took < cli_live.CANCEL_GRACE,
      f"{took:.1f}s")

print("\n[I] the next turn is not blocked by the guard")
Completions.create = lambda self, *a, **k: types.SimpleNamespace(
    choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content="第二轮成功", tool_calls=None),
        finish_reason="stop")], usage=None, model="m", id="i2")
second = None
try:
    second = guard.execute_graph()
except RuntimeError as exc:
    second = exc
check("no 图正在执行中", not isinstance(second, RuntimeError), second)
check("second turn produced an answer",
      getattr(second, "choices", [None])[0].message.content == "第二轮成功", second)

print("\n[J] a worker stuck outside a model call is abandoned, and says so")
Completions.create = stuck
cli_live.CANCEL_GRACE = 0.6
guard2 = Guard()
console2 = Console(file=io.StringIO(), width=100, force_terminal=False,
                   color_system=None)
threading.Timer(0.3, lambda: signal.raise_signal(signal.SIGINT)).start()
run = cli_live.run_with_live(console2, guard2.execute_graph)
result, error, interrupted, abandoned = run[:4]
check("interrupted", interrupted is True)
check("abandoned flagged", abandoned is True)
check("guard still held (caller must rebuild)", guard2.is_executing is True)
out = console2.file.getvalue()
check("user told the thread is still running", "仍卡在工具调用里" in out, out[-200:])
cli_live.CANCEL_GRACE = 10.0

Completions.create = TRUE_ORIGINAL
cli_events._T0 = None
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)

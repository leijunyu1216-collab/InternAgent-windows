"""Does --timeout actually stop a run? It used to be a lie.

The old code wrapped DRAgent.execute in asyncio.wait_for, but that body is fully
synchronous, so the timer could only fire once the whole research run returned —
measured with a 1s deadline around a 3s sleep, and the sleep ran to completion.
A hung endpoint or a runaway tool loop therefore had no ceiling at all.

The deadline is now policed by the two threads that keep ticking: cli_live's
render loop (live runs) and a watchdog join (_ask's spinner path). Both stop the
run through cli_events.request_cancel(), the same flag Ctrl+C uses.
"""
import argparse
import asyncio
import io
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

from rich.console import Console

import cli_events
import cli_live
import internagent_cli as ic
from openai.resources.chat.completions.completions import Completions

REAL_CREATE = Completions.create
failures = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {extra}"))
    if not cond:
        failures.append(label)


def quiet_console():
    return Console(file=io.StringIO(), width=100, force_terminal=False,
                   color_system=None)


class Guard:
    """Mimics GlobalExecutionAgent: the flag clears only in run's finally."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.is_executing = False
        self.calls = 0

    def run(self):
        if self.is_executing:
            raise RuntimeError("图正在执行中，请等待当前执行完成")
        self.is_executing = True
        self.calls += 1
        try:
            return self.behaviour()
        finally:
            self.is_executing = False


def chatty(self, *args, **kwargs):
    """A well-behaved model stream: it honours the cancel flag between chunks,
    which is exactly what cli_events' hook does around every real call."""
    def gen():
        for i in range(400):
            if cli_events.cancel_requested():
                raise cli_events.CliCancelled("用户中断")
            yield types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    delta=types.SimpleNamespace(content=f"片段{i}。",
                                                reasoning_content=None,
                                                tool_calls=None),
                    finish_reason=None)],
                usage=None, model="m", id="i")
            time.sleep(0.02)
    return gen()


def wedged(self, *args, **kwargs):
    """A tool blocked in a socket read: nothing cooperative to cancel."""
    time.sleep(30)


Completions.create = chatty
cli_events._T0 = time.monotonic()
cli_events._install_model_hook()          # wraps `chatty`, as in production
HOOKED = Completions.create


def stream_turn():
    return "".join(c.choices[0].delta.content
                   for c in HOOKED(object(), model="m", messages=[], stream=True))


def live_run(behaviour, timeout):
    guard = Guard(behaviour)
    console = quiet_console()
    started = time.monotonic()
    run = cli_live.run_with_live(console, lambda: guard.run() or "answer",
                                 timeout=timeout)
    return run, guard, console.file.getvalue(), time.monotonic() - started


print("\n[A] a deadline during a streaming call stops the turn")
run, guard, out, took = live_run(stream_turn, 0.5)
check("reported timed_out", run.timed_out is True, run)
check("not reported as a Ctrl+C", run.interrupted is False, run)
check("stopped near the deadline, not after the whole stream", took < 3.0,
      f"{took:.1f}s")
check("but not before it", took >= 0.5, f"{took:.1f}s")
check("the model call unwound on CliCancelled",
      isinstance(run.error, cli_events.CliCancelled), repr(run.error))
check("is_executing released, so the workflow is reusable",
      guard.is_executing is False, "guard still held")
check("did not have to abandon the thread", run.abandoned is False, run)
check("the user sees the budget in the message",
      "已达" in out and "上限" in out, out[-200:])
check("the cancel flag is cleared for the next turn",
      cli_events.cancel_requested() is False)

print("\n[B] a run that finishes inside the deadline is untouched")
run, guard, out, took = live_run(lambda: "done", 5)
check("no timeout reported", run.timed_out is False, run)
check("result kept", run.result == "done", run)
check("returned immediately, not after 5s", took < 2.0, f"{took:.1f}s")

print("\n[C] a worker wedged in a socket read is abandoned, and says so")
Completions.create = wedged          # bypasses the hook: a tool, not a model call
cli_live.CANCEL_GRACE = 0.5
run, guard, out, took = live_run(lambda: Completions.create(object()), 0.4)
check("timed_out", run.timed_out is True, run)
check("abandoned flagged so the caller rebuilds", run.abandoned is True, run)
check("guard is still held — the old 图正在执行中 symptom", guard.is_executing is True)
check("told the user the thread is still stuck", "仍卡在工具调用里" in out, out[-200:])
check("gave up after the grace window, not after the 30s read", took < 3.0,
      f"{took:.1f}s")
check("cancel flag cleared even though the thread is stuck",
      cli_events.cancel_requested() is False)
cli_live.CANCEL_GRACE = 10.0

print("\n[D] _ask's spinner path has a watchdog too")


class FakeAgent:
    """DRAgent-shaped enough for _ask: an async execute that never returns.

    `cooperative` blocks in a plain time.sleep instead of awaiting, so neither
    asyncio's own deadline nor the cancel flag can reach it — that is what the
    real DRAgent.execute does, since its body is synchronous.
    """

    def __init__(self, cooperative):
        self.cooperative = cooperative

    async def execute(self, context, params):
        if self.cooperative:
            while not cli_events.cancel_requested():
                await asyncio.sleep(0.02)
            raise cli_events.CliCancelled("用户中断")
        while True:
            time.sleep(0.05)


def ask_args(timeout):
    return argparse.Namespace(mode="qa", model="test-model", file=None,
                              record_events=None, timeout=timeout)


def one_ask(agent, timeout, console, live=False):
    started = time.monotonic()
    try:
        return (ic._ask(agent, ask_args(timeout), "问题", console, "workflow.log",
                        live=live), None, time.monotonic() - started)
    except RuntimeError as exc:
        return None, exc, time.monotonic() - started


console = quiet_console()
# Production gives an unwinding worker CANCEL_GRACE seconds; the cases below
# would otherwise spend it waiting on a thread that is never going to answer.
cli_live.CANCEL_GRACE = 0.5
_, raised, took = one_ask(FakeAgent(False), 0.5, console)
check("TurnTimeout raised instead of hanging forever",
      isinstance(raised, ic.TurnTimeout), repr(raised))
check("still a RuntimeError, so cmd_qa's fail() catches it",
      isinstance(raised, RuntimeError))
check("message names the budget",
      "执行超时" in str(raised) and "0.5s" in str(raised), str(raised))
check("it acted, so it came back fast", took < 3.0, f"{took:.1f}s")
check("and it did wait out the grace window before giving up", took >= 0.9,
      f"{took:.1f}s")
check("abandoned: the run ignored the cancel", raised.abandoned is True, raised)
check("the user is told to stop rather than left spinning",
      "正在停止" in console.file.getvalue(), console.file.getvalue()[-200:])

_, raised, took = one_ask(FakeAgent(True), 0.5, console)
check("a workflow that does yield is caught by the inner asyncio deadline",
      isinstance(raised, RuntimeError) and "执行超时" in str(raised), repr(raised))
check("either way the turn stops at the budget", took < 3.0, f"{took:.1f}s")
check("the flag is clear again for the next turn",
      cli_events.cancel_requested() is False)
cli_live.CANCEL_GRACE = 10.0

print("\n[E] the happy path through the watchdog still answers")


class Answering:
    async def execute(self, context, params):
        return {"final_result": "研究结论"}


(answer, _summary), _, took = one_ask(Answering(), 30, console)
check("answer returned", answer is not None and "研究结论" in answer, answer)
check("no timeout on a fast run", took < 5.0, f"{took:.1f}s")

print("\n[F] the live path is handed the same budget")
# _ask owns the deadline value; run_with_live owns enforcing it. If the wiring
# drops the kwarg the REPL silently becomes unstoppable again, and no other check
# here would notice.
seen = {}
real_live = cli_live.run_with_live


def spy_live(c, worker, archive=None, timeout=None):
    seen["timeout"] = timeout
    return real_live(c, worker, archive, timeout=timeout)


cli_live.run_with_live = spy_live
try:
    _, raised, took = one_ask(FakeAgent(True), 0.7, quiet_console(), live=True)
finally:
    cli_live.run_with_live = real_live
check("args.timeout 传到了渲染层", seen.get("timeout") == 0.7, f"{seen}")
check("live 路径的超时也照原样抛出",
      isinstance(raised, RuntimeError), repr(raised))

Completions.create = REAL_CREATE
cli_events._T0 = None
print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)

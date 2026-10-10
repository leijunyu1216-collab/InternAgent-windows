#!/usr/bin/env python3
"""Claude Code-style streaming display for a DR workflow run.

Completed milestones are printed permanently and scroll up; only the in-flight
work lives in the bottom `rich.Live` region, which redraws in place. Execution
nodes run in a ThreadPoolExecutor, so the live region is a set of concurrent
lanes rather than a single spinner.

The view is driven purely by the event records that cli_events publishes, so
the exact same code path renders a live run and a replayed recording.
"""

import collections
import hashlib
import json
import os
import queue
import re
import threading
import time

from rich.cells import cell_len, chop_cells, set_cell_size
from rich.console import Group
from rich.live import Live
from rich.text import Text

import cli_events

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Tool-call payloads are the bulk of the traffic and can carry a scraped page;
# keep the on-screen preview short and leave the full text to the log file.
TOOL_PREVIEW = 64
SUMMARY_PREVIEW = 96

# How many identical tool calls from one node before the display says something.
# Three is "the model has now retried the exact same query twice", which is the
# signature of a loop that will keep going until the ceiling stops it.
REPEAT_WARN = int(os.environ.get("CLI_REPEAT_WARN", "3"))

# How much of a model's reasoning to print permanently. A full deepseek-reasoner
# chain of thought runs to tens of thousands of characters per call and there are
# many calls per run, so the scrollback needs a ceiling; ThinkArchive keeps all
# of it and /think N re-prints one entry. 0 collapses every entry to its header.
THINK_CHARS = int(os.environ.get("CLI_THINK_CHARS", "400"))

# Seconds to let the worker unwind after Ctrl+C before abandoning it.
CANCEL_GRACE = 10.0

# Silence this long gets an explanation. Deliberately longer than any healthy
# gap between events: a search tool taking 30s is normal, so the note has to
# stay rare enough that seeing it means something.
STALL_SECS = float(os.environ.get("CLI_STALL_SECS", "30"))

# Display lines of streamed model output kept visible under a spinner row.
TAIL_LINES = 2

# Gutter for permanently printed reasoning, and the width it consumes.
THINK_GUTTER = "    │ "
THINK_INDENT = cell_len(THINK_GUTTER)


def _flat(value, limit):
    """One-line, length-capped rendering of an arbitrary payload fragment."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list)):
        import json
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _fmt_secs(secs):
    if secs < 60:
        return f"{secs:.1f}s"
    mins, sec = divmod(int(secs), 60)
    return f"{mins}m{sec:02d}s"


def _args_preview(arguments, limit=TOOL_PREVIEW):
    """Render tool arguments as `k=v, k=v` rather than raw JSON — the raw form
    wastes the narrow live region on braces and quotes."""
    if isinstance(arguments, dict):
        parts = []
        for k, v in arguments.items():
            parts.append(f"{k}={_flat(v, 48)}")
        return _flat(", ".join(parts), limit)
    return _flat(arguments, limit)


def _looks_structured(text):
    """True when a model response is a machine-readable payload rather than
    prose. The planner answers in graph JSON, which get_subtasks already
    renders as a readable task list — echoing the raw braces here would just
    bury it."""
    head = text.lstrip()
    return head[:1] in ("{", "[") or "```json" in head[:200]


_HANGING = "、。，．）》」』】；：？！…％%,"


def _chop_cjk(text, width):
    """chop_cells with Chinese line-breaking rules applied.

    rich only breaks at whitespace, and a Chinese paragraph is effectively one
    enormous word, so its wrapping comes out badly ragged. chop_cells fixes the
    width but happily starts a line with a closing mark, which Chinese
    typography forbids — so pull those back onto the previous line. Chopping
    two cells short leaves the room to do that without exceeding the console.
    """
    lines = chop_cells(text, max(8, width - 2))
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in _HANGING:
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return [line for line in lines if line]


class ThinkArchive:
    """Every model call's full output, numbered, kept out of the scrollback.

    The user steers research direction by reading the chain of thought, but a
    single deepseek-reasoner call can produce 20k+ characters and a run makes
    dozens of them — printing all of it buries the milestones it is meant to
    explain. So the screen gets a capped excerpt tagged with an entry number,
    the whole text lands here, and `/think N` re-prints one entry on demand.

    Entries are also appended to a markdown file: the in-memory list dies with
    the process, and a run that is Ctrl+C'd halfway is exactly the one worth
    re-reading. This is observation-only — nothing here is fed back to the
    model, so it cannot change what the workflow sends or receives.
    """

    def __init__(self, path=None):
        self.path = path
        self.entries = []
        self._fh = None

    def add(self, head, secs, chars, reasoning="", text=""):
        if not reasoning and not text:
            return None
        entry = {"id": len(self.entries) + 1, "head": head, "secs": secs,
                 "chars": chars, "reasoning": reasoning, "text": text}
        self.entries.append(entry)
        if self.path:
            try:
                self._append(entry)
            except OSError:
                self.path = None  # unwritable disk: stay in memory, keep running
        return entry["id"]

    def _append(self, entry):
        if self._fh is None:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            fresh = not os.path.exists(self.path)
            self._fh = open(self.path, "a", encoding="utf-8")
            if fresh:
                self._fh.write("# InternAgent 思考记录\n")
        e = entry
        self._fh.write(
            f"\n## #{e['id']} {e['head']} · {e['secs']:.1f}s · {e['chars']} 字\n")
        for label, body in (("思考", e["reasoning"]), ("输出", e["text"])):
            if body:
                self._fh.write(f"\n### {label}\n\n{body}\n")
        self._fh.flush()

    def get(self, ref):
        for entry in self.entries:
            if entry["id"] == ref:
                return entry
        return None

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


def _print_wrapped(console, body, style="dim italic", first_gutter=False):
    """Print prose with CJK-correct wrapping — rich only breaks at whitespace."""
    width = max(20, (console.width or 100) - THINK_INDENT)
    first = True
    for para in re.sub(r"\n{3,}", "\n\n", body.strip()).split("\n"):
        para = para.strip()
        if not para:
            continue
        for line in _chop_cjk(para, width):
            console.print(Text.assemble(
                (THINK_GUTTER if first or not first_gutter else " " * THINK_INDENT,
                 "dim"),
                (line.rstrip(), style)))
            first = False


def list_thinking(console, entries, path=None):
    if not entries:
        console.print(Text("  本轮没有模型思考记录", style="dim"))
        return
    for e in entries:
        console.print(Text.assemble(
            ("  ", ""), (f"#{e['id']:<4}", "magenta"),
            (set_cell_size(e["head"], max(20, (console.width or 100) - 34)), ""),
            (f"  {e['secs']:.1f}s · {e['chars']} 字", "dim")))
    console.print(Text.assemble(
        ("  ", "dim"),
        (f"/think 编号 展开全部 · /think 编号 字数 只看开头", "dim"),
        (f" · 文件 {path}" if path else "", "dim")))


def show_thinking(console, entry, limit=0):
    """Expand one archived call in full (or its first `limit` characters)."""
    console.print(Text.assemble(
        ("◆ ", "magenta"), (entry["head"], "bold white"),
        (f"  {entry['secs']:.1f}s · {entry['chars']} 字", "dim")))
    shown = 0
    for label, body in (("思考", entry["reasoning"]), ("输出", entry["text"])):
        if not body:
            continue
        console.print(Text(f"    ── {label} {len(body)} 字 ──", style="dim"))
        if limit and shown + len(body) > limit:
            body = body[:max(0, limit - shown)]
            _print_wrapped(console, body)
            console.print(Text(f"    …（已按 {limit} 字截断，去掉数字看全部）",
                               style="dim"))
            break
        _print_wrapped(console, body)
        shown += len(body)


def print_references(console, references, limit=0):
    """List the citations behind the last answer, one per line.

    The answer text carries the same list under `## References`, but that sits
    at the bottom of a long document; this is for scanning sources and grabbing
    a URL without re-asking, which would cost another turn.
    """
    if not references:
        console.print(Text("  上一轮没有解析出参考文献（答案里若自带列表请以正文为准）",
                           style="dim"))
        return
    shown = references[:limit] if limit and limit > 0 else references
    width = max(24, (console.width or 100) - 6)
    for ref in shown:
        tag = pad_cell(set_cell_size(f"[{ref.get('tag') or '?'}]", 5), 5)
        for i, line in enumerate(_chop_cjk(ref.get("title") or "(无标题)", width)):
            console.print(Text.assemble(("  ", ""),
                                         (tag if i == 0 else " " * 5, "cyan"),
                                         (line, "")))
        for line in _chop_cjk(ref.get("url") or "", width):
            console.print(Text.assemble(("  ", ""), (" " * 5, ""), (line, "dim")))
    if len(shown) < len(references):
        console.print(Text(f"  … 另有 {len(references) - len(shown)} 条"
                           "（/refs 0 看全部）", style="dim"))


def _tail_excerpt(body, cap):
    """The last `cap` characters of `body`, cut at a paragraph boundary where
    one is available. Returns (excerpt, dropped_chars).

    A chain of thought opens by restating the task it was given — which the
    milestone line above already says — and ends with the decision, which is the
    part worth steering on. Cutting on a raw character count starts mid-sentence
    and often keeps nothing but that boilerplate, so whole trailing paragraphs
    are preferred and only an over-long single paragraph is cut by characters.
    """
    if len(body) <= cap:
        return body, 0
    paras = [p for p in (line.strip() for line in body.split("\n")) if p]
    kept, total = [], 0
    for para in reversed(paras):
        if kept and total + len(para) > cap:
            break
        kept.append(para)
        total += len(para)
    kept.reverse()
    excerpt = "\n".join(kept)
    if len(excerpt) > cap:  # one paragraph longer than the whole budget
        excerpt = "…" + excerpt[-cap:].lstrip()
        shown = cap
    else:
        shown = total
        if len(kept) < len(paras):
            excerpt = "…\n" + excerpt
    return excerpt, len(body) - shown


def merge_usage(dst, summary):
    """Fold one turn's summary totals into a running session dict."""
    if dst is None:
        dst = {"secs": 0.0, "model_calls": 0, "tool_calls": 0, "usage": {},
               "usage_seen": False, "usage_by_stage": {}, "turns": 0}
    dst["secs"] += summary.get("secs") or 0.0
    dst["model_calls"] += summary.get("model_calls") or 0
    dst["tool_calls"] += summary.get("tool_calls") or 0
    dst["turns"] += 1
    if summary.get("usage_seen"):
        dst["usage_seen"] = True
    for key, val in (summary.get("usage") or {}).items():
        dst["usage"][key] = dst["usage"].get(key, 0) + val
    for stage, bucket in (summary.get("usage_by_stage") or {}).items():
        acc = dst["usage_by_stage"].setdefault(
            stage, {"prompt": 0, "completion": 0, "total": 0, "calls": 0})
        for key, val in bucket.items():
            acc[key] += val
    return dst


def print_summary(console, summary, think_count=0):
    """One dim line of turn totals to sit under the answer."""
    if summary is None:
        return
    usage = summary.get("usage") or {}
    parts = [_fmt_secs(summary.get("secs") or 0.0)]
    if summary.get("layers"):
        parts.append(f"{summary['layers']} 层")
    parts.append(f"{summary.get('model_calls', 0)} 次模型调用")
    if summary.get("tool_calls"):
        repeated = summary.get("repeated_tool_calls") or 0
        parts.append(f"{summary['tool_calls']} 次工具调用"
                     + (f"（{repeated} 次重复）" if repeated else ""))
    if summary.get("usage_seen") and usage.get("total"):
        parts.append(f"token {usage.get('prompt', 0)}入/{usage.get('completion', 0)}出")
    if summary.get("refs"):
        parts.append(f"{summary['refs']} 条引用 · /refs")
    if think_count:
        parts.append(f"{think_count} 条思考")
    console.print(Text("  " + " · ".join(parts), style="dim"))
    if (summary.get("repeated_tool_calls") or 0) >= 3:
        # The live warning scrolls away; this is the one that survives into the
        # transcript, so a turn that burned its budget on retries reads as such.
        console.print(Text(
            "  ⚠ 同一个节点用相同参数反复调同一个工具，多半是在原地打转；"
            "换个更具体的问题、或把 --timeout 调小止损", style="yellow"))


def pad_cell(text, width, right=False):
    """Align to *display columns*. ljust/rjust pad by characters, and a CJK
    glyph is two columns, so `ljust(8)` on 答案合成 leaves it 4 cells short and
    shifts every column after it."""
    pad = " " * max(0, width - cell_len(text))
    return (pad + text) if right else (text + pad)


def print_usage(console, by_stage, totals, usage_seen):
    """Per-stage token breakdown for /usage."""
    if not by_stage:
        console.print(Text(
            "  该端点未上报 token 用量（流式响应需要 stream_options.include_usage，"
            "部分供应商不支持）" if not usage_seen else "  还没有用量统计",
            style="dim"))
        return
    # (column width, header) — every row uses the same widths, so the digits
    # land in the same columns whatever the stage names look like.
    cols = ((4, "调用"), (6, "输入"), (6, "输出"), (6, "合计"))
    label_w = max([cell_len("阶段"), cell_len("合计")]
                  + [cell_len(s) for s in by_stage])

    def row(label, values, head_style, cell_style, total_style):
        parts = [("  " + pad_cell(label, label_w), head_style)]
        for (width, _), val in zip(cols, values):
            parts.append(("  " + pad_cell(str(val), width, right=True),
                          cell_style))
        parts[-1] = (parts[-1][0], total_style)
        return Text.assemble(*parts)

    console.print(row("阶段", [h for _, h in cols], "bold", "dim", "dim"))
    for stage, b in sorted(by_stage.items(),
                           key=lambda kv: -kv[1].get("total", 0)):
        console.print(row(stage, [b.get("calls", 0), b.get("prompt", 0),
                                  b.get("completion", 0), b.get("total", 0)],
                      "", "dim", "white"))
    u = totals or {}
    console.print(row("合计", [sum(b.get("calls", 0) for b in by_stage.values()),
                              u.get("prompt", 0), u.get("completion", 0),
                              u.get("total", 0)], "bold", "dim", "bold"))


class _Lane:
    __slots__ = ("key", "label", "tool", "args", "t_start", "t_end",
                 "done", "ok", "tools", "summary", "stage", "call")

    def __init__(self, key, label):
        self.key = key
        self.label = label
        self.tool = None
        self.args = ""
        self.t_start = time.monotonic()
        self.t_end = None
        self.done = False
        self.ok = None
        self.tools = 0
        self.summary = ""
        self.stage = ""
        self.call = None


class LiveView:
    """Accumulates event records into a renderable snapshot."""

    def __init__(self, console, archive=None):
        self.console = console
        self.archive = archive
        self.phase = "启动"
        self.layer = 0
        self.lanes = {}
        self.order = []
        self.t_start = time.monotonic()
        # When the display last heard anything; the stall hint measures silence
        # from here rather than from the start, so a long healthy run is quiet.
        self.t_event = self.t_start
        self.tool_calls = 0
        self.model_calls = 0
        # Token totals only ever include what the endpoint actually reported,
        # so a provider that omits usage leaves them at zero rather than at a
        # guessed number. `usage_seen` distinguishes "reported 0" from "silent".
        self.usage = {"prompt": 0, "completion": 0, "total": 0}
        self.usage_by_stage = {}
        self.usage_seen = False
        self.refs = 0
        self.references = []
        self.error = None
        # (node, tool, hash(args)) -> times called, for the repeat warning
        self.repeats = {}
        self.repeat_shown = 0
        self._pending_nodes = []
        # call id -> in-flight model call, i.e. the thinking happening right now
        self.calls = {}
        self._call_order = []

    # -- helpers ---------------------------------------------------------

    def _lane(self, record):
        data = record.get("data") or {}
        key = data.get("node_id")
        if key is None:
            key = data.get("subtask_id")
        if key is None:
            key = record.get("agent") or "?"
        key = str(key)
        lane = self.lanes.get(key)
        if lane is None:
            lane = _Lane(key, key)
            self.lanes[key] = lane
            self.order.append(key)
        return lane

    def _say(self, *renderables):
        self.console.print(*renderables)

    def _milestone(self, symbol, text, style="bold"):
        self._say(Text.assemble((f"{symbol} ", style), (text, style)))

    def _say_thinking(self, text, cap=THINK_CHARS, ref=None):
        """Print a capped excerpt of a model's reasoning permanently, in a
        gutter so it reads as quoted thought rather than as CLI chrome.

        The *tail* is kept, not the head — see _tail_excerpt. cap=0 collapses
        the entry to its header line entirely; the full text lives in the
        ThinkArchive and comes back with `/think N`.
        """
        body = re.sub(r"\n{3,}", "\n\n", text.strip())
        note = None
        if not cap:
            note = f"{len(body)} 字已折叠"
            body = ""
        else:
            body, dropped = _tail_excerpt(body, cap)
            if dropped:
                note = f"前 {dropped} 字已折叠"
        if ref:
            note = (note + " · " if note else "") + f"/think {ref} 展开"
        if body:
            _print_wrapped(self.console, body, first_gutter=True)
        if note:
            self.console.print(Text.assemble(
                (THINK_GUTTER if not body else " " * THINK_INDENT, "dim"),
                (note, "dim")))

    def _finish_lane(self, lane, ok, summary):
        lane.done = True
        lane.ok = ok
        lane.t_end = time.monotonic()
        lane.summary = summary
        mark, mstyle = ("✓", "green") if ok else ("✗", "red")
        detail = f" · {summary}" if summary else ""
        tools = f" · {lane.tools} 次工具调用" if lane.tools else ""
        self._say(Text.assemble(
            ("  ", ""), (mark, mstyle), (" ", ""),
            (lane.label, "white"),
            (f"  {_fmt_secs(lane.t_end - lane.t_start)}{tools}", "dim"),
            (detail, "dim"),
        ))

    # -- event handling --------------------------------------------------

    def feed(self, record):
        self.t_event = time.monotonic()
        event = record.get("event")
        data = record.get("data") or {}
        handler = getattr(self, "_on_" + event, None)
        if handler is not None:
            handler(record, data)

    def _on_workflow_start(self, record, data):
        self.t_start = time.monotonic()
        question = _flat(data.get("question"), 120)
        model = data.get("model") or "?"
        mode = data.get("mode") or "?"
        self._say()
        self.console.rule(Text.assemble(
            ("InternAgent ", "bold cyan"), (f"· {mode} · {model}", "dim")))
        if question:
            self._say(Text.assemble(("  ", ""), (question, "white")))
        self._say()

    # -- model calls: where the thinking actually is ---------------------

    def _stage_head(self, data):
        return " ".join(
            x for x in (data.get("stage"), data.get("detail")) if x) or "模型调用"

    def _perm_head(self, data):
        """Head for a permanent line. In the live region a node's model call
        renders inside that node's own lane, but the scrollback has no lane
        context — with three nodes in parallel, three bare "选择下一步" lines
        would be indistinguishable, so the node is named here. The stage label
        is dropped in that case because "节点 N" already says it."""
        node = data.get("node_id")
        if node is None:
            return self._stage_head(data)
        sub = data.get("subtask_id")
        where = f"节点 {node}" + (f" · 子任务 {sub}" if sub is not None else "")
        detail = data.get("detail")
        return f"{where} · {detail}" if detail else where

    def _drop_call(self, call):
        info = self.calls.pop(call, None)
        if call in self._call_order:
            self._call_order.remove(call)
        key = (info or {}).get("lane")
        if key is not None:
            lane = self.lanes.get(key)
            if lane is not None and lane.call == call:
                lane.call = None

    def _on_model_call_start(self, record, data):
        call = data.get("call")
        head = self._stage_head(data)
        node = data.get("node_id")
        lane = self.lanes.get(str(node)) if node is not None else None
        # A node-scoped call is rendered by its own lane; only calls that
        # belong to no lane (planner, coordinator, synthesizer) get a row here.
        self.calls[call] = {
            "head": head,
            # The lane label already reads "2 · 子任务 2", so repeating the
            # stage there would be "节点执行" twice over.
            "short": data.get("detail") or head,
            "t_start": time.monotonic(), "tail": "", "chars": 0,
            "lane": lane.key if lane is not None else None}
        self._call_order.append(call)
        self.model_calls += 1
        self.phase = data.get("stage") or self.phase
        if lane is not None:
            lane.stage = self.calls[call]["short"]
            lane.call = call

    def _on_model_call_chunk(self, record, data):
        call = self.calls.get(data.get("call"))
        if call is None:
            return
        call["chars"] += data.get("chars") or 0
        # Only the tail is ever rendered, so keep a bounded window rather than
        # accumulating a whole answer per lane.
        call["tail"] = (call["tail"] + (data.get("text") or ""))[-800:]

    def _add_usage(self, data):
        usage = data.get("usage") or {}
        if not isinstance(usage, dict) or not usage:
            return
        self.usage_seen = True
        stage = data.get("stage") or "模型调用"
        bucket = self.usage_by_stage.setdefault(stage, {
            "prompt": 0, "completion": 0, "total": 0, "calls": 0})
        for key in ("prompt", "completion", "total"):
            val = usage.get(key)
            if isinstance(val, int) and val > 0:
                self.usage[key] += val
                bucket[key] += val
        bucket["calls"] += 1

    def _on_model_call_end(self, record, data):
        self._drop_call(data.get("call"))
        self._add_usage(data)
        chars = data.get("chars") or 0
        reasoning = (data.get("reasoning") or "").strip()
        text = (data.get("text") or "").strip()
        head = self._perm_head(data)
        ref = None
        if self.archive is not None:
            ref = self.archive.add(head, data.get("secs") or 0, chars,
                                   reasoning, text)
        self._say(Text.assemble(
            ("  ", ""), ("◆", "magenta"), (" ", ""),
            (head, "white"),
            (f"  {data.get('secs') or 0:.1f}s · {chars} 字", "dim"),
            (f" · #{ref}" if ref else "", "dim magenta")))
        if reasoning:
            self._say_thinking(reasoning, ref=ref)
            return
        # The synthesizer's prose is printed in full as the answer a moment
        # later, and the planner answers in graph JSON that get_subtasks
        # already turns into a readable task list.
        if text and data.get("stage") != "答案合成" and not _looks_structured(text):
            self._say_thinking(text, ref=ref)

    def _on_model_call_error(self, record, data):
        self._drop_call(data.get("call"))
        self._say(Text.assemble(
            ("  ", ""), ("✗", "red"), (" ", ""),
            (self._perm_head(data), "red"),
            (f"  {data.get('error')}: {_flat(data.get('message'), 120)}", "dim red")))

    def _on_model_stream_disabled(self, record, data):
        self._say(Text.assemble(
            ("  ", ""), ("⚠", "yellow"),
            (" 该端点不支持流式返回，已改为整段接收", "dim yellow"),
            (f"（{_flat(data.get('error'), 80)}）", "dim")))

    def _on_get_subtasks(self, record, data):
        self.phase = "任务分解"
        subtasks = data.get("subtasks") or []
        self._milestone("●", f"规划任务分解（{len(subtasks)} 个子任务）", "bold cyan")
        for i, st in enumerate(subtasks[:12], 1):
            if isinstance(st, dict):
                label = _flat(st.get("task") or st.get("description") or st, 100)
            else:
                label = _flat(st, 100)
            self._say(Text.assemble(("  ", ""), (f"{i}. ", "dim"), (label, "")))
        if len(subtasks) > 12:
            self._say(Text(f"  … 另外 {len(subtasks) - 12} 个", style="dim"))

    def _on_get_current_execution_nodes(self, record, data):
        node_ids = data.get("node_ids") or []
        self.layer += 1
        self.phase = f"第 {self.layer} 层执行"
        self._pending_nodes = [str(n) for n in node_ids]
        for lane in self.lanes.values():
            if not lane.done:
                lane.done = True  # previous layer's stragglers
        self.lanes.clear()
        self.order.clear()
        for nid in self._pending_nodes:
            lane = _Lane(nid, nid)
            self.lanes[nid] = lane
            self.order.append(nid)
        self._milestone(
            "●", f"第 {self.layer} 层执行（{len(node_ids)} 个节点并行）", "bold cyan")

    def _on_get_subtask_process(self, record, data):
        lane = self._lane(record)
        proc = data.get("process") or {}
        tool = proc.get("tool_name")
        if not tool:
            return
        lane.tool = str(tool)
        lane.args = _args_preview(proc.get("arguments"))
        lane.tools += 1
        self.tool_calls += 1
        self._watch_repeat(lane, str(tool), proc.get("arguments"))
        if lane.done:  # node revived after a stray late event
            lane.done = False
            lane.t_end = None
        sub = data.get("subtask_id")
        if sub is not None:
            lane.label = f"{lane.key} · 子任务 {sub}"

    def _watch_repeat(self, lane, tool, arguments):
        """Warn when one node calls the same tool with the same arguments.

        This is the shape a runaway loop takes: the model re-issues a search that
        returned nothing useful, gets nothing useful again, and repeats until the
        per-node or per-layer ceiling stops it — which, before the layer cap was
        fixed, it did not. Per node rather than global, because parallel subtasks
        searching the same thing is normal. Says it once per threshold crossing so
        a legitimate retry pair stays quiet.
        """
        try:
            blob = json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                              default=str)
        except (TypeError, ValueError):
            blob = str(arguments)
        key = (lane.key, tool, hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12])
        n = self.repeats.get(key, 0) + 1
        self.repeats[key] = n
        if n < REPEAT_WARN or n % REPEAT_WARN:
            return
        self.repeat_shown += 1
        self._say(Text.assemble(
            ("  ⚠ ", "yellow"),
            (f"{lane.label} 已用相同参数第 {n} 次调用 {tool}", "yellow"),
            (f" · {lane.args}" if lane.args else "", "dim"),
            (" · 疑似原地打转，可 Ctrl+C 停止", "dim")))

    def _on_get_subtask_result(self, record, data):
        lane = self._lane(record)
        result = data.get("result") or {}
        sub = data.get("subtask_id")
        if sub is not None:
            lane.label = f"{lane.key} · 子任务 {sub}"
        # Subtasks are sequential within a node; keep the lane alive until the
        # node itself reports. Subordinate marker so this does not read as a
        # second completion line for the same node.
        ok = bool(result.get("success"))
        self._say(Text.assemble(
            ("    ", ""), ("└", "dim"), (" ", ""),
            (f"{lane.label}", "dim"),
            ("  ✗ " if not ok else "  ", "red" if not ok else "dim"),
            (_flat(result.get("summary"), SUMMARY_PREVIEW), "dim")))

    def _on_get_node_result(self, record, data):
        lane = self._lane(record)
        result = data.get("result") or {}
        self._finish_lane(lane, bool(result.get("success")),
                          _flat(result.get("summary"), SUMMARY_PREVIEW))

    def _on_get_coordinator_modification(self, record, data):
        self.phase = "协调器调整"
        self._milestone("●", "协调器调整执行图", "bold magenta")

    def _on_get_graph_after_coordinator(self, record, data):
        self.phase = "协调器调整"

    def _on_start_answer_node(self, record, data):
        self.phase = "合成答案"
        pending = [self.lanes[k] for k in self.order
                   if k in self.lanes and not self.lanes[k].done]
        if len(pending) > 3:
            # Answer synthesis supersedes per-node reporting; dumping dozens of
            # trailing completion lines here buries the milestone itself.
            now = time.monotonic()
            for lane in pending:
                lane.done = True
                lane.t_end = now
            self._say(Text(f"  ✓ {len(pending)} 个节点已完成（详情见日志）",
                           style="dim"))
        else:
            for lane in pending:
                self._finish_lane(lane, True, "")
        self._milestone("●", "合成最终答案", "bold cyan")

    def _on_get_cited_references(self, record, data):
        # synthesizer collects these into a tag-keyed dict and its signature
        # says list, so accept either shape. The last event wins: it is the set
        # gathered from the whole graph, not one node's slice.
        raw = data.get("references") or []
        items = raw.items() if isinstance(raw, dict) else enumerate(raw, 1)
        entries = []
        for tag, ref in items:
            if not isinstance(ref, dict):
                ref = {"title": _flat(ref, 160)}
            entries.append({
                "tag": str(ref.get("tag") or tag),
                "title": _flat(ref.get("title") or "", 160),
                "url": str(ref.get("url") or "").strip(),
            })
        self.references = entries
        self.refs = len(entries)
        if not entries:
            self._say(Text("  参考文献：答案里没有解析出可列出的条目", style="dim"))
            return
        self._say(Text.assemble(
            ("  ", ""), (f"引用 {len(entries)} 条参考文献", "dim"),
            (" · /refs 查看", "dim")))

    def _on_workflow_end(self, record, data):
        self.phase = "完成"

    def _on_workflow_error(self, record, data):
        self.error = f"{data.get('error')}: {data.get('detail')}"

    def _on_workflow_timeout(self, record, data):
        self.error = f"执行超时（{data.get('timeout')}s）"

    def _on_workflow_stub_failure(self, record, data):
        # dr_agent.py now puts the exception text next to the marker, so the
        # live view can name the reason instead of just the symptom.
        detail = (data.get("detail") or "").strip() or data.get("marker")
        self.error = f"工作流返回兜底文本（{detail}）"

    # -- rendering -------------------------------------------------------

    def _spinner(self):
        return SPINNER[int(time.monotonic() * 10) % len(SPINNER)]

    def _tail_lines(self, tail, avail):
        """Last TAIL_LINES display-lines of a stream, newest at the bottom."""
        tail = " ".join(tail.split())
        return chop_cells(tail, avail)[-TAIL_LINES:] if tail else []

    def _wasted_calls(self):
        """Tool calls that only repeated an earlier identical call.

        One entry per (node, tool, args) is work; everything past that is the
        model asking for something it already asked for.
        """
        return sum(n - 1 for n in self.repeats.values() if n > 1)

    def summary(self):
        """Turn totals for the caller's end-of-turn summary block."""
        return {
            "secs": time.monotonic() - self.t_start,
            "layers": self.layer,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "repeated_tool_calls": self._wasted_calls(),
            "refs": self.refs,
            "references": list(self.references),
            "usage": dict(self.usage),
            "usage_seen": self.usage_seen,
            "usage_by_stage": dict(self.usage_by_stage),
            "error": self.error,
        }

    def _footer(self):
        total = _fmt_secs(time.monotonic() - self.t_start)
        parts = [f"已用 {total}", f"{self.model_calls} 次模型调用",
                 f"{self.tool_calls} 次工具调用"]
        wasted = self._wasted_calls()
        if wasted:
            parts.append(f"其中 {wasted} 次是重复调用")
        if self.usage_seen and self.usage["total"]:
            parts.append(f"{self.usage['total']} tokens")
        parts.append("Ctrl+C 中断")
        return " · ".join(parts)

    def render(self):
        active = [self.lanes[k] for k in self.order
                  if k in self.lanes and not self.lanes[k].done]
        total = _fmt_secs(time.monotonic() - self.t_start)
        # Clamp by display cells, not characters: CJK occupies two columns, so
        # len() would still overflow. A wrapped Live row needs extra lines
        # cleared on each redraw, which leaves residue on some terminals.
        avail = max(20, (self.console.width or 100) - 14)
        lines = []
        for lane in active[:6]:
            secs = _fmt_secs(time.monotonic() - lane.t_start)
            call = self.calls.get(lane.call) if lane.call is not None else None
            wrote = ""
            if call is not None:
                body = f"{lane.label}  {call['short']}"
                if call["chars"]:
                    wrote = f" · {call['chars']} 字"
            elif lane.tool:
                body = f"{lane.label}  {lane.tool}({lane.args})"
            elif lane.stage:
                body = f"{lane.label}  {lane.stage}"
            else:
                body = f"{lane.label}  等待模型响应"
            lines.append(Text.assemble(
                (f"{self._spinner()} ", "cyan"),
                (set_cell_size(body, max(12, avail - cell_len(wrote) - 8)),
                 "white"),
                (f" {secs}{wrote}", "dim"),
            ))
            if call is not None:
                for tail in self._tail_lines(call["tail"], avail):
                    lines.append(Text.assemble(
                        ("  │ ", "dim"), (tail, "dim italic")))
        if len(active) > 6:
            lines.append(Text(f"… 另外 {len(active) - 6} 个节点", style="dim"))
        # Calls that belong to no lane — planner, coordinator, synthesizer —
        # come last so the streaming tail sits just above the status line and
        # reads as "the model is writing this right now".
        solo = [self.calls[k] for k in self._call_order
                if k in self.calls
                and self.calls[k].get("lane") not in self.lanes]
        for call in solo[:3]:
            secs = _fmt_secs(time.monotonic() - call["t_start"])
            wrote = f" · {call['chars']} 字" if call["chars"] else ""
            lines.append(Text.assemble(
                (f"{self._spinner()} ", "magenta"),
                (set_cell_size(call["head"],
                               max(12, avail - cell_len(wrote) - 8)), "white"),
                (f" {secs}{wrote}", "dim"),
            ))
            for tail in self._tail_lines(call["tail"], avail):
                lines.append(Text.assemble(
                    ("  │ ", "dim"), (tail, "dim italic")))
        if len(solo) > 3:
            lines.append(Text(f"… 另外 {len(solo) - 3} 个模型调用", style="dim"))
        if not lines:
            lines.append(Text.assemble(
                (f"{self._spinner()} ", "cyan"),
                (self.phase, "white"),
                (f"  {total}", "dim")))
        hint = self._stall_hint()
        if hint:
            # Keep it to one row: a wrapped Live line costs extra cleared lines
            # and leaves residue, and the actionable half of the sentence
            # survives truncation better than the tail does.
            lines.append(Text(set_cell_size(hint, avail), style="yellow"))
        lines.append(Text(self._footer(), style="dim"))
        return Group(*lines)

    def _stall_hint(self):
        """What to do while nothing is happening.

        A run that prints its header and then goes quiet for a minute reads
        exactly like a hung CLI, and the first real user did Ctrl+C at that
        point. Two very different stalls need different advice, so they get
        different wording: no call in flight means the request probably never
        got answered (wrong base_url/model, or a slow first token), while a call
        that is open but silent means the endpoint is thinking.
        """
        idle = time.monotonic() - self.t_event
        if idle < STALL_SECS:
            return None
        secs = int(idle)
        if self.calls:
            return f"  ⚠ {secs}s 无新字符 · 连接仍在，端点可能在排队 · Ctrl+C"
        return (f"  ⚠ {secs}s 无事件 · 可能卡在第一次模型调用，"
                f"核对 base_url 与模型名 · Ctrl+C")


LiveRun = collections.namedtuple(
    "LiveRun", "result error interrupted abandoned summary timed_out",
    defaults=(False,))
# result: whatever worker() returned. error: the exception it raised.
# interrupted: Ctrl+C was pressed. abandoned: the worker thread is still stuck
# in a socket read, so the workflow still holds its is_executing guard and must
# not be reused. summary: LiveRun.summary dict for the end-of-turn block.
# timed_out: the deadline passed `timeout` seconds without a result.


def run_with_live(console, worker, archive=None, timeout=None):
    """Run `worker()` on a daemon thread while rendering its events here.

    Ctrl+C asks cli_events to cancel, which makes the next model call raise
    CliCancelled — a BaseException, so it passes straight through the workflow's
    `except Exception` retry loops while still running the `finally:` blocks that
    release state such as GlobalExecutionAgent.is_executing. Python cannot kill
    a thread blocked in a socket read, so if nothing is in flight within
    CANCEL_GRACE we abandon it, and the caller must not reuse that workflow.

    `timeout` uses the same mechanism for a deadline instead of a keypress. It
    has to be policed here, on the rendering thread: the workflow body is
    synchronous, so an asyncio.wait_for around it cannot fire until the whole
    research run returns — measured 1s deadline vs a 3s sleep, and the sleep ran
    to completion. This loop is the only place that both keeps ticking and can
    set the cancel flag.
    """
    cli_events.clear_cancel()
    sink = cli_events.QueueSink().attach()
    view = LiveView(console, archive)
    box = {}
    stop_at = None if timeout is None else time.monotonic() + timeout

    def _target():
        try:
            box["result"] = worker()
        except BaseException as exc:  # surfaced to the caller, not swallowed
            box["error"] = exc
        finally:
            box["done"] = True

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()

    def _drain():
        """Move queued events into the view; returns how many were taken."""
        drained = 0
        while True:
            try:
                record = sink.q.get_nowait()
            except queue.Empty:
                return drained
            view.feed(record)
            drained += 1

    interrupted = False
    timed_out = False
    try:
        try:
            # Pass a renderable, not a callable, and drive refreshes ourselves:
            # rich's Live hands the callable to LiveRender unevaluated in some
            # versions, and we need a steady tick anyway for the elapsed timers.
            with Live(view.render(), console=console, auto_refresh=False,
                      transient=True) as live:
                while True:
                    drained = _drain()
                    if box.get("done") and drained == 0 and sink.q.empty():
                        break
                    if stop_at is not None and time.monotonic() >= stop_at:
                        timed_out = True
                        break
                    if drained == 0:
                        time.sleep(0.04)
                    live.update(view.render(), refresh=True)
        except KeyboardInterrupt:
            interrupted = True

        if interrupted or timed_out:
            cli_events.request_cancel()
            console.print(Text(
                f"已达 {timeout:g}s 上限，正在停止（等待当前模型调用返回）…"
                if timed_out else "正在停止（等待当前模型调用返回）…",
                style="yellow"))
            grace = time.monotonic() + CANCEL_GRACE
            # Keep collecting while the worker unwinds: the reasoning of the
            # call we just cancelled is exactly what /think should show, and it
            # arrives as events during this window.
            while not box.get("done") and time.monotonic() < grace:
                time.sleep(0.05)
                _drain()
            _drain()
    finally:
        sink.detach()
        cli_events.clear_cancel()

    abandoned = (interrupted or timed_out) and thread.is_alive()
    if abandoned:
        console.print(Text(
            "已中断，但后台线程仍卡在工具调用里（daemon，随进程退出）。"
            "下一轮提问会重建工作流。", style="yellow"))
    elif interrupted:
        console.print(Text("已中断。", style="yellow"))
    if view.error:
        console.print(Text(f"✗ {view.error}", style="bold red"))
    return LiveRun(box.get("result"), box.get("error"), interrupted, abandoned,
                   view.summary(), timed_out)


def replay_file(console, path, speed=1.0, archive=None):
    """Render a recorded JSONL through the live view — the no-API-cost way to
    tune the display."""
    view = LiveView(console, archive)
    with Live(view.render(), console=console, auto_refresh=False,
              transient=True) as live:
        def sink(record):
            view.feed(record)
            live.update(view.render(), refresh=True)
        n = cli_events.replay(path, sink, speed=speed)
    console.print(Text(f"回放完成：{n} 条事件", style="dim"))
    return n

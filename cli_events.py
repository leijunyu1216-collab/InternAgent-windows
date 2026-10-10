#!/usr/bin/env python3
"""Event plumbing for the InternAgent CLI: capture, record, and replay.

The DR workflow already emits nine semantically rich events (get_subtasks,
get_current_execution_nodes, get_subtask_process, get_node_result, ...) via
BaseAgent.send_redis_event. They never fire from the CLI because that method
early-returns unless both a Redis client and a task_id are present, and
dr_agent.py calls workflow.execute() without a task_id.

So this module patches that one method from the CLI side and fans the events
out to any number of subscribers — a JSONL file recorder and/or the live
terminal renderer. Nothing under version control is edited, which keeps a
`git pull` from reverting the work.

Those nine events describe the workflow's *structure*; none of them carries
any model reasoning, and the first one (get_subtasks) only fires after the
whole planning loop has finished — so a real run looks dead for its first
minute. Every DR model adapter (deepseek, openai_model, qwen, gemini, vllm,
intern_s1) funnels through the synchronous openai client's
chat.completions.create, so that single call site is patched too. It yields
model_call_start / model_call_chunk / model_call_end, which is where the
agent's actual thinking lives, and it is also the only place a Ctrl+C can be
turned into a cancellation the workflow will honour (see CliCancelled).

Set CLI_STREAM=0 to keep the model hook observation-only (no chunk events, no
conversion of non-streaming calls).

Record shape (one per line in the JSONL sink):
    {seq, t, wall, tid, event, agent, data}
`t` is seconds since the bus started; `tid` matters because execution nodes
run in a ThreadPoolExecutor, so events arrive concurrently and out of order.
"""

import itertools
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import types
from datetime import datetime

# get_subtask_process carries the full tool `result`, which for a scraped web
# page can be megabytes. The upstream comment claims it is length-limited; it
# is not. Truncation happens here, at the boundary, and the original size is
# preserved so the UI can be designed against real payload volumes.
MAX_STR = int(os.environ.get("CLI_EVENT_MAX_STR", "2000"))
MAX_ITEMS = int(os.environ.get("CLI_EVENT_MAX_ITEMS", "50"))
MAX_DEPTH = 12

# A model's chain of thought is the one payload the user actually reads (to
# steer the research direction), so it must not be cut to MAX_STR: /think and
# logs/think_*.md would silently show the first 2000 characters of a 23000
# character reasoning trace. Only these two *top-level* fields get the larger
# cap, so a scraped page in a tool result stays bulk.
MAX_THINK_STR = int(os.environ.get("CLI_EVENT_MAX_THINK_STR", "100000"))
THINK_KEYS = ("text", "reasoning")

# Key material must not survive into any sink: the JSONL recording, the live
# display, logs/think_*.md and logs/cli_*.log are all written to disk, and a
# provider that echoes the submitted key back in a 401 body would otherwise
# persist it. Redaction runs before truncation so a key that straddles the cut
# cannot leave half of itself behind.
_SECRET_NAMES = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?key|secret|authorization)"
# `Bearer` / `Basic` / `Token` are schemes, not secrets: "Authorization: Bearer
# sk-…" must lose the key and keep the scheme, which is the part that tells the
# user *where* the bad credential was sent from.
_SECRET_ASSIGN = re.compile(
    r"(?i)(" + _SECRET_NAMES + r")([\"']?)(\s*[:=]\s*)([\"']?)"
    r"(?!(?:Bearer|Basic|Token)\b)([^\s\"',;]{6,})")
_SECRET_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}\b")
_SECRET_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{6,}\b")


def redact(text):
    """Mask key material found in *text*. Idempotent, and leaves prose alone."""
    if not isinstance(text, str) or not text:
        return text
    out = _SECRET_ASSIGN.sub(
        lambda m: m.group(1) + m.group(2) + m.group(3) + m.group(4) + "***", text)
    # Bearer before the sk- pass: masking the token first leaves "Bearer sk-***",
    # and the asterisks no longer match the header's character class, so the
    # scheme would survive as if it were still part of a live credential.
    out = _SECRET_BEARER.sub("Bearer ***", out)
    out = _SECRET_SK.sub("sk-***", out)
    return out


def _fit(text, cap):
    return text if len(text) <= cap else text[:cap] + f"…[+{len(text) - cap} chars]"


def _sanitize(obj, depth=0, cap=None):
    if depth > MAX_DEPTH:
        return "<max-depth>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _fit(redact(obj), MAX_STR if cap is None else cap)
    if isinstance(obj, dict):
        return {str(k): _sanitize(v, depth + 1,
                                  MAX_THINK_STR if (depth == 0 and k in THINK_KEYS)
                                  else None)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        items = list(obj)
        out = [_sanitize(v, depth + 1) for v in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            out.append(f"…[+{len(items) - MAX_ITEMS} items]")
        return out
    if isinstance(obj, bytes):
        return f"<{len(obj)} bytes>"
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return _sanitize(repr(obj), depth + 1)


def secret_matches(text):
    """Every key-shaped span in *text* — the audit half of redact().

    The masked forms do not match (`***` is too short for any of the three
    value patterns), so `internagent doctor` can point at log files written
    before redaction existed without flagging today's output.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    for pattern in (_SECRET_ASSIGN, _SECRET_SK, _SECRET_BEARER):
        found.extend(m.group(0) for m in pattern.finditer(text))
    return found


class RedactingLogFilter(logging.Filter):
    """Keep key material out of log output.

    Attach this to a *handler*, not to a logger: a logger's own filters are
    skipped for records propagated up from a child logger, and the libraries
    that ever see an Authorization header (openai, httpx, camel) sit outside
    our logger tree.
    """

    def filter(self, record):
        try:
            formatted = record.getMessage()
        except Exception:
            return True  # a record we cannot read is not worth losing
        if isinstance(record.msg, str):
            record.msg = redact(formatted)
            record.args = None
        return True


# --------------------------------------------------------------------------
# Bus: one monkeypatch, many subscribers.
# --------------------------------------------------------------------------

_LOCK = threading.RLock()
_SINKS = []
_SEQ = 0
_T0 = None
_REFCOUNT = 0
_ORIGINAL = None
_PATCHED_CLS = None
_MODEL_CLS = None
_MODEL_ORIGINAL = None
_CALL_SEQ = itertools.count(1)

_STREAM = os.environ.get("CLI_STREAM", "1").lower() not in ("0", "false", "no")
_STREAM_OK = True  # flipped off permanently if a provider rejects stream=True
_STREAM_OPTS = True  # flipped off if a provider rejects stream_options

# Cooperative cancellation. Every DR agent wraps its model call in
# `except Exception`, and global_execution_agent.execute_graph only clears its
# is_executing flag in a finally: — so a cancellation that derives from
# Exception would be swallowed by the retry loop, while one that derives from
# BaseException sails through both and still runs the finally.
_CANCEL = threading.Event()


class CliCancelled(BaseException):
    """Raised inside the patched model call when the user hits Ctrl+C."""


def request_cancel():
    _CANCEL.set()


def clear_cancel():
    _CANCEL.clear()


def cancel_requested():
    return _CANCEL.is_set()


def _check_cancel():
    if _CANCEL.is_set():
        raise CliCancelled("用户中断")


def elapsed():
    return round(time.monotonic() - _T0, 3) if _T0 else 0.0


def emit(event, agent=None, data=None):
    """Publish one event to every subscriber. Never raises into the workflow."""
    global _SEQ
    with _LOCK:
        if _T0 is None:
            return
        _SEQ += 1
        record = {
            "seq": _SEQ,
            "t": elapsed(),
            "wall": datetime.now().isoformat(timespec="milliseconds"),
            "tid": threading.get_ident(),
            "event": event,
            "agent": agent,
            "data": _sanitize(data or {}),
        }
        sinks = list(_SINKS)
    for sink in sinks:
        try:
            sink(record)
        except Exception:
            pass  # a broken renderer must never take down the research run


def subscribe(sink):
    with _LOCK:
        if sink not in _SINKS:
            _SINKS.append(sink)


def unsubscribe(sink):
    with _LOCK:
        if sink in _SINKS:
            _SINKS.remove(sink)


def acquire():
    """Install the patch (refcounted, so recorder + renderer can coexist)."""
    global _REFCOUNT, _T0, _ORIGINAL, _PATCHED_CLS
    with _LOCK:
        _REFCOUNT += 1
        if _T0 is None:
            _T0 = time.monotonic()
        if _PATCHED_CLS is not None:
            return
        # base_agent does `from utils.logger import ...`, and `utils` lives
        # under dr_agents/ — that dir only lands on sys.path as a side effect
        # of importing DRAgent, so make sure of it here.
        import sys
        dr_agents = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "internagent", "mas", "agents", "dr_agents")
        if dr_agents not in sys.path:
            sys.path.insert(0, dr_agents)

        from internagent.mas.agents.dr_agents.agents.base_agent import BaseAgent

        def send_redis_event(self, event_type, event_data):
            # agent/node identity is what the UI groups by; the upstream
            # payload only carries it for some event types.
            ident = {k: getattr(self, k, None)
                     for k in ("node_id", "subtask_id", "task_id")}
            emit(event_type, agent=type(self).__name__,
                 data={**ident, **(event_data or {})})
            # Chain through so a real Redis deployment keeps working.
            if _ORIGINAL is not None:
                try:
                    return _ORIGINAL(self, event_type, event_data)
                except Exception:
                    return False
            return True

        _PATCHED_CLS = BaseAgent
        _ORIGINAL = BaseAgent.send_redis_event
        BaseAgent.send_redis_event = send_redis_event

        if _MODEL_CLS is None:
            _install_model_hook()


def release():
    global _REFCOUNT, _ORIGINAL, _PATCHED_CLS, _T0, _MODEL_CLS, _MODEL_ORIGINAL
    with _LOCK:
        _REFCOUNT = max(0, _REFCOUNT - 1)
        if _REFCOUNT == 0 and _PATCHED_CLS is not None:
            _PATCHED_CLS.send_redis_event = _ORIGINAL
            _PATCHED_CLS = None
            _ORIGINAL = None
            _T0 = None
            if _MODEL_CLS is not None:
                _MODEL_CLS.create = _MODEL_ORIGINAL
                _MODEL_CLS = None
                _MODEL_ORIGINAL = None


# --------------------------------------------------------------------------
# Model-call hook: the only place where reasoning exists.
# --------------------------------------------------------------------------

# Which module a call came from, as the user should read it. Keyed by file
# stem because that survives package moves better than a dotted path.
_STAGE_LABELS = {
    "global_planner_agent": "全局规划",
    "planner_agent": "子任务规划",
    "execution_agent": "节点执行",
    "global_execution_agent": "节点调度",
    "coordinator_agent": "协调器",
    "synthesizer_agent": "答案合成",
    "task_memory": "记忆检索",
    "answer_agent": "答案节点",
}

# Every function in the DR tree that actually issues a model call, mapped to
# what it is doing. Falling back to the raw function name keeps an unmapped
# call visible rather than anonymous.
_CALL_LABELS = {
    "_generate_plan": "生成执行图",
    "_generate_subtasks": "分解子任务",
    "execute_one_step": "选择下一步",
    "_generate_subtask_response": "汇总子任务",
    "_call_llm_for_modification": "调整执行图",
    "_synthesize_qa": "问答合成",
    "_generate_outline": "生成提纲",
    "_write_single_section": "撰写章节",
    "_polish_single_section": "润色章节",
    "_write_introduction_section": "撰写引言",
}

_MAX_WALK = 50
# How many DR frames to keep scanning for a round counter once the stage is
# known: the planner's iteration lives in plan(), one frame above the call.
_MAX_DR_FRAMES = 8


def _inspect_caller(frame):
    """Identify which DR agent is making this model call.

    The openai client has no idea what an InternAgent agent is, and the
    adapters pass nothing identifying — the stack is the only source of that
    context, and it is what turns an anonymous "model call" into
    "全局规划 · 生成执行图 · 第 2/10 轮". Cheap relative to the round trip.
    """
    stage = detail = round_label = None
    ident = {}
    depth = dr_depth = 0
    while frame is not None and depth < _MAX_WALK:
        path = frame.f_code.co_filename.replace("\\", "/")
        if "/dr_agents/" in path and "/models/" not in path and "/camel/" not in path:
            dr_depth += 1
            locals_ = frame.f_locals
            stem = os.path.basename(path)
            stem = stem[:-3] if stem.endswith(".py") else stem
            if stage is None and stem in _STAGE_LABELS:
                stage = _STAGE_LABELS[stem]
                name = frame.f_code.co_name
                detail = _CALL_LABELS.get(name, name)
            if round_label is None and locals_.get("max_iter"):
                cur = locals_.get("current_iter", locals_.get("idx"))
                if isinstance(cur, int):
                    round_label = f"第 {cur}/{locals_['max_iter']} 轮"
            if not ident:
                obj = locals_.get("self")
                if obj is not None and hasattr(obj, "send_redis_event"):
                    ident = {k: getattr(obj, k, None)
                             for k in ("node_id", "subtask_id", "task_id")}
            if stage is not None and ident and (
                    round_label is not None or dr_depth >= _MAX_DR_FRAMES):
                break
        frame = frame.f_back
        depth += 1
    if round_label:
        detail = f"{detail} · {round_label}" if detail else round_label
    return stage or "模型调用", detail, ident


def _prompt_preview(args, kwargs, limit=200):
    messages = kwargs.get("messages")
    if messages is None:
        for a in args:
            if isinstance(a, list) and a and isinstance(a[0], dict) and "role" in a[0]:
                messages = a
                break
    if not messages:
        return ""
    last = messages[-1]
    text = last.get("content") if isinstance(last, dict) else None
    if not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _content_of(response):
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _usage_of(response):
    """prompt/completion/total tokens, or {} when this provider doesn't report.

    Only the numbers the endpoint itself sends are surfaced — nothing is
    estimated, so a missing entry means "not reported", not "zero".
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    out = {}
    for src, dst in (("prompt_tokens", "prompt"), ("completion_tokens", "completion"),
                     ("total_tokens", "total")):
        val = getattr(usage, src, None)
        if val is None and isinstance(usage, dict):
            val = usage.get(src)
        if isinstance(val, (int, float)):
            out[dst] = int(val)
    if "total" not in out and "prompt" in out and "completion" in out:
        out["total"] = out["prompt"] + out["completion"]
    return out


class _FakeToolCall:
    """Stands in for openai's ChatCompletionMessageToolCall, whose fields the
    execution agent reads as attributes after a streamed tool call."""

    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = types.SimpleNamespace(name=name, arguments=arguments)


def _streamed(original, resource, args, kwargs, call, stage, ident):
    """Re-issue a non-streaming call as a stream, republishing the deltas as
    events, then rebuild the object shape the adapters expect.

    Only plain text generation gets this treatment (callers pass tools and
    response_format through untouched) because that is both the best-supported
    streaming path and the one that carries the visible reasoning.
    """
    global _STREAM_OPTS
    params = dict(kwargs)
    params["stream"] = True
    # Streaming responses carry no usage block unless asked for. This is the one
    # request field the hook adds, and it only asks the endpoint for extra
    # metadata — the prompt and the model's answer are untouched. An endpoint
    # that rejects the field loses token counts but keeps streaming, which is
    # why this falls back here rather than letting create() disable streaming.
    if _STREAM_OPTS:
        params["stream_options"] = {"include_usage": True}
    try:
        stream = original(resource, *args, **params)
    except Exception as exc:
        # Only retry without the field if the endpoint complained about *that*
        # field. An endpoint that rejects stream=True outright must fall through
        # to create()'s one-shot non-streaming fallback, not burn a second call.
        if not _STREAM_OPTS or "stream_options" not in str(exc).lower():
            raise
        _STREAM_OPTS = False
        params.pop("stream_options", None)
        stream = original(resource, *args, **params)

    content, reasoning = [], []
    tool_calls = {}
    finish = usage = model = rid = None
    pending = {}   # kind -> list[str], flushed on a time/size threshold
    last_flush = time.monotonic()

    def flush(force=False):
        nonlocal last_flush
        if not pending:
            return
        now = time.monotonic()
        buffered = sum(len(s) for v in pending.values() for s in v)
        if not force and now - last_flush < 0.15 and buffered < 400:
            return
        for kind in ("reasoning", "content"):
            pieces = pending.pop(kind, None)
            if pieces:
                emit("model_call_chunk", data={
                    "call": call, "stage": stage, "kind": kind,
                    "chars": sum(len(s) for s in pieces),
                    "text": "".join(pieces), **ident})
        last_flush = now

    for chunk in stream:
        _check_cancel()
        usage = getattr(chunk, "usage", None) or usage
        model = getattr(chunk, "model", None) or model
        rid = getattr(chunk, "id", None) or rid
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            finish = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        think = getattr(delta, "reasoning_content", None)
        if think:
            reasoning.append(think)
            pending.setdefault("reasoning", []).append(think)
            flush()
        text = getattr(delta, "content", None)
        if text:
            content.append(text)
            pending.setdefault("content", []).append(text)
            flush()
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = tool_calls.setdefault(
                getattr(tc, "index", 0) or 0,
                {"id": None, "name": "", "arguments": ""})
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                slot["name"] += getattr(fn, "name", None) or ""
                slot["arguments"] += getattr(fn, "arguments", None) or ""
    flush(force=True)

    message = types.SimpleNamespace(
        role="assistant",
        content="".join(content) or None,
        reasoning_content="".join(reasoning) or None,
        tool_calls=[_FakeToolCall(v["id"], v["name"], v["arguments"])
                    for v in tool_calls.values()] or None,
    )
    response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            index=0, message=message, finish_reason=finish or "stop")],
        usage=usage, model=model, id=rid)
    return response, "".join(content), "".join(reasoning)


def _install_model_hook():
    global _MODEL_CLS, _MODEL_ORIGINAL
    try:
        from openai.resources.chat.completions.completions import Completions
    except ImportError:
        try:
            from openai.resources.chat.completions import Completions
        except ImportError:
            return
    original = Completions.create

    def create(self, *args, **kwargs):
        global _STREAM_OK
        _check_cancel()
        stage, detail, ident = _inspect_caller(sys._getframe(1))
        call = next(_CALL_SEQ)
        t0 = time.monotonic()
        preview = _prompt_preview(args, kwargs)
        streamable = (_STREAM and _STREAM_OK and not kwargs.get("stream")
                      and not kwargs.get("tools")
                      and "response_format" not in kwargs)
        emit("model_call_start", data={
            "call": call, "stage": stage, "detail": detail,
            "model": kwargs.get("model"), "prompt": preview,
            "stream": streamable, **ident})

        try:
            if streamable:
                try:
                    response, text, think = _streamed(
                        original, self, args, kwargs, call, stage, ident)
                except CliCancelled:
                    raise
                except Exception as exc:
                    # One provider that rejects stream=True would otherwise
                    # double-bill every subsequent call, so stop trying.
                    _STREAM_OK = False
                    emit("model_stream_disabled",
                         data={"call": call, "error": str(exc)[:300]})
                    response = original(self, *args, **kwargs)
                    text, think = _content_of(response), ""
            else:
                response = original(self, *args, **kwargs)
                text, think = _content_of(response), ""
                _check_cancel()
        except CliCancelled:
            emit("model_call_cancelled", data={
                "call": call, "stage": stage, "detail": detail,
                "secs": round(time.monotonic() - t0, 2), **ident})
            raise
        except Exception as exc:
            emit("model_call_error", data={
                "call": call, "stage": stage, "detail": detail,
                "secs": round(time.monotonic() - t0, 2),
                "error": type(exc).__name__, "message": str(exc)[:400],
                **ident})
            raise
        emit("model_call_end", data={
            "call": call, "stage": stage, "detail": detail,
            "secs": round(time.monotonic() - t0, 2),
            "chars": len(text or "") + len(think or ""),
            "text": text, "reasoning": think,
            "usage": _usage_of(response), **ident})
        return response

    _MODEL_CLS, _MODEL_ORIGINAL = Completions, original
    Completions.create = create


class QueueSink:
    """Feed events to a renderer living on another thread."""

    def __init__(self, maxsize=0):
        self.q = queue.Queue(maxsize=maxsize)

    def __call__(self, record):
        try:
            self.q.put_nowait(record)
        except queue.Full:
            pass  # drop rather than block the workflow

    def attach(self):
        acquire()
        subscribe(self)
        return self

    def detach(self):
        unsubscribe(self)
        release()


class EventRecorder:
    """Thread-safe JSONL sink. Appends and flushes per line, so a killed run
    still leaves a usable partial recording."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._lock = threading.Lock()
        self._fh = None
        self._counts = {}

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def start(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")
        acquire()
        subscribe(self)
        self.mark("recorder_start", path=self.path)
        return self

    def stop(self):
        self.mark("recorder_stop", counts=dict(self._counts))
        unsubscribe(self)
        release()
        with self._lock:
            if self._fh:
                self._fh.flush()
                self._fh.close()
                self._fh = None

    @property
    def counts(self):
        return dict(self._counts)

    def mark(self, event, **data):
        """Emit a synthetic event (not originating from an agent)."""
        emit(event, agent=None, data=data)

    def __call__(self, record):
        with self._lock:
            if self._fh is None:
                return
            self._counts[record["event"]] = self._counts.get(record["event"], 0) + 1
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()


def summarize(path):
    """Compact post-run report: what fired, how often, how long."""
    from collections import Counter

    counts = Counter()
    lanes = set()
    first = last = None
    total = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            rec = json.loads(line)
            counts[rec["event"]] += 1
            lanes.add(rec["tid"])
            first = first if first is not None else rec["t"]
            last = rec["t"]
    return {
        "records": total,
        "distinct_events": len(counts),
        "counts": counts.most_common(),
        "threads": len(lanes),
        "span_s": round((last or 0) - (first or 0), 2),
    }


def load(path):
    """Read a recording back as a list of records (for replay-driven dev)."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def replay(path, sink, speed=1.0):
    """Re-drive a recording into any sink, preserving the original timing.

    This is how the live renderer gets developed and tuned without spending
    API credits: capture one real run, then iterate on the UI against it.
    """
    records = load(path)
    prev = None
    for rec in records:
        if prev is not None and speed > 0:
            gap = (rec["t"] - prev) / speed
            if gap > 0:
                time.sleep(min(gap, 5.0))
        prev = rec["t"]
        sink(rec)
    return len(records)

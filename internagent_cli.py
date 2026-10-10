#!/usr/bin/env python3
"""Terminal CLI for InternAgent with runtime (never persisted) API credentials.

Design constraint: the API key is collected via getpass on each invocation and
lives only in this process's environment. It is never written to .env, the
litellm config, or any temp file.
"""

import argparse
import getpass
import os
import re
import sys
from collections import namedtuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.abspath(__file__))
PROXY_DIR = os.path.join(os.path.dirname(REPO), "proxy")
LLPROXY_PY = r"D:\Anaconda\envs\llproxy\python.exe"

# litellm prefix is what tells the proxy how to talk upstream; base_url is what
# the OpenAI-compatible client in InternAgent needs. `note` is the one-column
# hint the wizard shows next to each name — reachability is stated because
# picking the wrong one costs a minute of timeouts before anything says so.
Provider = namedtuple("Provider", "base prefix note")
PROVIDERS = {
    "deepseek": Provider("https://api.deepseek.com/v1", "deepseek",
                         "官方端点 · V3 / R1，国内直连"),
    "qwen": Provider("https://dashscope.aliyuncs.com/compatible-mode/v1",
                     "openai", "阿里百炼 dashscope，国内直连"),
    "moonshot": Provider("https://api.moonshot.cn/v1", "openai",
                         "Kimi · 长上下文，国内直连"),
    "zhipu": Provider("https://open.bigmodel.cn/api/paas/v4", "openai",
                      "智谱 GLM，国内直连"),
    "siliconflow": Provider("https://api.siliconflow.cn/v1", "openai",
                            "硅基流动 · 聚合开源模型，国内直连"),
    "openrouter": Provider("https://openrouter.ai/api/v1", "openrouter",
                           "海外聚合，模型最多；国内通常要代理"),
    "openai": Provider("https://api.openai.com/v1", "openai",
                       "OpenAI 官方；国内直连不通"),
    "custom": Provider(None, "openai",
                       "手填 base_url · 本地 Ollama / vLLM 也走这里"),
}

# What each mode actually costs, from config_{mode}.yaml: main.max_iter is the
# layer cap, global_execution.planner.max_subtasks the fan-out per node, and
# synthesizer.polish adds a second pass over every section.
MODES = (
    ("qa", "深度调研问答 · 单阶段综合，节点上限 7，不润色"),
    ("simple", "轻量背景调研 · 节点上限 5，每节点 2 个子任务"),
    ("complex", "完整深度调研 · 开 coordinator 修图 + 逐节润色，最贵"),
)


def fail(msg):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


# Non-secret defaults remembered between runs so the wizard stops asking for
# the base_url every time. The API key is NEVER written here — see
# feedback-secrets-runtime-only; it is prompted per invocation and lives only
# in this process's environment.
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".internagent.json")
CONFIG_KEYS = ("provider", "base_url", "model", "mode", "timeout")

# Edits to tracked upstream files that make third-party APIs work at all. They
# are commented "Local patch" in place; a git pull or checkout can wipe them with
# nothing failing, so `internagent doctor` greps for one distinctive line of each.
# (path, marker to find, what silently breaks without it)
DR = os.path.join("internagent", "mas", "agents", "dr_agents")
LOCAL_PATCHES = (
    (os.path.join(DR, "models", "__init__.py"),
     "Local patch: route any OpenAI-compatible vendor",
     "kimi-* / glm-* 等模型名会报 Unsupported model"),
    (os.path.join(DR, "workflow", "main.py"),
     "# Local patch: follow config model.default_model",
     "查询分析步骤会硬要 o4-mini"),
    (os.path.join(DR, "workflow", "main.py"),
     "# Local patch: the layer cap used to be advisory",
     "图层循环没有上限，工具可以一直调用下去"),
    (os.path.join(DR, "tools", "tool_integration.py"),
     "# Local patch: honor EXTRACTION_MODEL",
     "网页摘要会用写死的 gpt-4o-mini"),
    (os.path.join(DR, "config_qa.yaml"),
     '# - "volc_search"',
     "启用了需要企业 key 的 volc_search 工具"),
    (os.path.join(DR, "agents", "global_planner_agent.py"),
     "# Local patch: the inner `while idx <= max_iter` breaks with",
     "key 无效时外层 while True 会无限重试（实测 4 分钟 5255 条 401）"),
    (os.path.join(DR, "agents", "global_planner_agent.py"),
     "Local patch: reset the graph this planner produced last time",
     "规划失败的那一轮会静默重跑上一轮问题的图"),
    (os.path.join(DR, "agents", "global_planner_agent.py"),
     "Local patch: this re-plan used to be uncapped",
     "模型连续给出带环的图时无限重规划，且把 \"null\" 当成当前图发给模型"),
    (os.path.join(DR, "workflow", "main.py"),
     "# Local patch: the planner now gives up",
     "规划没产出图时崩在 None.to_dict()，只留下一句 workflow failed"),
    (os.path.join(DR, "agents", "task", "execution_agent.py"),
     "Local patch: an identical (tool, arguments) pair used to be re-issued",
     "同一个检索在同一子任务里一遍遍重发，直到预算耗尽"),
    (os.path.join(DR, "agents", "task", "execution_agent.py"),
     "Local patch: this used to return",
     "工具报错被记成调用成功，「全部失败就停」的分支永远进不去"),
    (os.path.join(DR, "agents", "task", "planner_agent.py"),
     "Local patch: this fallback used to hand back",
     "端点挂掉时反而拆出 4 个笼统子任务，调用量比成功时还大"),
    (os.path.join("internagent", "mas", "agents", "dr_agent.py"),
     "Local patch: carry the real reason out of the except block",
     "工作流崩了只剩一句兜底文本，看不出崩在哪"),
)


def _load_user_config():
    import json
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: data[k] for k in CONFIG_KEYS if k in data}
    except (OSError, ValueError):
        return {}


def _save_user_config(cfg):
    import json
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({k: cfg[k] for k in CONFIG_KEYS if cfg.get(k) is not None},
                      fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _probe_models(base_url, key, timeout=25):
    """Cheap pre-flight: catches a typo'd key or base_url in seconds instead of
    after the workflow has already spent a minute initialising.
    Returns (model_ids, error_string)."""
    import json
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        code = getattr(exc, "code", "")
        return [], f"HTTP {code} {exc}" if code else str(exc)
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id")), None


def resolve_base(args):
    """Return (base_url, litellm_prefix). Provider preset wins over --base-url
    only when no explicit URL is given."""
    if args.provider == "custom":
        base = args.base_url or os.environ.get("OPENAI_API_BASE_URL")
        if not base:
            base = input("上游 base_url（形如 https://host/v1）: ").strip()
        if not base:
            fail("custom 供应商必须提供 base_url")
        return base, "openai"
    p = PROVIDERS[args.provider]
    return args.base_url or p.base, p.prefix


def collect_credentials(args):
    """Prompt for the secret unless it is already in the environment."""
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key:
            fail(f"环境变量 {args.api_key_env} 为空")
        return key, "env"
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("UPSTREAM_API_KEY")
    if key and not args.prompt_key:
        return key, "env"
    key = _prompt_secret().strip()
    if not key:
        fail("未提供 API key")
    return key, "prompt"


# get_model() dispatches on the model-name prefix, and each provider-specific
# class reads its own variable — the generic OPENAI_* ones are never consulted
# for those, so publishing only OPENAI_API_KEY silently breaks them.
PROVIDER_ENV = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    "qwen": ("QWEN_API_KEY", "QWEN_BASE_URL"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_BASE_URL"),
    "intern": ("INTERN_S1_API_KEY", None),
}


def _match_provider(model):
    """Mirror the prefix rules in dr_agents/models/__init__.py:get_model()."""
    low = model.lower()
    if "deepseek" in low:
        return "deepseek"
    for prefix in ("qwen", "gemini", "intern"):
        if low.startswith(prefix):
            return prefix
    return None


def publish(key, base_url, model=None):
    os.environ["OPENAI_API_KEY"] = key
    os.environ["OPENAI_API_BASE_URL"] = base_url
    os.environ["OPENAI_BASE_URL"] = base_url
    provider = _match_provider(model) if model else None
    if not provider:
        return
    key_var, base_var = PROVIDER_ENV[provider]
    os.environ[key_var] = key
    if base_var:
        # These classes build the client as f"{base_url}/v1" themselves.
        root = base_url.rstrip("/")
        os.environ[base_var] = root[:-3] if root.endswith("/v1") else root


def cmd_check(args):
    base_url, key = resolve_base(args)[0], collect_credentials(args)[0]
    print(f"探测 {base_url.rstrip('/')}/models", flush=True)
    ids, err = _probe_models(base_url, key)
    if err:
        fail(f"{err}\n这个 key 与该 base_url 不匹配，换 --provider 或 --base-url 再试。")

    print(f"\n可用模型 {len(ids)} 个:")
    for mid in ids:
        print(f"  {mid}")
    if not ids:
        print("  (返回 200 但没有模型列表，可能这个端点是公开的、并未真正鉴权)")


_QUIET = {"console_handler": None, "log_file": None}


def _verbose_console_handler():
    import logging

    import cli_events

    if _QUIET["console_handler"] is None:
        h = logging.StreamHandler(sys.stderr)
        h.setLevel(logging.INFO)
        h.addFilter(cli_events.RedactingLogFilter())
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"))
        _QUIET["console_handler"] = h
    return _QUIET["console_handler"]


def _set_verbose(on):
    import logging

    h = _verbose_console_handler() if on else None
    # Identity check in the addHandler filter lets this handler through.
    for name, obj in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(obj, logging.Logger) and name.startswith("InternResearch"):
            if on and h not in obj.handlers:
                obj.addHandler(h)
            elif not on:
                obj.removeHandler(h)


def _install_quiet_logging(log_dir=None):
    """Silence all InternResearch loggers to file-only, for every creation path.

    The workflow creates node loggers at runtime (get_node_logger), each with
    its own console StreamHandler, and there are two module instances of the
    logger module. Instead of patching each, install one global filter on
    Logger.addHandler that refuses console handlers on InternResearch.* loggers.
    Must run BEFORE importing DRAgent."""
    import logging
    import logging.handlers
    from datetime import datetime

    import cli_events

    if log_dir is None:
        log_dir = os.path.join(REPO, "logs")
    os.makedirs(log_dir, exist_ok=True)

    def guard(handler):
        """Put the key redactor on every file under logs/, not just our own.

        LoggerConfig builds its own RotatingFileHandlers (logs/InternResearch
        _<date>.log plus one per node logger), and those are what a provider's
        401 body lands in when it echoes the key it was given. Handler filters
        are the only boundary that sees records from loggers we do not own.
        """
        if isinstance(handler, logging.FileHandler) and not any(
                isinstance(f, cli_events.RedactingLogFilter)
                for f in getattr(handler, "filters", [])):
            handler.addFilter(cli_events.RedactingLogFilter())
        return handler

    log_file = os.path.join(log_dir, f"cli_{datetime.now():%Y-%m-%d}.log")
    _QUIET["log_file"] = log_file

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=50, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.addFilter(cli_events.RedactingLogFilter())
    fh.setFormatter(logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))

    # Existing loggers: swap console -> file.
    for name, obj in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(obj, logging.Logger) and name.startswith("InternResearch"):
            for h in list(obj.handlers):
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    obj.removeHandler(h)
                guard(h)
            if not any(isinstance(h, logging.FileHandler) for h in obj.handlers):
                obj.addHandler(fh)
    root = logging.getLogger("InternResearch")
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(fh)
    # LoggerConfig._create_logger early-returns when handlers exist, so its
    # propagate=False never applies to this pre-created logger. Without this,
    # records bubble to root, where camel's logging.basicConfig(stream=stdout)
    # handler prints them.
    root.propagate = False
    # camel/logger.py installs a stdout StreamHandler on the root logger at
    # import time; stop it from being created at all.
    os.environ.setdefault("CAMEL_LOGGING_DISABLED", "true")

    # Future loggers: refuse console handlers outright, except the verbose one.
    original_add = logging.Logger.addHandler

    def _filtered_add(self, handler):
        if (isinstance(handler, logging.StreamHandler)
                and not isinstance(handler, logging.FileHandler)
                and (self.name or "").startswith("InternResearch")
                and handler is not _QUIET["console_handler"]):
            return
        guard(handler)
        return original_add(self, handler)

    logging.Logger.addHandler = _filtered_add
    return log_file


_PTK_OK = None


def _ptk_available():
    """Probe prompt_toolkit once. It needs a real Win32 console screen buffer,
    which a Git-Bash/MSYS pty does not provide."""
    global _PTK_OK
    if _PTK_OK is None:
        try:
            from prompt_toolkit.output.defaults import create_output
            create_output()
            _PTK_OK = True
        except Exception:
            _PTK_OK = False
    return _PTK_OK


def _disable_ptk():
    global _PTK_OK
    _PTK_OK = False


def _is_win_console():
    """A real Win32 console (cmd / PowerShell / Windows Terminal), as opposed to
    a Git-Bash/MSYS pty, which sets TERM and has no console screen buffer."""
    return (os.name == "nt" and sys.stdin.isatty()
            and not os.environ.get("MSYSTEM") and not os.environ.get("TERM"))


def _masked_console_input(message):
    """Hidden input that echoes one mask character per keystroke.

    prompt_toolkit's password mode echoes nothing at all, which reads as "my
    input is not registering". Worse, with a CJK IME left in Chinese mode the
    keystrokes are composed into pinyin, so the user submits a wrong key with
    no way to see it. A visible mask makes both failure modes obvious. A paste
    (Ctrl+V) arrives as a burst of characters and is echoed the same way, and
    unlike typing it is immune to the IME.
    """
    import msvcrt

    sys.stdout.write(message)
    sys.stdout.flush()
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":  # Ctrl+C
            sys.stdout.write("\n")
            raise KeyboardInterrupt
        if ch == "\x08":  # Backspace
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        if ch in ("\x00", "\xe0"):  # function/arrow-key prefix byte
            msvcrt.getwch()
            continue
        chars.append(ch)
        sys.stdout.write("•")
        sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chars)


def _read_clipboard():
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _prompt_secret(message="API key（以 • 回显、不保存）: "):
    if not sys.stdin.isatty():
        return input(message).strip()  # piped / scripted runs
    if _is_win_console():
        try:
            return _masked_console_input(message).strip()
        except KeyboardInterrupt:
            print()
            sys.exit(130)
        except Exception:
            pass
    # MSYS pty or non-Windows: getpass. Keys are ASCII so the IME caveat that
    # rules getpass out for CJK questions does not apply here.
    return getpass.getpass(message).strip()


def _config_section(mode, key):
    """One top-level block of the config file this mode would load."""
    path = os.path.join(REPO, "internagent", "mas", "agents", "dr_agents",
                        f"config_{mode}.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        section = data.get(key)
        return dict(section) if isinstance(section, dict) else {}
    except Exception:
        # A missing file is not our problem: DRAgent loads the config itself and
        # will say so. Fall back to the plain override, i.e. today's behaviour.
        return {}


def _section_override(mode, key, **updates):
    """A whole config section, with dotted paths inside it changed.

    DRAgent merges overrides with a shallow dict.update (dr_agent.py:137), so an
    override must carry the file's own section along with it: sending
    {"global_execution": {"max_workers": 3}} on its own would delete the
    planner/execution sub-sections, the enabled_tools lists and need_memory.
    """
    section = _config_section(mode, key)
    for dotted, value in updates.items():
        parts = dotted.split(".")
        node = section
        for part in parts[:-1]:
            child = node.get(part)
            # Copy before writing: the nested dicts still alias the loaded file.
            child = dict(child) if isinstance(child, dict) else {}
            node[part] = child
            node = child
        node[parts[-1]] = value
    return section


def _model_override(mode, model):
    """The workflow_config `model:` block to send for --model.

    Handing DRAgent {"default_model": model} replaces the whole model section and
    silently drops per-stage choices — global_planner_model, synthesizer_model,
    global_execution_model, task_agent_model — that workflow/main.py:57-82 reads.
    Starting from the file's own section changes only the default.
    """
    return _section_override(mode, "model", default_model=model)


def _budget_override(mode, max_workers=None, max_tool_calls=None):
    """The workflow_config `global_execution:` block for the cost gate.

    max_workers is how many nodes run at once (a metered key gets 429s at 10),
    max_tool_calls is the per-subtask cap on model turns in
    global_execution.execution, i.e. the loop ExecutionAgent runs.
    """
    updates = {}
    if max_workers:
        updates["max_workers"] = max_workers
    if max_tool_calls:
        updates["execution.max_tool_calls"] = max_tool_calls
    if not updates:
        return None
    return _section_override(mode, "global_execution", **updates)


def _budget_overrides(mode, args):
    """workflow_config blocks for the cost gate, from --max-* flags.

    The bounds that decide how many model calls one question can make are
    nodes-parallel x subtask-turns x layers, and all three live in the YAML the
    workflow loads, so overriding them means handing DRAgent the whole section.
    """
    overrides = {}
    workers = getattr(args, "max_workers", None)
    calls = getattr(args, "max_tool_calls", None)
    layers = getattr(args, "max_layers", None)
    if workers or calls:
        overrides["global_execution"] = _budget_override(mode, workers, calls)
    if layers:
        overrides["main"] = _section_override(mode, "main", max_iter=layers)
    return overrides


def _budget_summary(mode, args, compact=False):
    """The three multipliers that bound one question, as one readable line.

    Printed so the numbers are visible before a run starts rather than discovered
    in the YAML after a bill: the workflow's own defaults assume a server-side
    quota, not a metered personal key.

    ``compact`` drops the flag-name list and the wordier separators. The card this
    goes into cuts a value rather than wrapping it, and at 60 columns the long
    form lost a digit to that cut — "图层 ≤1…" says nothing about the layer cap
    it exists to show.
    """
    execution = _config_section(mode, "global_execution")
    main = _config_section(mode, "main")
    sub = execution.get("execution")
    sub = sub if isinstance(sub, dict) else {}
    # The three fallbacks are the defaults the agents themselves use
    # (global_execution_agent.py:47, execution_agent.py:101, main.py:151), so a
    # run without a readable config file still reports what will really happen.
    workers = getattr(args, "max_workers", None) or execution.get("max_workers", 10)
    calls = getattr(args, "max_tool_calls", None) or sub.get("max_tool_calls", 15)
    layers = getattr(args, "max_layers", None) or main.get("max_iter", 10)
    if compact:
        return f"并发 ≤{workers} · 工具 ≤{calls} 轮/子任务 · 图层 ≤{layers}"
    return (f"并发节点 ≤{workers} · 每子任务 ≤{calls} 轮工具 · 图层 ≤{layers}"
            f"（--max-workers / --max-tool-calls / --max-layers）")


def _init_agent(args, console):
    """Shared setup for qa/chat: creds, quiet logging, DRAgent construction."""
    base_url, key = resolve_base(args)[0], collect_credentials(args)[0]
    publish(key, base_url, args.model)
    if args.model:
        os.environ["EXTRACTION_MODEL"] = args.model

    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO, ".env"), override=False)
    publish(key, base_url, args.model)

    log_file = _install_quiet_logging()
    if args.verbose:
        _set_verbose(True)

    sys.path.insert(0, REPO)

    with console.status("[bold cyan]初始化工作流…[/bold cyan]", spinner="dots"):
        from internagent.mas.agents.dr_agent import DRAgent

        overrides = {}
        if args.model:
            overrides["model"] = _model_override(args.mode, args.model)
        overrides.update(_budget_overrides(args.mode, args))
        agent = DRAgent(
            model=args.model or "o4-mini",
            config={"mode": args.mode, **({"workflow_config": overrides} if overrides else {})},
        )
    if agent.workflow is None:
        fail("DR workflow 初始化失败，原因见日志中的 'Failed to initialize DR workflow' 警告。"
             "常见于模型名前缀与密钥不匹配（deepseek*/qwen*/gemini*/intern* 各自读独立变量）。"
             f"\n完整日志: {log_file}")
    return agent, log_file


class Interrupted(RuntimeError):
    """A run the user cut short with Ctrl+C.

    `abandoned` means the worker thread was still blocked when cli_live gave up
    on it, so GlobalExecutionAgent.is_executing is still held and reusing that
    DRAgent would fail every subsequent turn with 图正在执行中.
    """

    def __init__(self, abandoned=False):
        super().__init__("已中断")
        self.abandoned = abandoned


class TurnTimeout(RuntimeError):
    """A run the --timeout deadline cut short.

    Raised by _ask, which enforces the deadline from the thread that can actually
    act on it — cli_live's render loop for live runs, a watchdog join otherwise.
    An asyncio.wait_for around DRAgent.execute cannot: its body is synchronous,
    so the timer only gets a chance to fire after the whole run returns.
    `abandoned` carries the same meaning as in Interrupted.
    """

    def __init__(self, timeout, abandoned=False, log_file=None):
        super().__init__(
            f"执行超时（{timeout}s）。可能是端点没有返回，也可能是工具在反复调用，"
            f"日志里能看到最后一步；要跑久一点就加大 --timeout。"
            + (f"\n完整日志: {log_file}" if log_file else ""))
        self.timeout = timeout
        self.abandoned = abandoned


def _ask(agent, args, question, console, log_file, live=False, archive=None):
    """Run one question through the workflow; returns (answer, summary).

    live=True renders the Claude Code-style streaming display (cli_live) instead
    of a single spinner; the workflow still runs on a worker thread either way.
    archive collects each model call's full reasoning for /think. summary is the
    turn's totals, or None on the non-live path where nothing measures them.
    """
    import asyncio
    import contextlib
    import io
    import logging
    import threading

    # Redirect python-level prints (workflow demo banners, tool dumps) to an
    # in-memory buffer, then route them through the logging file handler.
    # A real file handle here would deadlock the RotatingFileHandler's rename
    # on Windows and spill a logging-error traceback per record.
    sink = io.StringIO()

    timeout = getattr(args, "timeout", 300)

    async def _coro():
        return await asyncio.wait_for(
            agent.execute({"task": question, "file_path": args.file}, {}),
            timeout=timeout)

    model_label = args.model or "(config 默认)"
    record_path = getattr(args, "record_events", None)

    import cli_events
    recorder_ctx = cli_events.EventRecorder(record_path) if record_path else None

    def _worker():
        cli_events.emit("workflow_start", data={
            "question": question, "mode": args.mode, "model": model_label})
        try:
            ans = str(asyncio.run(_coro()))
        except asyncio.TimeoutError as exc:
            cli_events.emit("workflow_timeout", data={"timeout": timeout})
            raise RuntimeError(
                f"执行超时（{timeout}s）。可能是 API key 无效导致无限重试。"
                f"\n完整日志: {log_file}") from exc
        except RuntimeError:
            raise
        except cli_events.CliCancelled:
            raise  # a Ctrl+C is not a workflow failure; run_with_live reports it
        except BaseException as exc:
            cli_events.emit("workflow_error",
                            data={"error": type(exc).__name__, "detail": str(exc)})
            raise
        cli_events.emit("workflow_end", data={"answer_chars": len(ans)})
        return ans

    stub_marker = None
    summary = None
    if recorder_ctx is not None:
        recorder_ctx.start()
    try:
        if live:
            from rich.console import Console

            import cli_live
            # rich resolves Console.file dynamically, so redirect_stdout would
            # swallow the live display as well as the workflow's prints. Pin the
            # render console to the real stdout (reconfigured to utf-8 above).
            live_console = Console(file=sys.__stdout__)
            with contextlib.redirect_stdout(sink):
                run = cli_live.run_with_live(live_console, _worker, archive,
                                             timeout=timeout)
            answer, summary = run.result, run.summary
            if run.interrupted:
                raise Interrupted(run.abandoned)
            if run.timed_out:
                cli_events.emit("workflow_timeout",
                                data={"timeout": timeout, "enforced_by": "live"})
                raise TurnTimeout(timeout, run.abandoned, log_file)
            if run.error is not None:
                raise run.error
        else:
            import cli_live

            box = {}

            def _target():
                try:
                    box["result"] = _worker()
                except BaseException as exc:
                    box["error"] = exc
                finally:
                    box["done"] = True

            # The spinner path has no render loop to police the deadline from, so
            # join with a timeout and cancel through the same flag cli_live uses.
            cli_events.clear_cancel()
            thread = threading.Thread(target=_target, daemon=True)
            thread.start()
            timed_out = False
            interrupted = False
            try:
                with console.status(
                        f"[bold green]运行中[/bold green]  [dim]{args.mode} · "
                        f"{model_label} · 上限 {timeout}s[/dim]",
                        spinner="arc"):
                    with contextlib.redirect_stdout(sink):
                        thread.join(timeout)
                        timed_out = not box.get("done")
            except KeyboardInterrupt:
                interrupted = True
            if interrupted or timed_out:
                cli_events.request_cancel()
                console.print(
                    f"[yellow]已达 {timeout}s 上限，正在停止…[/yellow]" if timed_out
                    else "[yellow]正在停止（等待当前调用返回）…[/yellow]")
                thread.join(cli_live.CANCEL_GRACE)
            if interrupted:
                raise Interrupted(abandoned=thread.is_alive())
            if timed_out:
                cli_events.emit("workflow_timeout",
                                data={"timeout": timeout, "enforced_by": "watchdog"})
                raise TurnTimeout(timeout, abandoned=thread.is_alive(),
                                  log_file=log_file)
            if "error" in box:
                raise box["error"]
            answer = box.get("result")

        # DRAgent.execute swallows workflow exceptions and returns a stub
        # string (dr_agent.py:170-189), so a failed run would otherwise
        # exit 0 with a plausible-looking "answer" — and a recording taken
        # with --record-events would look successful when it is not.
        stub_marker = next((m for m in (
            "Note: DR workflow execution failed",
            "Note: DR workflow is not available") if m in answer), None)
        stub_reason = ""
        if stub_marker:
            # dr_agent puts the real exception text on that same line, so the
            # reason can be shown without sending anyone into the logs. Strip
            # the boilerplate around it: on screen only the cause is news.
            line = next((ln.strip() for ln in answer.split("\n")
                         if stub_marker in ln), "")
            reason = line.replace(stub_marker, "").replace(
                "Please provide background context manually.", "").strip(" .:：")
            stub_reason = (reason or stub_marker)[:300]
            cli_events.emit("workflow_stub_failure",
                            data={"marker": stub_marker, "detail": stub_reason})
    finally:
        # Leaving the flag set after a watchdog timeout would make every later
        # model call in this process raise CliCancelled instantly.
        cli_events.clear_cancel()
        captured = sink.getvalue()
        sink.close()
        if recorder_ctx is not None:
            recorder_ctx.stop()
    if captured.strip():
        logging.getLogger("InternResearch.cli.stdout").info(
            "---- 捕获的 print 输出 ----\n%s", captured)

    if record_path:
        stats = cli_events.summarize(record_path)
        console.print(
            f"[dim]事件录制: {os.path.abspath(record_path)} — "
            f"{stats['records']} 条 / {stats['distinct_events']} 种事件 / "
            f"{stats['threads']} 个并发线程 / 跨度 {stats['span_s']}s[/dim]")
        for name, n in stats["counts"]:
            console.print(f"[dim]  {n:>5}  {name}[/dim]")

    if stub_marker:
        raise RuntimeError(
            f"工作流执行失败，DRAgent 返回了兜底文本。原因: {stub_reason}\n"
            f"其他常见原因：API key 无效、模型名与密钥不匹配。完整日志: {log_file}")
    return answer, summary


def cmd_qa(args):
    from rich.console import Console
    from rich.markdown import Markdown

    err_console = Console(stderr=True)
    out_console = Console()

    agent, log_file = _init_agent(args, err_console)
    try:
        answer, _ = _ask(agent, args, args.question, err_console, log_file)
    except RuntimeError as exc:
        fail(str(exc))

    out_console.print()
    out_console.rule("[bold]回答[/bold]")
    out_console.print(Markdown(answer))
    out_console.rule()

    if args.output:
        out_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(answer)
        err_console.print(f"[dim]已写入 {out_path}[/dim]")
    err_console.print(f"[dim]日志: {log_file}[/dim]")


def cmd_chat(args):
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    err_console = Console(stderr=True)
    out_console = Console()

    agent, log_file = _init_agent(args, err_console)

    out_console.print(Panel.fit(
        "[bold]InternAgent[/bold] 交互模式\n"
        f"[dim]模型: {args.model or '(config 默认)'} · 模式: {args.mode} · 超时: {args.timeout}s[/dim]\n"
        "[dim]/verbose 显示日志 · /quiet 静默 · exit 退出[/dim]",
        border_style="cyan"))
    out_console.print(f"[dim]日志: {log_file}[/dim]")

    n = 0
    while True:
        out_console.print()
        try:
            question = out_console.input("[bold green]> [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            out_console.print("[dim]再见[/dim]")
            return
        if not question:
            continue
        low = question.lower()
        if low in ("exit", "quit", "q", "/exit", "/quit"):
            out_console.print("[dim]再见[/dim]")
            return
        if low == "/verbose":
            _set_verbose(True)
            err_console.print("[dim]日志已切换到终端[/dim]")
            continue
        if low == "/quiet":
            _set_verbose(False)
            err_console.print("[dim]日志已静默[/dim]")
            continue

        n += 1
        try:
            answer, _ = _ask(agent, args, question, err_console, log_file)
        except RuntimeError as exc:
            err_console.print(f"[yellow]{exc}[/yellow]")
            if isinstance(exc, TurnTimeout) and exc.abandoned:
                # The stuck worker still holds is_executing; without a fresh
                # DRAgent every later turn would fail with 图正在执行中.
                err_console.print("[dim]上一轮的线程还卡在调用里，正在重建工作流…[/dim]")
                agent, log_file = _init_agent(args, err_console)
            continue
        except KeyboardInterrupt:
            err_console.print("[yellow]已中断，可继续提问[/yellow]")
            continue
        out_console.print(Markdown(answer))
        out_console.print(f"[dim]（第 {n} 轮 · 继续提问或 exit 退出）[/dim]")


WIZARD_KEY_VAR = "__INTERNAGENT_WIZARD_KEY"


def _prompt_line(message, default=""):
    """prompt_toolkit when a real console is available (it handles CJK IME
    correctly on Windows); plain input() otherwise, e.g. under Git Bash."""
    if _ptk_available():
        from prompt_toolkit import prompt as pt_prompt
        while True:
            try:
                value = pt_prompt(message, default=default).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(130)
            except Exception:
                _disable_ptk()
                break
            if value:
                return value
            print("  不能为空，请重新输入", file=sys.stderr)

    shown = f"{message}[{default}] " if default else message
    while True:
        try:
            value = input(shown).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(130)
        value = value or default
        if value:
            return value
        print("  不能为空，请重新输入", file=sys.stderr)


def _prompt_optional(message, default=""):
    """A prompt where an empty answer is valid (Enter means skip)."""
    if _ptk_available():
        from prompt_toolkit import prompt as pt_prompt
        while True:
            try:
                return pt_prompt(message, default=default).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(130)
            except Exception:
                _disable_ptk()
                break
    shown = f"{message}[{default}] " if default else message
    try:
        return (input(shown).strip() or default)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(130)


def _existing_file(raw):
    """Normalise a path the user typed or pasted, or return None.

    Windows users paste paths out of Explorer with the surrounding quotes and
    sometimes a trailing backslash, and cmd marks a directory the same way it
    marks a file, so an existing *file* is the only thing accepted here.
    """
    if not raw:
        return None
    path = os.path.expanduser(raw.strip().strip('"').strip("'").rstrip("\\/"))
    return path if os.path.isfile(path) else None


# -- startup banner ------------------------------------------------------

# A hand-cut slab font, so the logo costs no dependency and no downloaded font.
# Only the glyphs the name needs are defined; an unmapped character makes
# _banner() return None and the caller falls back to the compact panel.
BANNER_FONT = {
    "I": ["###", " # ", " # ", " # ", "###"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#"],
    "T": ["#####", "..#..", "..#..", "..#..", "..#.."],
    "E": ["####", "#...", "###.", "#...", "####"],
    "R": ["####.", "#...#", "####.", "#.#..", "#..#."],
    "A": [".###.", "#...#", "#####", "#...#", "#...#"],
    "G": [".####", "#....", "#..##", "#...#", ".###."],
}
BANNER_INK = os.environ.get("CLI_BANNER_COLOR", "#4EC9B0")

# The shadow sits one cell down-right, so the gaps have to be wider than it or
# the letters fuse into one blob.
BANNER_GAP = 2
BANNER_LINE_GAP = 2


def _darker(color, factor=0.42):
    hexpart = color.lstrip("#")
    if len(hexpart) != 6:
        return "dim"
    rgb = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(max(0, int(c * factor)) for c in rgb)


def _banner(lines, avail):
    """Compose the slab logo into a single Text, one entry of `lines` per row.

    Each glyph gets a hard drop-shadow one cell down and right, which is what
    makes the block letters read as slabs rather than as flat pixels. Returns
    None if a character has no glyph or the terminal is too narrow — a wrapped
    banner is worse than no banner.
    """
    from rich.text import Text

    words = [[ch for ch in line.upper().strip()] for line in lines]
    words = [w for w in words if w]
    if not words or any(ch not in BANNER_FONT for w in words for ch in w):
        return None
    height = len(BANNER_FONT["N"])
    widths = [sum(len(BANNER_FONT[ch][0]) for ch in w)
              + BANNER_GAP * (len(w) - 1) for w in words]
    width = max(widths)
    if width + 1 > (avail or 80):
        return None
    rows = (len(words) - 1) * (height + BANNER_LINE_GAP) + height + 1

    ink = [[False] * (width + 1) for _ in range(rows)]
    row0 = 0
    for w, word_w in zip(words, widths):
        col = (width - word_w) // 2  # centre the shorter line under the longer
        for ch in w:
            glyph = BANNER_FONT[ch]
            for r, row in enumerate(glyph):
                for c, cell in enumerate(row):
                    if cell == "#":
                        ink[row0 + r][col + c] = True
            col += len(glyph[0]) + BANNER_GAP
        row0 += height + BANNER_LINE_GAP

    shadow = _darker(BANNER_INK)
    parts = []
    for r, row in enumerate(ink):
        for c, cell in enumerate(row):
            if cell:
                parts.append(("█", BANNER_INK))
            elif r and c and ink[r - 1][c - 1]:
                parts.append(("█", shadow))
            else:
                parts.append((" ", ""))
        parts.append(("\n", ""))
    return Text.assemble(*parts)


def console_width():
    """Terminal width for the banner guard. Piped output has no size to ask,
    and a wrapped banner is worse than no banner, so None means skip it."""
    try:
        return os.get_terminal_size().columns
    except (OSError, ValueError, AttributeError):
        return None


# How much of the previous answer /carry replays into the next turn. A research
# report is far longer than the planner's task field needs, and pasting all of
# it would cost real tokens on every follow-up.
CARRY_CHARS = int(os.environ.get("CLI_CARRY_CHARS", "3000"))

REPL_COMMANDS = (
    ("/help", "显示这些命令"),
    ("/think [编号 [字数]]", "列出本轮思考，或展开某条的完整推理"),
    ("/usage", "分阶段的 token 用量"),
    ("/refs [条数]", "列出上一轮引用的参考文献"),
    ("/carry [on|off]", "把上一轮结论接进下一轮（默认关：每轮都是独立研究）"),
    ("/answer", "重新打印上一轮回答（它会滚出屏幕；别名 /last）"),
    ("/save [路径]", "把上一轮回答写入文件（默认 answers/时间_问题.md）"),
    ("/retry", "重跑上一个问题（连同当时实际发出的内容）"),
    ("/verbose", "把日志同时打到终端"),
    ("/quiet", "日志只写文件"),
    ("/clear", "清空屏幕"),
    ("exit", "退出（Ctrl+D 同义）"),
)

# Substring → what the user can actually do about it. Matched against the
# lowercased exception type + message, first hit wins, so order by specificity.
_ERROR_HINTS = (
    (("invalid api key", "incorrect api key", "401", "unauthorized"),
     "API key 无效或已过期。重新运行 internagent 换一把 key。"),
    (("insufficient", "balance", "quota", "exhausted"),
     "账户余额或配额不足。到供应商控制台确认额度。"),
    (("429", "rate limit", "too many requests"),
     "触发限流。稍等再试，或换并发更低/更便宜的模型。"),
    (("403", "forbidden"),
     "这把 key 没有该模型的访问权限。换个模型名或确认账号权限。"),
    (("404", "not found", "does not exist", "unknown model"),
     "模型名不存在，或 base_url 路径不对。核对供应商给出的确切模型名。"),
    (("timeout", "timed out", "readtimeout"),
     "请求超时。网络或端点太慢，可用 --timeout 加大上限。"),
    (("connection refused", "failed to establish", "econnrefused",
      "connection error"),
     "连不上 base_url。检查地址、端口和是否需要代理。"),
    (("getaddrinfo", "name resolution", "nodename", "known host"),
     "域名解析失败。检查 base_url 拼写。"),
    (("ssl", "certificate", "tls"),
     "TLS 握手失败。端点证书或中间代理有问题。"),
    (("context length", "maximum context", "too many tokens"),
     "超出模型上下文长度。缩小问题范围或换长上下文的模型。"),
)


def _first_line(text, limit=300):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


# Characters NTFS refuses, plus "%": _write_answer() runs strftime over the
# basename, so a percent from a pasted URL or a "100% 命中率" question would
# otherwise turn into a format directive.
_BAD_NAME_CHARS = set('<>:"/\\|?*%') | {"\x00"}


def _slug(text, limit=40):
    """A filename-safe fragment of a question, CJK kept as-is.

    Illegal characters become spaces rather than vanishing, so "GNN/transformer"
    reads as two words instead of one mashed-together token. Returns "" when
    nothing usable is left; callers prepend their own timestamp, which also
    keeps a question like "CON" from being a reserved Windows device name.
    """
    cleaned = "".join(
        " " if (c in _BAD_NAME_CHARS or ord(c) < 32) else c
        for c in _first_line(text, limit * 2))
    stem = re.sub(r"\s+", " ", cleaned).strip()[:limit].rstrip(". ")
    return f"_{stem}" if stem else ""


def _explain_text(text):
    """Translate an error string into an actionable line (see _ERROR_HINTS).

    The raw message is kept underneath: a heuristic hint that turns out to be
    wrong must not cost the user the information they need to diagnose it.
    """
    low = text.lower()
    for keys, hint in _ERROR_HINTS:
        if any(k in low for k in keys):
            return f"{hint}\n[dim]原始错误: {_first_line(text, 200)}[/dim]"
    return _first_line(text, 400)


def _explain(exc):
    return _explain_text(f"{type(exc).__name__}: {exc}")


def _write_answer(path, answer, err_console):
    from datetime import datetime

    if "%" in os.path.basename(path):
        path = datetime.now().strftime(path)
    out_path = os.path.abspath(path)
    folder = os.path.dirname(out_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(answer)
    err_console.print(f"[green]✓ 已写入 {out_path}[/green]")


def _think_command(console, archive, spec, turn=None):
    """Handle `/think [编号 [字数]]` in the REPL: list, expand one, or expand
    one truncated to N characters. With no argument it lists the current turn's
    calls, which is the scope the user is asking about."""
    import cli_live
    from rich.text import Text

    if archive is None or not archive.entries:
        console.print(Text("  还没有思考记录（本轮没有产生模型推理）", style="dim"))
        return
    parts = spec.split()
    if not parts:
        entries = turn if turn else archive.entries
        if turn and len(archive.entries) > len(turn):
            console.print(Text(
                f"  本轮 {len(turn)} 条（本会话共 {len(archive.entries)} 条，"
                f"编号全局递增）", style="dim"))
        cli_live.list_thinking(console, entries, archive.path)
        return
    raw = parts[0]
    if not raw.lstrip("-").isdigit():
        console.print(Text(f"  未知编号 '{raw}'，用 /think 看列表", style="yellow"))
        return
    entry = archive.get(int(raw))
    if entry is None:
        console.print(Text(f"  没有 #{raw}，用 /think 看列表", style="yellow"))
        return
    limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    cli_live.show_thinking(console, entry, limit)


WIZARD_STEPS = 7


def _wizard_reader():
    """The key reader the wizard may use, or None for the numbered fallback.

    msvcrt's console input needs a real Win32 console, which a Git-Bash/MSYS pty
    does not give (the same probe prompt_toolkit fails against); on POSIX it
    needs termios. A piped stdin has no keys to read at all. Returning None is
    what the tests drive, so the numbered path stays covered.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    import cli_ui
    if os.name == "nt":
        return cli_ui.win_key_reader() if _is_win_console() else None
    try:
        import termios  # noqa: F401
    except ImportError:
        return None
    return cli_ui.posix_key_reader()


def _wizard_cancel(console):
    console.print("[dim]已取消，什么都没跑。[/dim]")
    sys.exit(130)


def _pick_model(console, title, ids, typed, read_key, ask=None, sub="", note=""):
    """Choose among the models the endpoint actually serves.

    Esc keeps what was typed rather than cancelling the wizard: this list is a
    convenience, and an endpoint that reports /models unfaithfully should not be
    able to block a run that would otherwise have gone ahead.
    """
    import cli_ui

    opts = [(m, "") for m in ids] + [(f"仍然用 {typed}", "保留刚才输入的名字")]
    i, cancelled = cli_ui.select(
        console, title, opts, default=len(ids), read_key=read_key, ask=ask,
        sub=sub, note=note, filterable=len(ids) > 9,
        footer="↑↓ 选择 · Enter 确认 · Esc 保留原输入")
    if cancelled or not 0 <= i < len(ids):
        return typed
    return ids[i]


def cmd_run(args):
    """Zero-flag entry point: ask for provider / base_url / model / key /
    question, then run with the live display and drop into a follow-up REPL."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text

    import cli_ui

    console = Console()
    err_console = Console(stderr=True)
    cfg = _load_user_config()
    rk = _wizard_reader()

    def _sub(i):
        return f"{i}/{WIZARD_STEPS}"

    # The numbered fallback and the free-text steps both go through the two
    # prompt helpers, so tests (and a piped stdin) keep driving the wizard
    # through the same seams they always have.
    def _ask_choice(msg, default=""):
        return _prompt_line(msg, default)

    def _ask_text(default=""):
        return _prompt_line("  › ", default)

    def _ask_skip(default=""):
        return _prompt_optional("  › ", default)

    banner = _banner(["INTERN", "AGENT"], console.width)
    if banner is not None:
        console.print()
        console.print(banner)
        console.print("[dim]自动科研助手 · API key 只存在于本进程，不会写入任何文件[/dim]")
        console.print("[dim]Ctrl+C 中断 · /think 展开思考 · /verbose 看日志 · exit 退出[/dim]")
        console.print()
    else:
        console.print(Panel.fit(
            "[bold cyan]InternAgent[/bold cyan]  自动科研助手\n"
            "[dim]按提示填几项即可开始。API key 只存在于本进程，不会写入任何文件。[/dim]\n"
            "[dim]运行时：Ctrl+C 中断 · /think 展开思考 · /verbose 看日志 · exit 退出[/dim]",
            border_style="cyan"))
    # Giving the question on the command line means "do not ask me anything
    # else": every step then resolves from a flag or the saved config, or fails
    # naming the flag to add. Interactive runs still walk all seven steps.
    scripted = bool(getattr(args, "question", None))
    if scripted and not getattr(args, "api_key_env", None):
        fail("脚本化运行需要 --api-key-env：key 不应该出现在命令行里")
    if cfg and not scripted:
        console.print(Text.assemble(
            ("上次配置  ", "dim"),
            (f"{cfg.get('provider', '?')} · {cfg.get('base_url', '?')} · "
             f"{cfg.get('model', '?')}", ""),
            ("   直接回车即沿用", "dim")))
    elif not cfg and not scripted:
        console.print(Text(
            "还没有保存的配置，需要填写 base_url 和 API key。", style="yellow"))

    names = list(PROVIDERS)
    provider = getattr(args, "provider", None) or (
        cfg.get("provider") if scripted else None)
    if provider:
        if provider not in PROVIDERS:
            fail(f"--provider 只认这些：{', '.join(names)}")
    elif scripted:
        fail("脚本化运行需要 --provider（或先在向导里存一份配置）")
    else:
        i, quit_ = cli_ui.select(
            console, "模型供应商",
            [(n, PROVIDERS[n].note) for n in names],
            default=(names.index(cfg["provider"])
                     if cfg.get("provider") in names else 0),
            read_key=rk, ask=_ask_choice, sub=_sub(1))
        if quit_:
            _wizard_cancel(console)
        provider = names[i]

    preset = PROVIDERS[provider].base
    default_base = cfg.get("base_url") if cfg.get("provider") == provider else preset
    base_url = getattr(args, "base_url", None) or (
        default_base if (scripted or getattr(args, "provider", None)) else None)
    if not base_url:
        if scripted:
            fail(f"{provider} 没有预设 base_url，脚本化运行需要 --base-url")
        base_url = cli_ui.read_value(
            console, "base_url", _ask_text, default=default_base or "",
            note="形如 https://host/v1；本机端点写 http://localhost:11434/v1",
            sub=_sub(2))

    model = getattr(args, "model", None) or (
        cfg.get("model") if scripted else None)
    if not model:
        if scripted:
            fail("脚本化运行需要 --model（或先在向导里存一份配置）")
        model = cli_ui.read_value(
            console, "模型名", _ask_text, default=cfg.get("model") or "",
            note="填该供应商真实提供的名字，连上之后会拿端点返回的列表核对",
            sub=_sub(3))

    key_env = getattr(args, "api_key_env", None)
    if key_env:
        key = os.environ.get(key_env, "").strip()
        if not key:
            fail(f"环境变量 {key_env} 为空")
        console.print(f"[dim]API key 从环境变量 {key_env} 读取，不经过交互输入[/dim]")
    else:
        key = cli_ui.read_value(
            console, "API key", lambda d="": _prompt_secret("  › "),
            note="以 • 回显、不写盘；手动输入先切到英文输入法，或直接 Ctrl+V 粘贴",
            sub=_sub(4), echo=False)
        if not key:
            clip = _read_clipboard()
            if clip and "\n" not in clip:
                console.print("[yellow]输入为空，但剪贴板里有一段内容。"
                              "是否直接用作 API key？[/yellow]")
                if _prompt_line("使用剪贴板? (y/N): ",
                                default="N").lower() in ("y", "yes"):
                    key = clip.strip()
        if key:
            cli_ui.chosen(console, "API key", f"已读入 {len(key)} 位，只留在本进程")
    if not key:
        fail("未提供 API key")

    console.print()
    with console.status("[cyan]校验 key 与 base_url…[/cyan]", spinner="dots"):
        ids, err = _probe_models(base_url, key)
    if err:
        console.print(f"[red]✗ 探测失败: {err}[/red]")
        # Guessing "key 与 base_url 不匹配" for a DNS or proxy failure sends
        # people to the wrong settings, so name the actual cause when we can.
        console.print(f"[yellow]{_explain_text(err)}[/yellow]")
        console.print("[yellow]修好后重新运行 internagent 即可。[/yellow]")
        sys.exit(1)
    cli_ui.chosen(console, "key 校验", f"通过 · 端点提供 {len(ids)} 个模型")
    if ids and model not in ids:
        if scripted:
            # Nobody is watching a scripted run to notice this warning and pick a
            # real name; failing here beats spending the tokens on a wrong model.
            fail(f"模型 '{model}' 不在该端点提供的模型里。可选：{', '.join(ids[:15])}")
        # The probe already put the real list in hand, so a mistyped name is
        # corrected here instead of only being warned about.
        model = _pick_model(console, "端点没有这个模型名", ids, model, rk,
                            _ask_choice, sub=_sub(3),
                            note=f"你输入的 {model} 不在返回列表里")

    mode = getattr(args, "mode", None)
    order = [m[0] for m in MODES]
    if not mode:
        if scripted:
            mode = cfg.get("mode") if cfg.get("mode") in order else "qa"
        else:
            i, quit_ = cli_ui.select(
                console, "运行模式", list(MODES),
                default=(order.index(cfg["mode"])
                         if cfg.get("mode") in order else 0),
                read_key=rk, ask=_ask_choice, sub=_sub(5),
                note="qa 已关掉需要企业 key 的 volc_search，simple/complex 还开着它")
            if quit_:
                _wizard_cancel(console)
            mode = MODES[i][0]

    def _show_attach(found):
        # The name on the ✓ line, the full path underneath: the name is what you
        # recognise, the path is what you check, and cutting either one to fit a
        # column defeats the purpose.
        console.print(Text.assemble((f"{cli_ui.CHECK} ", cli_ui.INK),
                                    ("本地文件  ", "dim"),
                                    (os.path.basename(found), "bold")))
        console.print(f"[dim]  {os.path.dirname(found)}[/dim]")

    attach = ""
    if getattr(args, "file", None):
        found = _existing_file(args.file)
        if not found:
            fail(f"--file 指定的文件不存在：{args.file}")
        attach = found
        _show_attach(found)
    elif not scripted:
        while True:
            raw = cli_ui.read_value(
                console, "本地文件（可选）", _ask_skip,
                note="给这轮调研喂原始材料：论文 / 数据表 / 需求文档的路径，回车跳过",
                sub=_sub(6), echo=False)
            if not raw:
                break
            found = _existing_file(raw)
            if found:
                attach = found
                _show_attach(found)
                break
            console.print(f"[yellow]  没有找到这个文件：{raw[:60]}"
                          f"（再试一次，回车跳过）[/yellow]")
    if attach:
        console.print("[dim]  本轮及后续追问都会带上它[/dim]")

    # Same precedence as the other steps: flag, then what the wizard saved, then
    # 600. --timeout used to be written to the config but never read back, so a
    # run that once needed 900s quietly fell back to the default the next day.
    raw_timeout = (getattr(args, "timeout", None) or cfg.get("timeout") or 600)
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        console.print(f"[yellow]timeout={raw_timeout!r} 不是数字，按 600 处理[/yellow]")
        timeout = 600
    if timeout < 1:
        # 0 or negative would make Thread.join() wait forever instead of timing
        # out, i.e. exactly the behaviour this flag exists to guarantee.
        fail("--timeout 需要是正整数秒")

    # Route the key through collect_credentials()' existing env-var path so the
    # wizard reuses the tested publish()/_init_agent() plumbing untouched.
    ns = argparse.Namespace(
        provider=provider, base_url=base_url, model=model, mode=mode,
        api_key_env=WIZARD_KEY_VAR, prompt_key=False, file=attach or None,
        output=None,
        verbose=getattr(args, "verbose", False),
        timeout=timeout,
        max_workers=getattr(args, "max_workers", None),
        max_tool_calls=getattr(args, "max_tool_calls", None),
        max_layers=getattr(args, "max_layers", None),
        record_events=getattr(args, "record_events", None))

    if not scripted:
        # One card that says what is about to run, because a deep-research turn
        # costs real tokens and the three multipliers are easy to forget. The mode
        # line is just the name — its description is on the ✓ step above, and a
        # value this long is the one that gets cut at 60 columns.
        while True:
            cli_ui.card(
                console, "准备开始",
                [("供应商", f"{provider} · {base_url}"),
                 ("模型", model),
                 ("模式", mode),
                 ("预算", _budget_summary(mode, ns, compact=True)),
                 ("单轮上限", f"{timeout}s"),
                 ("附件", os.path.basename(attach) if attach else "无")],
                footer="Enter 开始 · m 换一个模型 · q 退出")
            raw = (_ask_skip() or "").strip().lower()
            if raw in ("q", "quit", "exit"):
                _wizard_cancel(console)
            if raw in ("", "y", "yes"):
                break
            if raw == "m":
                if not ids:
                    console.print("[yellow]  端点没有返回模型列表，列不出可选项"
                                  "（重新跑一遍第 3 步可以手输）[/yellow]")
                else:
                    model = _pick_model(console, "换一个模型", ids, model, rk,
                                        _ask_choice, note=f"当前用的是 {model}")
                ns.model = model
                continue
            console.print(f"[yellow]  不认识 {raw}：回车开始，m 换模型，q 退出"
                          f"（Ctrl+C 取消）[/yellow]")

    saved = _save_user_config({
        "provider": provider, "base_url": base_url, "model": model,
        "mode": mode, "timeout": timeout})
    if saved:
        console.print(f"[dim]已记住供应商/base_url/模型到 {CONFIG_PATH}（不含 key）[/dim]")

    os.environ[WIZARD_KEY_VAR] = key
    console.print()
    agent, log_file = _init_agent(ns, err_console)
    err_console.print(f"[dim]日志: {log_file}[/dim]")
    err_console.print(f"[dim]每轮上限 {timeout}s，到点自动停止并重建工作流"
                      f"（--timeout 调整）[/dim]")
    err_console.print(f"[dim]成本闸门 {_budget_summary(ns.mode, ns)}[/dim]")

    import cli_live
    from datetime import datetime as _dt
    archive = cli_live.ThinkArchive(
        os.path.join(REPO, "logs", f"think_{_dt.now():%Y-%m-%d}.md"))
    err_console.print(
        f"[dim]思考记录: {archive.path} · 会话内用 /think 编号 展开[/dim]")

    st = {"turn": 0, "question": None, "answer": None, "thinks": [],
          "refs": [], "session": None, "carry": False, "payload": None}

    def _carry_payload(text):
        """The task string actually sent, with /carry's recap prepended.

        Workflow.execute rebuilds the graph per call (dr_agent.py:179), so a
        follow-up like "第二点再展开" otherwise reaches the planner with nothing
        tying it to the answer it is reacting to. Returns (payload, carried).
        """
        if not st["carry"] or not st["answer"]:
            return text, 0
        recap = st["answer"].strip()
        if len(recap) > CARRY_CHARS:
            recap = recap[:CARRY_CHARS].rstrip() + "…"
        return ("【上一轮结论 · 仅作背景，不要重复回答】\n" + recap +
                "\n\n【本轮问题】\n" + text), len(recap)

    def _turn(text, payload=None):
        """Ask one question, then render the answer and its totals.

        payload defaults to whatever /carry says should go with this question.
        /retry passes the payload it sent last time: st["answer"] has moved on by
        then, so recomposing would quietly attach the retried turn's own answer
        as its background.
        """
        nonlocal agent, log_file
        st["question"] = text
        # The answer arrives minutes later and the live region clears itself, so
        # without this the scrollback has no record of which question a report
        # answered — and with /carry the text actually sent is not the question.
        console.print(Text.assemble(("问 ", "bold green"),
                                    (_first_line(text, 160), "dim")))
        if payload is None:
            payload, carried = _carry_payload(text)
            if carried:
                console.print(f"[dim]已带上上一轮结论 {carried} 字[/dim]")
        st["payload"] = payload
        seen = len(archive.entries)
        try:
            answer, summary = _ask(agent, ns, payload, console, log_file,
                                   live=True, archive=archive)
        except (Interrupted, TurnTimeout) as exc:
            # An interrupt is when the partial reasoning matters most — the user
            # cut in because the direction looked wrong. A timeout is the same
            # story: whatever the model got through before we stopped it is the
            # evidence for why the run was cut short.
            st["thinks"] = archive.entries[seen:]
            if isinstance(exc, TurnTimeout):
                # cli_live's own line is a status ("已达上限，正在停止"), which
                # disappears; this is the one that says what to do next.
                err_console.print(f"[yellow]{exc}[/yellow]")
            if exc.abandoned:
                # Say it plainly as well as in the spinner: the status line
                # disappears when it clears, and "why did the next question
                # take a few seconds to start?" is otherwise unanswerable.
                err_console.print(
                    "[dim]上一轮的工作线程还卡在模型调用里，正在重建工作流…[/dim]")
                with console.status("[cyan]重建工作流…[/cyan]", spinner="dots"):
                    agent, log_file = _init_agent(ns, err_console)
                err_console.print("[dim]已重建，可以继续提问。日志: %s[/dim]" % log_file)
            else:
                err_console.print(
                    "[dim]可继续提问，要跑久一点就加大 --timeout[/dim]"
                    if isinstance(exc, TurnTimeout)
                    else "[dim]已中断，可继续提问[/dim]")
            if st["thinks"]:
                err_console.print(
                    f"[dim]本轮已留下 {len(st['thinks'])} 条思考，/think 查看[/dim]")
            return
        except KeyboardInterrupt:
            st["thinks"] = archive.entries[seen:]
            err_console.print("[yellow]已中断，可继续提问[/yellow]")
            return
        except Exception as exc:  # a failed turn must not kill the REPL
            st["thinks"] = archive.entries[seen:]
            err_console.print(f"[yellow]{_explain(exc)}[/yellow]")
            err_console.print(f"[dim]完整日志: {log_file}[/dim]")
            return

        st["answer"] = answer
        st["thinks"] = archive.entries[seen:]
        st["refs"] = (summary or {}).get("references") or []
        st["session"] = cli_live.merge_usage(st["session"], summary)
        st["turn"] += 1
        console.print()
        console.rule("[bold]回答[/bold]")
        console.print(Markdown(answer))
        cli_live.print_summary(console, summary, len(st["thinks"]))
        if ns.output:
            _write_answer(ns.output, answer, err_console)

    def _cmd_help(rest):
        for name, desc in REPL_COMMANDS:
            console.print(Text.assemble(("  " + cli_live.pad_cell(name, 24),
                                         "cyan"), (desc, "dim")))

    def _cmd_think(rest):
        _think_command(console, archive, rest, st["thinks"])

    def _cmd_usage(rest):
        session = st["session"] or {}
        cli_live.print_usage(console, session.get("usage_by_stage") or {},
                             session.get("usage") or {},
                             session.get("usage_seen"))

    def _cmd_refs(rest):
        limit = 0
        if rest:
            try:
                limit = int(rest.split()[0])
            except ValueError:
                console.print("[yellow]数字表示只列前几条：/refs 10[/yellow]")
                return
        cli_live.print_references(console, st["refs"], limit)

    def _cmd_answer(rest):
        # A long run scrolls the answer off the top; re-printing it beats
        # scrolling, and beats re-asking (which costs another turn).
        if not st["answer"]:
            console.print("[yellow]还没有可回看的回答[/yellow]")
            return
        console.rule("[bold]上一轮回答[/bold]")
        console.print(Markdown(st["answer"]))
        console.rule()

    def _cmd_save(rest):
        if not st["answer"]:
            console.print("[yellow]还没有可保存的回答[/yellow]")
            return
        # A bare timestamp sorts uselessly once you have saved a dozen answers;
        # the question is the only label the user will recognise later.
        _write_answer(rest or os.path.join(
            "answers",
            f"answer_%Y%m%d-%H%M%S{_slug(st['question'] or '')}.md"),
            st["answer"], err_console)

    def _cmd_retry(rest):
        if not st["question"]:
            console.print("[yellow]还没有可重跑的问题[/yellow]")
            return
        console.print(f"[dim]重跑：{st['question'][:80]}[/dim]")
        _turn(st["question"], st["payload"])

    def _cmd_clear(rest):
        console.clear()

    def _cmd_carry(rest):
        state = rest.strip().lower()
        if state in ("on", "开", "true", "1"):
            st["carry"] = True
        elif state in ("off", "关", "false", "0"):
            st["carry"] = False
        elif state:
            console.print("[yellow]只认 /carry、/carry on、/carry off[/yellow]")
            return
        else:
            st["carry"] = not st["carry"]
        if not st["carry"]:
            console.print("[dim]下一轮起只发新问题（每轮独立研究）[/dim]")
            return
        if not st["answer"]:
            console.print("[dim]已开启 · 还没有结论可带，从第一轮回答之后生效[/dim]")
            return
        console.print(
            f"[dim]已开启 · 下一个问题会带上上一轮结论的前 "
            f"{min(len(st['answer'].strip()), CARRY_CHARS)} 字"
            f"（上限 {CARRY_CHARS}，CLI_CARRY_CHARS 可调）[/dim]")

    COMMANDS = {
        "/help": _cmd_help, "/?": _cmd_help,
        "/think": _cmd_think, "/usage": _cmd_usage, "/save": _cmd_save,
        "/refs": _cmd_refs, "/carry": _cmd_carry,
        "/answer": _cmd_answer, "/last": _cmd_answer,
        "/retry": _cmd_retry, "/clear": _cmd_clear,
        "/verbose": lambda rest: (_set_verbose(True),
                                  err_console.print("[dim]日志已切换到终端[/dim]")),
        "/quiet": lambda rest: (_set_verbose(False),
                                err_console.print("[dim]日志已静默[/dim]")),
    }

    scripted = getattr(args, "question", None)
    def _farewell():
        """One line on what the session cost and where its reasoning went."""
        console.print()
        session = st["session"] or {}
        bits = [f"{st['turn']} 轮"]
        total = ((session.get("usage") or {}).get("total")) or 0
        if total:
            bits.append(f"共 {total} tokens")
        console.print(f"[dim]本次会话 {' · '.join(bits)} · 思考全文 {archive.path}[/dim]")
        console.print("[dim]再见[/dim]")

    while True:
        console.print()
        if scripted:
            # `run "问题"` — the positional replaces step ⑦, then the REPL is
            # still there so a terminal run can keep asking.
            question = scripted
            scripted = None
        elif st["turn"] == 0 and st["question"] is None:
            try:
                question = cli_ui.read_value(
                    console, "研究问题", _ask_text,
                    note="一句话说清要查什么；之后的追问每轮都会重新规划一次研究",
                    sub=_sub(7), echo=False)
            except SystemExit:
                return
        else:
            try:
                question = console.input("[bold green]> [/bold green]").strip()
            except (EOFError, KeyboardInterrupt):
                _farewell()
                return
            if not question:
                continue
            low = question.lower()
            if low in ("exit", "quit", "q", "/exit", "/quit"):
                _farewell()
                return
            if low.startswith("/"):
                cmd, _, rest = question.partition(" ")
                handler = COMMANDS.get(cmd.lower())
                if handler is None:
                    console.print(
                        f"[yellow]未知命令 {cmd} · /help 查看可用命令[/yellow]")
                    continue
                handler(rest.strip())
                continue

        _turn(question)
        hint = f" · /think 看 {len(st['thinks'])} 条思考" if st["thinks"] else ""
        console.print(f"[dim]（第 {st['turn']} 轮 · 继续提问{hint} · /help · exit 退出）[/dim]")
        if st["turn"] == 1 and st["answer"] and not st["carry"]:
            console.print(
                "[dim]每一轮都会重新规划一次研究，问题之间不带记忆。想让下一轮接着这轮的"
                "结论走，输入 /carry[/dim]")


def cmd_replay(args):
    """Re-render a recording made with --record-events through the same view a
    live run uses. No API cost, so a run's reasoning can be re-read afterwards
    or the display re-tuned against real event shapes."""
    from rich.console import Console

    import cli_events
    import cli_live

    if not os.path.isfile(args.path):
        fail(f"找不到录制文件: {args.path}")
    stats = cli_events.summarize(args.path)
    console = Console()
    console.print(
        f"[dim]回放 {os.path.abspath(args.path)} — {stats['records']} 条事件 · "
        f"{stats['distinct_events']} 种 · {stats['threads']} 个并发线程 · "
        f"跨度 {stats['span_s']}s[/dim]")
    for name, n in stats["counts"]:
        console.print(f"[dim]  {n:>5}  {name}[/dim]")
    cli_live.replay_file(console, args.path, speed=args.speed)


def cmd_proxy(args):
    base_url, key = resolve_base(args)[0], collect_credentials(args)[0]
    litellm_prefix = PROVIDERS[args.provider].prefix
    upstream = f"{litellm_prefix}/{args.upstream_model}"

    import tempfile

    cfg = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    entry = f"""      model: {upstream}
      api_key: os.environ/UPSTREAM_API_KEY
      api_base: os.environ/UPSTREAM_API_BASE"""
    cfg.write(
        "model_list:\n"
        f"  - model_name: {args.alias}\n    litellm_params:\n{entry}\n"
        f"  - model_name: '*'\n    litellm_params:\n{entry}\n"
        "litellm_settings:\n  drop_params: true\n  num_retries: 2\n  request_timeout: 600\n"
        "general_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\n"
    )
    cfg.close()

    os.environ["UPSTREAM_API_KEY"] = key
    os.environ["UPSTREAM_API_BASE"] = base_url.rstrip("/").removesuffix("/v1")
    os.environ.setdefault("LITELLM_MASTER_KEY", "sk-local-proxy-only")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

    print(f"Claude Code 请求模型名 '{args.alias}' -> {upstream}")
    print(f"另请设置 ANTHROPIC_BASE_URL=http://127.0.0.1:{args.port} "
          f"和 ANTHROPIC_API_KEY={os.environ['LITELLM_MASTER_KEY']}\n")
    os.execv(LLPROXY_PY, [LLPROXY_PY, "-m", "litellm", "--config", cfg.name,
                          "--host", "127.0.0.1", "--port", str(args.port)])


def _encoding_probe(stream):
    """Can this stream actually carry the glyphs the interface draws?

    Encoding the real characters beats matching codec names: a cp936 console
    holds Chinese fine and still dies on the ✓ and ░ the display uses, so a
    name allowlist would call a crashing setup healthy.
    """
    enc = getattr(stream, "encoding", None) or "utf-8"
    sample = "调研答案 ✓ ✗ ⚠ ◆ ● │ ░ █"
    try:
        sample.encode(enc)
        return True, enc, ""
    except (UnicodeEncodeError, LookupError) as exc:
        return False, enc, _first_line(str(exc), 60)


def cmd_doctor(args):
    """Pre-flight for the CLI itself: what the interface needs, what the
    workflow imports, whether the key promise holds, and what to do about each
    gap. No network, no key, nothing written beyond a probe file."""
    import importlib.metadata
    import importlib.util
    import shutil
    import subprocess
    from rich.console import Console
    from rich.text import Text

    import cli_live

    console = Console()
    blockers = []
    width = 18

    def row(state, name, detail, hint=None):
        symbol = {"ok": "✓", "warn": "⚠", "bad": "✗"}[state]
        style = {"ok": "green", "warn": "yellow", "bad": "red"}[state]
        console.print(Text.assemble(
            (f"  {symbol} ", style),
            (cli_live.pad_cell(name, width), ""),
            (detail, "dim" if state == "ok" else style)))
        if hint and state != "ok":
            console.print(Text.assemble(
                ("      " + cli_live.pad_cell("", width), "dim"), (hint, "dim")))

    console.print("\n[bold]① 运行环境[/bold]")
    ok, enc, bad = _encoding_probe(sys.stdout)
    if ok:
        row("ok", "输出编码", enc)
    else:
        row("bad", "输出编码", f"{enc} 放不下界面字符：{bad}",
            "set PYTHONIOENCODING=utf-8（chcp 65001 亦可）；"
            "launcher 脚本已经设过，直接用 internagent 启动就没这问题")
        blockers.append("输出编码")
    row("ok" if sys.stdin.isatty() else "warn", "终端",
        "交互" if sys.stdin.isatty() else "非交互（管道/脚本）")
    banner = _banner(["INTERN", "AGENT"], console.width)
    row("ok", "控制台宽度", f"{console.width} 列 · "
        + ("logo 完整显示" if banner is not None else "窄于 logo，改用紧凑面板"))

    console.print("\n[bold]② 界面依赖[/bold]")
    for mod, label, why in (("rich", "rich", "整个显示层"),
                            ("prompt_toolkit", "prompt_toolkit",
                             "中文输入法下的行编辑")):
        if importlib.util.find_spec(mod) is None:
            row("bad", label, "缺失", f"pip install {mod}（缺少会影响：{why}）")
            blockers.append(mod)
        else:
            extra = ""
            if mod == "prompt_toolkit" and not _ptk_available():
                extra = "，但当前终端无 Win32 控制台缓冲区，改用 input()"
            row("ok", label, f"已装{extra}")
    if _is_win_console() and not _ptk_available():
        console.print("  [dim]提示：Windows 中文输入需要 prompt_toolkit，"
                      "或在英文输入法下粘贴 key。[/dim]")

    console.print("\n[bold]③ 工作流依赖（qa）[/bold]")
    # Check installed *distributions* against the file that declares them, so a
    # missing pin and a missing module are not conflated — and so the vendored
    # camel copy (internagent/mas/agents/dr_agents/camel) is not demanded of pip.
    reqs = _read_requirements("requirements-qa-windows.txt")
    missing, present = [], 0
    for pkg in reqs:
        try:
            importlib.metadata.version(pkg)
            present += 1
        except importlib.metadata.PackageNotFoundError:
            missing.append(pkg)
    if not missing and reqs:
        row("ok", "requirements", f"{present} 个包全部就位")
    else:
        for pkg in missing:
            row("bad", pkg, "未安装")
            blockers.append(pkg)
        if reqs and missing:
            console.print("      [dim]pip install -r "
                          f"{os.path.join(REPO, 'requirements-qa-windows.txt')}"
                          "[/dim]")
        if not reqs:
            row("warn", "requirements", "读不到 requirements-qa-windows.txt")
    try:
        spec = importlib.util.find_spec("internagent.mas.agents.dr_agent")
        row("ok" if spec else "bad", "dr_agent", "找到" if spec else "找不到",
            f"需在 {REPO} 下运行，或把该目录加入 PYTHONPATH")
        if not spec:
            blockers.append("internagent 包")
    except (ImportError, ValueError) as exc:
        row("bad", "dr_agent", f"导入失败：{_first_line(str(exc), 80)}",
            "多为缺依赖；先看上面的缺失项")
        blockers.append("internagent 包")

    console.print("\n[bold]④ 可写位置[/bold]")
    logs = os.path.join(REPO, "logs")
    probe = os.path.join(logs, ".write-test")
    try:
        os.makedirs(logs, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        row("ok", "logs/", f"思考归档写入 logs/think_<日期>.md")
    except OSError as exc:
        row("bad", "logs/", f"不可写：{_first_line(str(exc), 80)}",
            "/think 仍可在工作期内展开，只是关不掉后回看")

    console.print("\n[bold]⑤ 配置与密钥[/bold]")
    cfg = _load_user_config()
    if cfg:
        row("ok", "已记住", " · ".join(f"{k}={cfg[k]}" for k in CONFIG_KEYS
                                       if cfg.get(k) is not None))
        console.print(f"      [dim]{CONFIG_PATH}[/dim]")
    else:
        row("warn", "已记住", "无配置", "首次运行 internagent 向导时会写一份")
    leaked = _config_holds_secret()
    if leaked:
        row("bad", "密钥落盘", f"{CONFIG_PATH} 含疑似密钥字段：{leaked}",
            "删掉该文件重跑向导；确认没有把 key 写进 .env")
        blockers.append("key 泄漏到配置文件")
    else:
        row("ok", "密钥落盘", "无（key 只存在于进程环境变量，退出即消失）")
    leaks = _audit_log_secrets(logs_dir=logs)
    if leaks:
        row("bad", "日志审计", f"{len(leaks)} 处疑似密钥，多半来自加脱敏之前的旧日志",
            "附到 issue 前先删掉：" + "、".join(sorted({os.path.basename(p)
                                                  for p, _n, _s in leaks})))
        for path, line_no, snippet in leaks[:3]:
            console.print(f"      [dim]{os.path.basename(path)}:{line_no}"
                          f"  {snippet}[/dim]")
    else:
        row("ok", "日志审计", "logs/ 里没有密钥形状的内容")

    console.print("\n[bold]⑥ 命令行入口[/bold]")
    launcher = shutil.which("internagent")
    if launcher:
        row("ok", "internagent", launcher)
    else:
        row("warn", "internagent", "不在 PATH",
            f"直接用 python {os.path.join(REPO, 'internagent_cli.py')}，"
            "或按 README 建一个 wrapper")
    for exe in ("claude", "openhands", "iflow"):
        path = shutil.which(exe)
        row("ok" if path else "warn", exe, path or "不在 PATH（仅 discovery 需要）")
    try:
        out = subprocess.run(
            [LLPROXY_PY, "-c",
             "from importlib.metadata import version;print(version('litellm'))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        ver = out.stdout.strip().splitlines()[-1] if out.returncode == 0 else ""
        row("ok" if ver else "warn", "litellm",
            (ver + "（llproxy 环境）") if ver else f"{LLPROXY_PY} 里没有",
            "仅 internagent proxy 子命令需要")
    except OSError:
        row("warn", "litellm", f"找不到解释器 {LLPROXY_PY}",
            "仅 internagent proxy 子命令需要")

    console.print("\n[bold]⑦ discovery 依赖（可选）[/bold]")
    for name in ("torch", "transformers", "rdkit"):
        found = importlib.util.find_spec(name) is not None
        row("ok" if found else "warn", name, "已装" if found else "缺失",
            "qa 模式不需要，只有 discovery/科研发现流程用")
    row("ok" if os.path.exists(os.path.join(REPO, ".env")) else "warn",
        ".env", "存在" if os.path.exists(os.path.join(REPO, ".env")) else "缺失",
        "只有从文件读 key 的工作流需要；CLI 走运行时输入")
    row("ok" if os.path.isdir(PROXY_DIR) else "warn", "proxy 目录",
        PROXY_DIR if os.path.isdir(PROXY_DIR) else f"{PROXY_DIR} 不存在",
        "仅 claudecode 代理实验需要")

    console.print("\n[bold]⑧ 上游本地补丁[/bold]")
    # Upstream hardcodes OpenAI-hosted model names and has one unbounded layer
    # loop. The fixes live in the working tree, so a `git pull`/`git checkout`
    # can wipe them without any error at runtime — that is how this file's own
    # 401-loop regression happened twice. Say it out loud instead.
    missing = []
    for rel, marker, breaks in LOCAL_PATCHES:
        path = os.path.join(REPO, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if marker not in fh.read():
                    missing.append((rel, breaks))
        except OSError:
            missing.append((rel, "文件不存在"))
    if missing:
        row("warn", "本地补丁", f"{len(LOCAL_PATCHES) - len(missing)}"
            f"/{len(LOCAL_PATCHES)} 在位",
            "被 git pull 冲掉了；docs/cli.md 的“本地补丁”一节有每一处的改法")
        for rel, breaks in missing:
            console.print(Text.assemble(("      ✗ ", "yellow"),
                                        (f"{rel} — {breaks}", "dim")))
    else:
        row("ok", "本地补丁", f"{len(LOCAL_PATCHES)}/{len(LOCAL_PATCHES)} 在位")

    if getattr(args, "selftest", False):
        console.print("\n[bold]⑨ 离线界面自检[/bold]")
        runner = os.path.join(REPO, "tests", "cli", "run_all.py")
        if not os.path.isfile(runner):
            row("warn", "自检", f"找不到 {runner}")
        else:
            proc = subprocess.run([sys.executable, runner],
                                  env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=600)
            lines = [l for l in (proc.stdout or "").split("\n") if l.strip()]
            for line in lines[-10:]:
                console.print("  " + line)
            done = lines[-1] if lines else ""
            row("ok" if proc.returncode == 0 else "bad", "自检",
                done or f"退出码 {proc.returncode}")
            if proc.returncode:
                blockers.append("离线自检失败")
                console.print("  [dim]完整输出：python tests/cli/run_all.py -v"
                              "[/dim]")
            console.print("  [dim]自检不联网、不花 API 额度[/dim]")

    console.print()
    if blockers:
        console.print(Text.assemble(("阻塞项 ", "bold red"),
                                    (" · ".join(blockers), "red")))
        console.print("[dim]逐项解决后重跑 internagent doctor[/dim]")
        sys.exit(1)
    console.print("[green]✓ 界面与 qa 流程就绪[/green] "
                  "[dim]还差一把 API key —— 运行时输入即可，不会被保存[/dim]")


def _read_requirements(name):
    """Distribution names from a requirements file, pins dropped. Comments and
    option lines (-r / --extra-index-url) are skipped so a nested requirement
    cannot masquerade as a package."""
    path = os.path.join(REPO, name)
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(("#", "-", "http")):
                    continue
                line = line.split("#", 1)[0].strip()
                for sep in ("==", ">=", "<=", "~=", "!=", "@", " "):
                    if sep in line:
                        line = line.split(sep, 1)[0]
                        break
                if line:
                    out.append(line)
    except OSError:
        return []
    return out


def _config_holds_secret():
    """Field names in the saved config that must never be there. The CLI's
    promise is that a key lives only in this process, and a doctor that only
    checks CONFIG_KEYS' values would pass on a hand-edited file."""
    import json
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return sorted(k for k in data
                  if any(w in k.lower() for w in
                         ("key", "token", "secret", "password"))
                  or str(data[k]).startswith(("sk-", "Bearer ")))


def _audit_log_secrets(logs_dir=None):
    """Look for key material in what has already been written to logs/.

    Redaction sits at the event boundary now, but a folder of logs from an
    older checkout stays on disk, and someone about to attach `logs/` to a
    GitHub issue is exactly the person who needs to be told. Snippets are
    masked before they are printed, so this check cannot leak the key itself.
    """
    import cli_events

    logs_dir = logs_dir or os.path.join(REPO, "logs")
    hits = []
    scanned = 0
    try:
        walk = list(os.walk(logs_dir))
    except OSError:
        return hits
    for root, _dirs, files in walk:
        for name in sorted(files):
            if scanned >= 60:
                return hits
            if not name.endswith((".log", ".md", ".jsonl", ".txt")):
                continue
            path = os.path.join(root, name)
            scanned += 1
            try:
                if os.path.getsize(path) > 32 * 1024 * 1024:
                    continue
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if cli_events.secret_matches(line):
                            hits.append((path, n,
                                         cli_events.redact(_first_line(line, 70))))
                            if len(hits) >= 5:
                                return hits
            except OSError:
                continue
    return hits


def _pos_int(raw):
    """argparse type: a count of things is never zero or negative."""
    try:
        n = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"需要整数，收到 {raw!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"需要 ≥1 的整数，收到 {n}")
    return n


def main():
    p = argparse.ArgumentParser(
        prog="internagent",
        description="InternAgent CLI（密钥运行时输入，不落盘）。"
                    "不带任何参数即进入交互式向导。")
    sub = p.add_subparsers(dest="cmd")

    def add_creds(sp):
        sp.add_argument("--provider", choices=list(PROVIDERS), default="custom",
                        help="上游供应商预设，决定 base_url")
        sp.add_argument("--base-url", help="覆盖预设的 base_url")
        sp.add_argument("--api-key-env", help="从该环境变量读 key，而不是交互输入")
        sp.add_argument("--prompt-key", action="store_true", help="即使环境变量已有值也强制交互输入")

    def add_budget(sp):
        # 成本闸门：三个上限相乘就是一道题最多花多少次模型调用。个人 key 通常有
        # 限流，config 里的默认值是按服务端吞吐定的，不代表你的额度扛得住。
        sp.add_argument("--max-workers", type=_pos_int, metavar="N",
                       help="同时执行的节点数上限（默认取 config 的 10）；"
                            "被限流/429 时调小")
        sp.add_argument("--max-tool-calls", type=_pos_int, metavar="N",
                       help="每个子任务最多问模型几轮工具（默认取 config 的 5）；"
                            "怀疑工具在反复调用时用它止损")
        sp.add_argument("--max-layers", type=_pos_int, metavar="N",
                       help="图层数上限，即 config 的 main.max_iter（qa 默认 5）")

    s = sub.add_parser("check", help="验证 key 并列出该供应商提供的模型")
    add_creds(s)
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("qa", help="深度调研问答（当前已验证可用的模式）")
    add_creds(s)
    s.add_argument("question")
    s.add_argument("--model", help="模型名，需供应商真实提供；缺省用 config_qa.yaml 的值")
    s.add_argument("--mode", choices=["qa", "simple", "complex"], default="qa")
    s.add_argument("--file", help="附带文件")
    s.add_argument("-o", "--output", help="把答案写入文件")
    s.add_argument("--verbose", "-v", action="store_true",
                   help="在终端显示详细日志（默认只写文件）")
    s.add_argument("--timeout", type=int, default=300,
                   help="执行超时秒数（默认 300），防止无效 key 导致无限重试")
    s.add_argument("--record-events", metavar="PATH",
                   help="把工作流事件流录制为 JSONL（供离线开发进度界面用，追加写入）")
    add_budget(s)
    s.set_defaults(func=cmd_qa)

    s = sub.add_parser("chat", help="交互式多轮对话（Claude Code 风格，初始化一次反复提问）")
    add_creds(s)
    s.add_argument("--model", help="模型名，需供应商真实提供；缺省用 config_qa.yaml 的值")
    s.add_argument("--mode", choices=["qa", "simple", "complex"], default="qa")
    s.add_argument("--file", help="每轮附带文件")
    s.add_argument("--verbose", "-v", action="store_true",
                   help="启动时即显示详细日志（会话内可用 /verbose、/quiet 切换）")
    s.add_argument("--timeout", type=int, default=300,
                   help="每轮执行超时秒数（默认 300）")
    s.add_argument("--record-events", metavar="PATH",
                   help="把工作流事件流录制为 JSONL（每轮追加，供离线开发进度界面用）")
    add_budget(s)
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("proxy", help="启动 LiteLLM 代理，供 claudecode 实验后端使用")
    add_creds(s)
    s.add_argument("--upstream-model", required=True, help="真实上游模型名，如 deepseek-chat")
    s.add_argument("--alias", default="claude-sonnet-4-5-20250929",
                   help="Claude Code 请求时用的名字，默认对齐 InternAgent 的硬编码值")
    s.add_argument("--port", type=int, default=4000)
    s.set_defaults(func=cmd_proxy)

    s = sub.add_parser("doctor", help="检查依赖、CLI、配置是否就位")
    s.add_argument("--selftest", action="store_true",
                   help="顺带跑完 tests/cli 的离线界面自检（不联网、不花额度）")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("run", help="交互式向导（等价于直接输入 internagent）；"
                                   "给了哪一步的参数就跳过哪一步")
    s.add_argument("question", nargs="?",
                   help="第⑦步的问题。给了就不提问，答完仍进入 REPL 继续追问")
    s.add_argument("--provider", help="第①步；须是列表里的名字，配合 --base-url 可省")
    s.add_argument("--base-url", help="第②步；不给时用该供应商的预设（或配置里同供应商的地址）")
    s.add_argument("--model", help="第③步")
    s.add_argument("--mode", choices=["qa", "simple", "complex"], help="第⑤步")
    s.add_argument("--api-key-env", help="第④步：从该环境变量读 key，跳过交互输入（供脚本化运行）")
    s.add_argument("--file", help="第⑥步：附件文件路径，给了即跳过该步（不再作为默认值重问）")
    s.add_argument("--verbose", "-v", action="store_true",
                   help="启动即显示详细日志（会话内可用 /verbose、/quiet 切换）")
    s.add_argument("--timeout", type=int,
                   help="每轮执行的墙钟上限秒数（不给则用上次记住的值，再没有就 600）。"
                        "到点会请求取消并重建工作流，一轮不会无限跑下去")
    s.add_argument("--record-events", metavar="PATH",
                   help="把工作流事件流录制为 JSONL（供离线开发进度界面用）")
    add_budget(s)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("replay",
                       help="回放 --record-events 录下的事件流（不花 API）")
    s.add_argument("path", help="JSONL 录制文件")
    s.add_argument("--speed", type=float, default=1.0,
                   help="回放速度倍率；0 表示尽快（默认 1.0，即真实节奏）")
    s.set_defaults(func=cmd_replay)

    args = p.parse_args()
    if args.cmd is None:
        # bare `internagent` with no subcommand enters the wizard
        args = p.parse_args(["run"])
    args.func(args)


if __name__ == "__main__":
    main()

"""Offline checks that no API key can reach a sink, and that thinking survives
the recorder boundary. Runs with no key and no network.

Two invariants:
  * every string that leaves cli_events is redacted before it is truncated, so
    neither logs/think_*.md nor the --record-events JSONL can persist a key;
  * a model's chain of thought is *not* cut to MAX_STR, because reading it in
    full is the reason /think and the think archive exist.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # dr_agents fixture package
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import atexit
import io
import json
import logging
import logging.handlers
import shutil
import tempfile
import time

import cli_events

FAILS = []


def check(cond, msg, extra=""):
    if cond:
        print(f"  ok  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL {msg}" + ("" if not extra else f"  [{extra}]"))


TMP = tempfile.mkdtemp(prefix="clisecrets_")
atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))

SECRET = "sk-abcdef1234567890ABCDEF"
MASKED = "sk-***"

# ---------------------------------------------------------------- redact()
print("\n[redact]")

check(SECRET not in cli_events.redact(f"invalid api_key: {SECRET}"),
      "api_key: <key> 被遮掉")
check(SECRET not in cli_events.redact('{"api_key": "%s"}' % SECRET),
      'JSON 形式的 "api_key": "<key>" 被遮掉')
check(SECRET not in cli_events.redact(f"api_key={SECRET}"), "api_key=<key> 被遮掉")
bearer = f"Authorization: Bearer {SECRET}"
check(cli_events.redact(bearer) == "Authorization: Bearer ***",
      "Bearer 头去掉 key、留下方案名（方案名说明 key 是从哪个头发出去的）",
      cli_events.redact(bearer))
check(SECRET not in cli_events.redact(f"Authorization: {SECRET}"),
      "没有方案名的 Authorization 头照样遮掉")
jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef"
check(jwt not in cli_events.redact(f"Authorization: Bearer {jwt}"),
      "Bearer 后面的 JWT 也被遮掉")
check(SECRET not in cli_events.redact(f"access_token={SECRET}"), "access_token= 被遮掉")
check(SECRET not in cli_events.redact(f"请求头带上 {SECRET} 再试"),
      "裸 sk- key 在任何上下文里都被遮掉")
check(cli_events.redact("这里解释一下 api_key 应当如何轮换，与本次调研无关。")
      == "这里解释一下 api_key 应当如何轮换，与本次调研无关。",
      "没有 key 的中文说明不被改写")
once = cli_events.redact(f"api_key: {SECRET}")
check(cli_events.redact(once) == once, "脱敏是幂等的，重复套用不会损坏文本")
check(once == "api_key: ***", "替换后仍保留字段名，便于排错", once)
check(cli_events.redact("") == "" and cli_events.redact(None) is None,
      "空串 / None 原样返回")
check(cli_events.redact("sk-ab") == "sk-ab",
      "短于阈值的串不匹配（避免把普通词当成 key）")

# ------------------------------------------------------- sanitize + caps
print("\n[sanitize: 截断与上限]")

full_think = "推理：" + "x" * 50000
cleaned = cli_events._sanitize({"reasoning": full_think, "text": "y" * 30000})
check(len(cleaned["reasoning"]) == len(full_think),
      f"顶层 reasoning 不再被砍到 {cli_events.MAX_STR} 字（{len(full_think)} 字全留）",
      f"得到 {len(cleaned['reasoning'])}")
check(len(cleaned["text"]) == 30000, "顶层 text 同样全留",
      f"得到 {len(cleaned['text'])}")
check(cleaned["reasoning"].startswith("推理：") and cleaned["reasoning"].endswith("x"),
      "全留的是完整文本，不是重新拼出来的片段")

bulk = cli_events._sanitize({"result": "z" * 50000})
check(len(bulk["result"]) < cli_events.MAX_STR + 50
      and bulk["result"].endswith("chars]"),
      "工具抓取的网页结果仍按 MAX_STR 截断（它是体积大户）",
      f"得到 {len(bulk['result'])}")
nested = cli_events._sanitize({"messages": [{"text": "w" * 5000}]})
check(len(nested["messages"][0]["text"]) < 3000,
      "只有 data 顶层的 text/reasoning 才享受大上限",
      f"得到 {len(nested['messages'][0]['text'])}")
check(len(cli_events._sanitize({"prompt": "p" * 50000})["prompt"]) < 3000,
      "prompt 预览仍被截断")

LEAKY = "TOPSECRET" * 6
straddle = "x" * (cli_events.MAX_STR - 20) + '{"api_key": "' + LEAKY + '"}'
out = cli_events._sanitize({"detail": straddle})["detail"]
check("TOPSECRET" not in out,
      "跨越截断点的 key 不会留下前半段（脱敏先于截断）", out[-40:])

# ------------------------------------------------------------ emit boundary
print("\n[emit: 真实 sink]")

REC_PATH = os.path.join(TMP, "events.jsonl")
TAIL = "思" * 60000
REASONING = f"先看看 {SECRET} 是否有效，{TAIL}"
_original_acquire, _original_release = cli_events.acquire, cli_events.release
cli_events.acquire = lambda: None      # the sinks are what is under test here
cli_events.release = lambda: None
try:
    cli_events._T0 = time.monotonic()
    recorder = cli_events.EventRecorder(REC_PATH)
    recorder.start()
    qsink = cli_events.QueueSink()
    cli_events.subscribe(qsink)
    cli_events.emit("model_call_end", agent="TaskExecutionAgent", data={
        "call": 7, "stage": "子任务执行", "secs": 12.3, "chars": 60000,
        "reasoning": REASONING,
        "text": f"工具返回 401，请求里的 api_key 是 {SECRET}",
        "prompt": f"user: 调研一下 {SECRET}",
    })
    record = qsink.q.get_nowait()
    recorder.stop()
finally:
    cli_events.unsubscribe(qsink)
    cli_events.acquire = _original_acquire
    cli_events.release = _original_release
    cli_events._T0 = None

disk = open(REC_PATH, "rb").read().decode("utf-8")
check(SECRET not in disk, "JSONL 录制里没有 key")
check("api_key: ***" in disk or MASKED in disk, "录制里保留了脱敏后的痕迹", disk[:120])
expected_len = len(REASONING) - len(SECRET) + len(MASKED)
check(len(record["data"]["reasoning"]) == expected_len
      and record["data"]["reasoning"].endswith(TAIL),
      "实时渲染器收到的是完整思考，不是 2000 字残段",
      f"得到 {len(record['data']['reasoning'])}，期望 {expected_len}")
check(MASKED in record["data"]["reasoning"],
      "思考里的 key 被换成占位符，位置信息仍在", record["data"]["reasoning"][:40])
lines = [json.loads(l) for l in disk.strip().splitlines()]
written = [l for l in lines if l["event"] == "model_call_end"]
check(len(written) == 1 and written[0]["seq"] == record["seq"],
      "落盘的记录与队列里的同一条（录制可回放）")

# ----------------------------------------------------------- logging filter
print("\n[logging filter 挂在 handler 上]")


def emit_to_file(handler, message, *args):
    lg = logging.getLogger("secretprobe.%d" % time.monotonic_ns())
    lg.handlers = [handler]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    lg.error(message, *args)
    handler.flush()
    return lg


log_path = os.path.join(TMP, "direct.log")
h = logging.FileHandler(log_path, encoding="utf-8")
h.addFilter(cli_events.RedactingLogFilter())
h.setFormatter(logging.Formatter("%(message)s"))
emit_to_file(h, "鉴权失败，使用的 key 为 %s", SECRET)
written_text = open(log_path, encoding="utf-8").read()
check(SECRET not in written_text, "带 %s 参数的日志记录也被脱敏", written_text.strip())
check(MASKED in written_text, "占位符仍然出现在日志里")
check("鉴权失败" in written_text, "日志内容本身没有被丢掉")
h.close()

# A child logger propagates to a parent handler; a filter on the *logger* would
# be skipped for those records, which is why it belongs on the handler.
parent = logging.getLogger("secretprobe.parent")
parent.handlers = []
parent.propagate = False
prop_path = os.path.join(TMP, "propagated.log")
child_h = logging.FileHandler(prop_path, encoding="utf-8")
child_h.addFilter(cli_events.RedactingLogFilter())
child_h.setFormatter(logging.Formatter("%(message)s"))
parent.addHandler(child_h)
kid = logging.getLogger("secretprobe.parent.child")
kid.propagate = True
kid.setLevel(logging.DEBUG)
kid.warning("echo of api_key=%s", SECRET)
child_h.flush()
check(SECRET not in open(prop_path, encoding="utf-8").read(),
      "子 logger 冒泡上来的记录同样被脱敏")
child_h.close()
parent.handlers = []

odd = logging.getLogger("secretprobe.weird")
odd.handlers = []
odd.propagate = False
odd.setLevel(logging.DEBUG)
buf = io.StringIO()
mem_h = logging.StreamHandler(buf)
mem_h.addFilter(cli_events.RedactingLogFilter())
odd.addHandler(mem_h)
odd.error(object())          # a non-str msg must not raise inside the filter
check("object" in buf.getvalue(), "非字符串消息照常输出，过滤器不吞记录",
      repr(buf.getvalue()))

# ------------------------------------------ wired into the CLI's own logging
print("\n[_install_quiet_logging 覆盖 workflow 自己建的日志文件]")

import internagent_cli

original_add = logging.Logger.addHandler
quiet_dir = os.path.join(TMP, "logs")
node_file = os.path.join(quiet_dir, "InternResearch_node.log")
node_handler = None
try:
    cli_log = internagent_cli._install_quiet_logging(log_dir=quiet_dir)
    node_handler = logging.handlers.RotatingFileHandler(node_file, encoding="utf-8")
    node_handler.setFormatter(logging.Formatter("%(message)s"))
    node_logger = logging.getLogger("InternResearch.node5")
    node_logger.handlers = []
    node_logger.addHandler(node_handler)      # through the patched addHandler
    node_logger.setLevel(logging.DEBUG)
    node_logger.propagate = False
    node_logger.error("openai 返回 401：api_key %s 无效", SECRET)
    node_handler.flush()
    node_text = open(node_file, encoding="utf-8").read()
    check(SECRET not in node_text,
          "LoggerConfig 自建的文件 handler 也挂上了脱敏过滤器", node_text.strip())
    check("无效" in node_text and MASKED in node_text,
          "该写进去的排查信息照常写进去", node_text.strip()[:80])

    cli_handler = logging.getLogger("InternResearch").handlers[-1]
    check(any(isinstance(f, cli_events.RedactingLogFilter) for f in cli_handler.filters),
          "CLI 自己的 cli_*.log 也有过滤器")
    check(os.path.isfile(cli_log), "cli_*.log 建出来了", cli_log)
finally:
    logging.Logger.addHandler = original_add
    for name in ("InternResearch", "InternResearch.node5"):
        lg = logging.getLogger(name)
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
            handler.close()
    if node_handler is not None:
        node_handler.close()

verbose = internagent_cli._verbose_console_handler()
check(any(isinstance(f, cli_events.RedactingLogFilter) for f in verbose.filters),
      "/verbose 的 stderr 输出同样脱敏")

# ------------------------------------------------- doctor's log-file audit
print("\n[doctor ⑤：审计已经落盘的日志]")

check(cli_events.secret_matches(f"api_key={SECRET}"),
      "secret_matches 认得出没脱敏的行")
check(cli_events.secret_matches("api_key: ***") == [], "脱敏后的行不再算泄漏")
check(cli_events.secret_matches("Authorization: Bearer ***") == [],
      "Bearer *** 不再算泄漏")
check(cli_events.redact("sk-***") == "sk-***", "占位符本身不会被二次改写")

audit_dir = os.path.join(TMP, "audit")
os.makedirs(audit_dir, exist_ok=True)
plant = os.path.join(audit_dir, "cli_2026-01-01.log")
with open(plant, "w", encoding="utf-8") as fh:
    fh.write(f"ERROR 401 api_key={SECRET}\n第二行是正常的中文\n")
with open(os.path.join(audit_dir, "ignore.png"), "w", encoding="utf-8") as fh:
    fh.write(SECRET)      # 不是日志类扩展名，应该被跳过

hits = internagent_cli._audit_log_secrets(logs_dir=audit_dir)
check(len(hits) == 1 and hits[0][1] == 1, "只报出那一行，扩展名过滤生效", hits)
check(SECRET not in hits[0][2] and "api_key=***" in hits[0][2],
      "回报的片段本身已经脱敏", hits[0][2] if hits else "")
clean = os.path.join(TMP, "clean")
os.makedirs(clean, exist_ok=True)
with open(os.path.join(clean, "think_2026-01-01.md"), "w", encoding="utf-8") as fh:
    fh.write("api_key: ***\n调研结论：三种策略各有取舍。\n")
check(internagent_cli._audit_log_secrets(logs_dir=clean) == [],
      "现在写出的日志不会被误报")
check(internagent_cli._audit_log_secrets(logs_dir=os.path.join(TMP, "没有这个目录")) == [],
      "日志目录不存在时静默返回，不影响自检")

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)

"""End-to-end offline drive of the wizard + REPL in cmd_run.

Nothing here touches the network: the workflow (_ask), the key probe and the
agent construction are stubbed, so a scripted stdin exercises the *loop* —
command dispatch, per-turn state, error handling, interrupts, and the promise
that a key never reaches disk.
"""
import argparse
import atexit
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # the repo

import internagent_cli as ic
import rich.console

FAILS = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# Scratch lives outside the repo so a failed run cannot leave junk in a clone.
TMP = tempfile.mkdtemp(prefix="internagent_repl_")
atexit.register(shutil.rmtree, TMP, True)
CFG = os.path.join(TMP, "config.json")
FAKE_KEY = "sk-FAKE-should-never-hit-disk"
STUB = {"inits": 0, "asks": [], "files": [], "turn": 0}
ATTACH = os.path.join(TMP, "研究材料.txt")
BOGUS = os.path.join(TMP, "不存在的材料.txt")
with open(ATTACH, "w", encoding="utf-8") as fh:
    fh.write("第一批样本 12 例，指标见附表。\n")

REAL = {"prompt_line": ic._prompt_line, "prompt_secret": ic._prompt_secret,
        "prompt_optional": ic._prompt_optional,
        "probe_models": ic._probe_models, "init_agent": ic._init_agent,
        "ask": ic._ask, "CONFIG_PATH": ic.CONFIG_PATH, "REPO": ic.REPO,
        "wizard_reader": ic._wizard_reader,
        "Console.input": rich.console.Console.input}


def fake_ask(agent, args, question, console, log_file, live=False,
             archive=None):
    STUB["asks"].append(question)
    STUB["files"].append(getattr(args, "file", "missing"))
    if question == "boom":
        raise RuntimeError("AuthenticationError: Error code: 401 - "
                           "{'error': {'message': 'Invalid API Key'}}")
    if question == "cut":
        # Interrupted mid-way: some model calls already landed, so the partial
        # reasoning is exactly what the user will want to read.
        STUB["turn"] += 1
        if archive is not None:
            archive.add("被中断的调用", 1.0, 30, reasoning="方向不对，正在纠正", text="")
        raise ic.Interrupted(abandoned=True)
    if question == "slow":
        # The --timeout deadline fired; the worker was wedged, so the workflow
        # has to be rebuilt just like after an abandoned Ctrl+C.
        STUB["turn"] += 1
        if archive is not None:
            archive.add("超时的调用", 40.0, 90, reasoning="一直在等端点返回", text="")
        raise ic.TurnTimeout(600, abandoned=True, log_file="workflow.log")
    if question == "slow2":
        raise ic.TurnTimeout(600, abandoned=False, log_file="workflow.log")
    STUB["turn"] += 1
    for i in (1, 2):
        if archive is not None:
            archive.add(f"阶段 {STUB['turn']}-{i}", 3.0, 40,
                        reasoning="完整推理 %d-%d：%s" % (STUB["turn"], i,
                                                        "论证" * 60), text="")
    return (f"这是第 {STUB['turn']} 轮的回答正文。", {
        "secs": 12.0, "layers": 1, "model_calls": 3, "tool_calls": 2,
        "refs": 4,
        "references": [{"tag": str(i),
                        "title": f"文献标题 {i}：" + "中文长标题" * 20,
                        "url": f"https://example.test/paper{i}"}
                       for i in range(1, 5)],
        "usage": {"prompt": 1000, "completion": 200, "total": 1200},
        "usage_seen": True,
        "usage_by_stage": {"全局规划": {"prompt": 1000, "completion": 200,
                                       "total": 1200, "calls": 3}},
        "error": None})


def fake_init(args, console):
    STUB["inits"] += 1
    return object(), os.path.join(TMP, "workflow.log")


class Script:
    def __init__(self, lines):
        self.lines = list(lines)
        self.exit_code = None      # set when cmd_run calls fail() -> sys.exit()

    def next(self, *a, **k):
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


def run_repl(inputs, file=None, question=None, provider=None, base_url=None,
             model=None, mode=None, key_env=None, expect_exit=False,
             timeout=None, max_workers=None, max_tool_calls=None,
             max_layers=None):
    """Drive cmd_run with a scripted stdin; returns (stdout+stderr, script).

    STUB["prompts"] counts the wizard's input calls for this run only, so a
    fully-flagged invocation can be proven to ask nothing.

    The arrow-key reader is forced off: whether msvcrt can read keys depends on
    whether *this* process happens to have a console, and a suite that changed
    behaviour between `python tests/cli/run_all.py` and the same command piped
    to a file would be worthless. The arrow path is covered by test_wizard.py,
    which injects its own key sequence.
    """
    os.environ.pop(ic.WIZARD_KEY_VAR, None)
    STUB["prompts"] = 0
    script = Script(inputs)

    def count(fn):
        def wrapper(*a, **k):
            STUB["prompts"] += 1
            return fn(*a, **k)
        return wrapper

    ic._prompt_line = count(lambda msg, default="": script.next())
    ic._prompt_secret = count(lambda msg="": FAKE_KEY)
    # Mirrors the real one: Enter (an empty scripted line) keeps the default.
    ic._prompt_optional = count(lambda msg, default="": (script.next() or default))
    ic._probe_models = lambda base, key, timeout=25: (["test-model", "other"], None)
    ic._init_agent = fake_init
    ic._ask = fake_ask
    ic._wizard_reader = lambda: None
    ic.CONFIG_PATH = CFG
    ic.REPO = TMP  # keeps ThinkArchive out of the real logs/
    rich.console.Console.input = lambda self, *a, **k: script.next()
    env_key = None
    if key_env:
        env_key = os.environ.get(key_env)
        os.environ[key_env] = FAKE_KEY
    out, err = io.StringIO(), io.StringIO()
    so, se = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        try:
            ic.cmd_run(argparse.Namespace(cmd="run", api_key_env=key_env,
                                          verbose=False, timeout=timeout,
                                          record_events=None, file=file,
                                          question=question, provider=provider,
                                          base_url=base_url, model=model,
                                          mode=mode, max_workers=max_workers,
                                          max_tool_calls=max_tool_calls,
                                          max_layers=max_layers))
        except SystemExit as exc:
            if not expect_exit:
                raise
            script.exit_code = exc.code
    finally:
        sys.stdout, sys.stderr = so, se
        ic._prompt_line = REAL["prompt_line"]
        ic._prompt_secret = REAL["prompt_secret"]
        ic._prompt_optional = REAL["prompt_optional"]
        ic._probe_models = REAL["probe_models"]
        ic._init_agent = REAL["init_agent"]
        ic._ask = REAL["ask"]
        ic.CONFIG_PATH = REAL["CONFIG_PATH"]
        ic.REPO = REAL["REPO"]
        ic._wizard_reader = REAL["wizard_reader"]
        rich.console.Console.input = REAL["Console.input"]
        os.environ.pop(ic.WIZARD_KEY_VAR, None)
        if key_env:
            if env_key is None:
                os.environ.pop(key_env, None)
            else:
                os.environ[key_env] = env_key
    return out.getvalue() + err.getvalue(), script


# ① 供应商 ② base_url ③ 模型名 ④ 模式 ⑥ 文件（先打错一次）⑥ 附件 · 确认卡回车
WIZARD = ["1", "https://api.invalid.test/v1", "test-model", "qa",
          BOGUS, ATTACH, ""]

print("== 1. 一轮问答 + 小结 + 各命令 ==")
save_to = os.path.join(TMP, "saved.md")
joined, script = run_repl(WIZARD + [
    "第一个问题", "/help", "/usage", f"/save {save_to}", "/retry",
    "/think", "/think 1", "/answer", "/last", "/nope", "第二个问题",
    "/refs", "/refs 2", "/refs 乱输入",
    "/verbose", "/quiet", "/clear", "exit",
])
print(joined)
check("自动科研助手" in joined, "打印了启动界面")
for step in ("模型供应商", "base_url", "模型名", "API key", "运行模式",
             "本地文件（可选）", "研究问题"):
    check(step in joined, f"向导按顺序出现 {step}")
for i in range(1, 8):
    check(f"{i}/7" in joined, f"每一步标出自己排在第 {i} 步")
check("准备开始" in joined and "预算" in joined, "开局前给一张确认卡，写清要跑什么")
check("没有找到这个文件" in joined, "路径打错时说明找不到并再问一次")
check("本轮及后续追问都会带上" in joined, "确认后回显带上的文件")
check("这是第 1 轮的回答正文。" in joined, "第一轮回答渲染出来")
check("问 第一个问题" in joined, "问题回显在滚动区，认得出回答对应哪一问")
check("成本闸门" in joined and "并发节点" in joined and "图层" in joined,
      "开局就把三个乘数摆出来，不用事后翻 YAML")
check("本次会话 3 轮" in joined and "共 3600 tokens" in joined,
      "退出时总结这一趟会话跑了多少轮、多少 token")
check("思考全文" in joined and "think_" in joined.split("本次会话")[-1],
      "退出时给出思考归档的位置")
check("这是第 2 轮的回答正文。" in joined, "/retry 用同一个问题重跑")
check(script.lines == [] and STUB["asks"].count("第一个问题") == 2,
      "重跑再次提出同一个问题")
check("12.0s" in joined and "3 次模型调用" in joined, "每轮末尾有小结")
check("4 条引用" in joined, "小结里有引用数")
for name, _desc in ic.REPL_COMMANDS:
    check(name in joined, f"/help 列出 {name}")
check("未知命令 /nope" in joined, "未知命令被提示而不是崩")
check("1200" in joined and "全局规划" in joined, "/usage 打出分阶段表格")
check("文献标题 1" in joined and "https://example.test/paper1" in joined,
      "/refs 列出文献标题与 URL")
check("另有 2 条" in joined, "/refs 2 只列前两条并说明剩下的")
check("数字表示只列前几条" in joined, "/refs 的非数字参数会被指出来")
check(os.path.isfile(save_to) and "回答正文" in open(
    save_to, encoding="utf-8").read(), "/save 写出回答")
check(STUB["files"] and all(f == ATTACH for f in STUB["files"]),
      f"每一轮都把附件路径传给了工作流：{STUB['files']}")
check(ic._existing_file("") is None and ic._existing_file(BOGUS) is None,
      "_existing_file 拒绝空输入和不存在的路径")
check(ic._existing_file(f'"{ATTACH}"') == ATTACH,
      "从资源管理器粘过来的带引号路径能用")
check(ic._existing_file(ATTACH + "\\") == ATTACH, "尾随的反斜杠/斜杠被清掉")
check(ic._slug("近五年 GNN/transformer 的进展?") == "_近五年 GNN transformer 的进展",
      "文件名里非法字符变成空格，中文原样保留")
check("%" not in ic._slug("100% 命中率为什么高"),
      "百分号不进文件名——_write_answer 会把 % 当 strftime 模板")
check(len(ic._slug("问" * 100)) <= 41, "_slug 有长度上限")
check(ic._slug("....") == "" and ic._slug("") == "", "全是标点时返回空串")
check(ic._slug("收尾问题.").endswith("问题"), "结尾的点被去掉（Windows 不接受）")
check(not any(c in '<>:"/\\|?*' for c in ic._slug('a<b>c:d"e/f\\g|h?i*j')),
      "NTFS 拒绝的字符一个都不留")
check("完整推理 1-1" in joined, "/think 1 展开归档的完整推理")
check("本轮" in joined or "共" in joined, "/think 无参数时列出本轮")
check(joined.count("上一轮回答") >= 2, "/answer 与 /last 都能回看回答")

print("== 2. key 只存在于进程 ==")
check(os.path.isfile(CFG), "配置文件已写出")
raw = open(CFG, encoding="utf-8").read()
data = json.loads(raw)
check(set(data) <= set(ic.CONFIG_KEYS), f"只含 {ic.CONFIG_KEYS}：{sorted(data)}")
check(FAKE_KEY not in raw, "磁盘上的配置没有 key")
check(FAKE_KEY not in joined, "屏幕上没有回显 key")
check(data.get("base_url") == "https://api.invalid.test/v1", "base_url 被记住")
check(os.environ.get(ic.WIZARD_KEY_VAR) is None, "退出后环境变量已清理")
think_files = [f for f in os.listdir(os.path.join(TMP, "logs"))]
check(len(think_files) == 1 and think_files[0].startswith("think_")
      and "完整推理 3-2" in open(os.path.join(TMP, "logs", think_files[0]),
                                 encoding="utf-8").read(),
      f"思考归档写入文件：{think_files}")

print("== 3. 出错的一轮不会杀掉 REPL ==")
if os.path.exists(CFG):
    os.remove(CFG)
joined, _ = run_repl(WIZARD + ["boom", "/answer", "恢复正常的问题", "exit"])
check("API key 无效" in joined, "401 被翻译成可操作提示")
check("Invalid API Key" in joined, "原始错误仍然给出")
check("还没有可回看的回答" in joined, "没有回答时 /answer 说清楚而不是印空白")
check("恢复正常的问题" in STUB["asks"], "报错后继续接受提问")
check("这是第" in joined, "下一轮正常出答案")
check(os.path.isfile(CFG), "同一会话会重新保存配置")

print("== 4. 中断的一轮会重建工作流 ==")
inits_before = STUB["inits"]
joined, _ = run_repl(WIZARD + ["cut", "中断后的问题", "exit"])
check(STUB["inits"] == inits_before + 2, "启动一次 + abandoned 后重建一次")
check("重建工作流" in joined, "告知用户正在重建")
check("中断后的问题" in STUB["asks"], "重建后继续提问")
check("/think 查看" in joined, "中断也提示本轮留下的思考")

print("== 4b. --timeout 到点的一轮 ==")
inits_before = STUB["inits"]
asks_before = len(STUB["asks"])
joined, _ = run_repl(WIZARD + ["slow", "超时后的问题", "slow2", "exit"])
check("执行超时（600s）" in joined, "说清是哪一轮、超时上限多少")
check("加大 --timeout" in joined, "给出下一步可做什么")
check(STUB["inits"] == inits_before + 2,
      "线程卡住的超时同样重建工作流，否则下一轮会撞上 图正在执行中")
check("超时后的问题" in STUB["asks"][asks_before:], "超时不会退出会话")
check("/think 查看" in joined, "超时那一轮的部分思考仍然留着")
check(joined.count("已重建，可以继续提问") == 1,
      "只有线程卡住的超时才重建；已经退出的那次只说一声")

print("== 5. --file 走的是同一个附件步骤 ==")
files_before = len(STUB["files"])
joined, _ = run_repl(WIZARD[:4] + ["", "带附件的问题", "exit"], file=ATTACH)
check("本轮及后续追问都会带上" in joined
      and os.path.basename(ATTACH) in joined and TMP in joined,
      "--file 给了就跳过第⑥步，并把路径回显出来")
check(STUB["files"][files_before:] == [ATTACH], "附件路径传到工作流")

joined, script = run_repl(WIZARD[:4] + [""], file=BOGUS, expect_exit=True)
check(script.exit_code == 1, "--file 指向不存在的文件时立刻退出（不 Start 工作流）")
check("--file 指定的文件不存在" in joined, "退出原因说清楚是哪个参数")
check(STUB["asks"] and "带附件的问题" in STUB["asks"], "上面那轮确实跑到了工作流")

print("== 6. /carry 把上一轮结论接进下一轮 ==")
asks_before = len(STUB["asks"])
joined, _ = run_repl(WIZARD + [
    "第一轮的问题", "/carry", "第二轮的问题", "/carry off", "第三轮的问题",
    "exit"])
sent = STUB["asks"][asks_before:]
check(sent[0] == "第一轮的问题", "默认关闭：问题原样发给工作流")
check("每一轮都会重新规划" in joined, "第一轮后提示轮与轮之间没有记忆")
check("已开启" in joined, "/carry 报告状态改变了")
check(sent[1].startswith("【上一轮结论") and sent[1].endswith("第二轮的问题"),
      "开启后按「背景 + 本轮问题」两段发送")
check("回答正文" in sent[1], "上一轮的回答确实进了 payload")
check("已带上上一轮结论" in joined, "告知用户这轮带了多少字")
check(sent[2] == "第三轮的问题", "/carry off 之后恢复只发问题本身")

joined, _ = run_repl(WIZARD + ["q1", "/carry 随便写", "exit"])
check("只认 /carry" in joined, "/carry 的非法参数会被指出来")

retry_before = len(STUB["asks"])
joined, _ = run_repl(WIZARD + ["q1", "/carry on", "q2", "/retry", "exit"])
sent = STUB["asks"][retry_before:]
check(sent[1].endswith("q2") and sent[2] == sent[1],
      "/retry 重发上一轮真正发出去的文本，而不是重新拼一份背景")

cap_before = ic.CARRY_CHARS
ic.CARRY_CHARS = 6
joined, _ = run_repl(WIZARD + ["q1", "/carry on", "q2", "exit"])
sent = STUB["asks"][-1]
recap = sent.partition("【本轮问题】")[0].split("】", 1)[1].strip()
check(len(recap) <= ic.CARRY_CHARS + 1 and recap.endswith("…"),
      f"背景被截到上限以内并留下省略号：{recap!r}")
check(sent.endswith("q2"), "截断只作用于背景，问题本身完整")
ic.CARRY_CHARS = cap_before

print("== 7. 给齐参数就一个问题都不问 ==")
if os.path.exists(CFG):
    os.remove(CFG)
asks_before = len(STUB["asks"])
joined, script = run_repl(["exit"], question="脚本化的问题", provider="deepseek",
                          model="test-model", mode="qa", file=ATTACH,
                          key_env="INTERNAGENT_TEST_KEY")
check(STUB["prompts"] == 0, f"向导的输入函数一次都没被调用（{STUB['prompts']}）")
check("① 模型供应商" not in joined and "⑦ 研究问题" not in joined,
      "带参数的步骤不再打印提问")
check("脚本化的问题" in STUB["asks"][asks_before:], "位置参数直接成为第一个问题")
check(STUB["files"][-1] == ATTACH, "--file 照样接进工作流")
check("deepseek" in open(CFG, encoding="utf-8").read(), "脚本化运行也保存配置")
check(FAKE_KEY not in joined, "从环境变量读的 key 不回显在终端上")
check(os.environ.get("INTERNAGENT_TEST_KEY") is None, "测试自己也不留环境变量")
check(script.lines == [], "跑完一轮后 REPL 仍在，exit 正常退出")

joined, script = run_repl(["exit"], question="q", provider="不存在的供应商",
                          model="m", key_env="INTERNAGENT_TEST_KEY",
                          expect_exit=True)
check(script.exit_code == 1 and "--provider 只认" in joined,
      "--provider 拼错时列出可用名字并退出")

# 位置参数即"别再问我别的"：缺什么就点名缺哪个参数，而不是一步步问下去。
joined, script = run_repl(["exit"], question="q", provider="deepseek",
                          model="test-model", expect_exit=True)
check(script.exit_code == 1 and "--api-key-env" in joined,
      "脚本化运行不给 --api-key-env 会被拒绝（key 不该出现在命令行上）")
check(STUB["prompts"] == 0, "被拒绝的脚本化运行也没有回退到提问")

if os.path.exists(CFG):
    os.remove(CFG)  # 这一条测的是"没有配置可借用"，先清掉上一段跑测试存的
joined, script = run_repl(["exit"], question="q", model="test-model",
                          key_env="INTERNAGENT_TEST_KEY", expect_exit=True)
check(script.exit_code == 1 and "需要 --provider" in joined,
      "没有配置时脚本化运行点名 --provider")

joined, script = run_repl(["exit"], question="q", provider="deepseek",
                          model="别的模型", key_env="INTERNAGENT_TEST_KEY",
                          expect_exit=True)
check(script.exit_code == 1 and "不在该端点提供的模型里" in joined,
      "脚本化运行时模型名对不上直接失败，不留一个没人看的警告")

joined, _ = run_repl(["exit"], question="q", provider="deepseek",
                     model="test-model", key_env="INTERNAGENT_TEST_KEY")
check(STUB["prompts"] == 0 and "⑥ 本地文件" not in joined,
      "没给 --file 的脚本化运行跳过第⑥步（附件本来就是可选的）")

print("== 7b. --max-* 走的是同一条命令行 ==")
joined, _ = run_repl(["exit"], question="q", provider="deepseek",
                     model="test-model", key_env="INTERNAGENT_TEST_KEY",
                     max_workers=3, max_tool_calls=2, max_layers=1)
check("并发节点 ≤3" in joined and "≤2 轮工具" in joined and "图层 ≤1" in joined,
      "三个闸门都反映在开局的提示里")
check(STUB["prompts"] == 0, "带上闸门参数同样一个问题都不问")

print("== 8. /save 不带参数时按问题命名 ==")
# 默认路径是相对的，所以在 TMP 里跑，别把产物落进仓库。
cwd = os.getcwd()
os.chdir(TMP)
try:
    run_repl(WIZARD + ["近五年 GNN/transformer 的进展", "/save", "exit"])
finally:
    os.chdir(cwd)
saved = sorted(os.listdir(os.path.join(TMP, "answers")))
hit = [n for n in saved if "近五年 GNN transformer 的进展" in n]
check(len(hit) == 1 and hit[0].startswith("answer_2") and hit[0].endswith(".md"),
      f"默认文件名同时有时间和问题摘要：{saved}")
check(hit and open(os.path.join(TMP, "answers", hit[0]),
                   encoding="utf-8").read().strip(), "写出来的确实是回答正文")

print("== 9. --timeout 会被记住，也会被用上 ==")
joined, _ = run_repl(WIZARD + ["exit"], timeout=90)
check("每轮上限 90s" in joined, "开始时就说明这一轮的时间预算")
check(json.load(open(CFG, encoding="utf-8"))["timeout"] == 90,
      "上限写进了配置")
joined, _ = run_repl(["exit"], question="q", provider="deepseek",
                     model="test-model", key_env="INTERNAGENT_TEST_KEY")
check("每轮上限 90s" in joined, "下次不带 --timeout 时沿用上次的 90s")
joined, _ = run_repl(["exit"], question="q", provider="deepseek",
                     model="test-model", key_env="INTERNAGENT_TEST_KEY",
                     timeout=30)
check("每轮上限 30s" in joined, "命令行显式给的比记住的大")

print("== 10. --model 只改默认模型，不抹掉分段模型 ==")
# DRAgent merges workflow_config with a shallow update, so the override has to
# carry the config file's own model section with it.
cfg_repo = os.path.join(TMP, "moderepo")
cfg_dir = os.path.join(cfg_repo, "internagent", "mas", "agents", "dr_agents")
os.makedirs(cfg_dir, exist_ok=True)
with open(os.path.join(cfg_dir, "config_qa.yaml"), "w", encoding="utf-8") as fh:
    fh.write("model:\n  default_model: o4-mini\n"
             "  synthesizer_model: gpt-4o\n  task_agent_model: null\n")
real_repo = ic.REPO
ic.REPO = cfg_repo
try:
    merged = ic._model_override("qa", "deepseek-chat")
finally:
    ic.REPO = real_repo
check(merged.get("default_model") == "deepseek-chat", "默认模型换成了 --model 给的")
check(merged.get("synthesizer_model") == "gpt-4o",
      "config 里单独指定的合成模型没有被丢掉")
check("task_agent_model" in merged and merged["task_agent_model"] is None,
      "显式的 null 也原样保留")
ic.REPO = os.path.join(TMP, "nosuchrepo")
try:
    check(ic._model_override("qa", "m") == {"default_model": "m"},
          "读不到 config 文件时退回只给 default_model，不报错")
finally:
    ic.REPO = real_repo

print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过")

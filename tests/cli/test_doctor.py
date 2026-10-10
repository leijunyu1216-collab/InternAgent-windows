"""Offline checks for `internagent doctor`: does the pre-flight actually fire?

A doctor that only ever prints green is decoration, so each broken state is
simulated here (temp config, stubbed requirement list) and the expected blocker
plus exit code is asserted. The real environment is never mutated.
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

FAILS = []
TMP = tempfile.mkdtemp(prefix="internagent_doctor_")
atexit.register(shutil.rmtree, TMP, True)


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


REAL = {"CONFIG_PATH": ic.CONFIG_PATH, "REPO": ic.REPO,
        "reqs": ic._read_requirements}


def doctor(cfg=None, reqs=None, repo=None):
    """Run doctor against a scratch config/requirement list; returns (text, code)."""
    ic.CONFIG_PATH = cfg if cfg is not None else os.path.join(TMP, "none.json")
    ic.REPO = repo or REAL["REPO"]
    if reqs is not None:
        ic._read_requirements = lambda name: list(reqs)
    else:
        ic._read_requirements = REAL["reqs"]
    out, err = io.StringIO(), io.StringIO()
    so, se = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    code = 0
    try:
        ic.cmd_doctor(argparse.Namespace(selftest=False))
    except SystemExit as exc:
        code = exc.code or 0
    finally:
        sys.stdout, sys.stderr = so, se
        ic.CONFIG_PATH = REAL["CONFIG_PATH"]
        ic.REPO = REAL["REPO"]
        ic._read_requirements = REAL["reqs"]
    return out.getvalue() + err.getvalue(), code


print("== 1. 现状：只该缺 key ==")
text, code = doctor()
check(code == 0, f"当前环境无阻塞项（exit {code}）")
check("requirements" in text and "个包全部就位" in text, "按 requirements 文件核对依赖")
check("camel" not in text, "不把仓库内置的 camel 当成 pip 依赖报缺失")
check("API key" in text, "把‘还差 key’说成唯一剩下的事")

print("== 2. 配置里混进了 key ==")
bad_cfg = os.path.join(TMP, "bad.json")
with open(bad_cfg, "w", encoding="utf-8") as fh:
    json.dump({"provider": "deepseek", "api_key": "sk-LEAK-1234567890",
               "model": "deepseek-chat"}, fh)
text, code = doctor(cfg=bad_cfg)
print(text)
check(code == 1, f"退出码 1（{code}）")
check("密钥落盘" in text and "api_key" in text, "点名是哪个字段不该在")
check("sk-LEAK-1234567890" not in text, "报告里不复述 key 本身")
check("key 泄漏" in text, "列入阻塞项")

print("== 3. 值长得像 key 也算 ==")
weird_cfg = os.path.join(TMP, "weird.json")
with open(weird_cfg, "w", encoding="utf-8") as fh:
    json.dump({"model": "sk-abcdefgh"}, fh)
text, code = doctor(cfg=weird_cfg)
check(code == 1 and "model" in text, "字段名无害、值形如 sk- 也被拦下")

print("== 4. 依赖缺失 ==")
text, code = doctor(reqs=["rich", "no-such-package-xyz-123"])
check(code == 1, f"退出码 1（{code}）")
check("no-such-package-xyz-123" in text, "缺的包被点名")
check("requirements-qa-windows.txt" in text, "给出安装命令")

print("== 5. requirements 文件解析 ==")
names = REAL["reqs"]("requirements-qa-windows.txt")
check("python-dotenv" in names or "dotenv" in names, "读到声明的包")
check(all(not n.startswith(("#", "-")) for n in names), "注释与选项行不会伪装成包名")
check(all("==" not in n for n in names), "版本号被剥掉")
check(REAL["reqs"]("no-such-file.txt") == [], "文件不存在时返回空而不是崩")

print("== 6. logs 不可写 ==")
blocked = os.path.join(TMP, "blocked")
os.makedirs(blocked, exist_ok=True)
# A *file* named logs makes every way of creating logs/think_*.md fail, and
# ThinkArchive's own OSError fallback means the run itself still works.
with open(os.path.join(blocked, "logs"), "w", encoding="utf-8") as fh:
    fh.write("not a directory")
text, code = doctor(repo=blocked)
check("logs/" in text and "不可写" in text, "指出归档落点写不进去")
check("/think" in text, "说明后果是关不掉后回看，而不是不能用")
# Not a blocker: the run works, only the after-the-fact review is lost, so the
# exit code must stay 0 or scripts treating doctor as a gate would over-fire.
check(code == 0, f"logs 写不进去只是降级，不该让 doctor 判定失败（exit {code}）")

print("== 7. --selftest 会跑离线套件 ==")
src = open(os.path.join(REAL["REPO"], "internagent_cli.py"),
           encoding="utf-8").read()
check("selftest" in src and "run_all.py" in src, "doctor 里接了自检入口")

print("== 8. 编码探针按真实字形判断 ==")


class Stream:
    def __init__(self, encoding):
        self.encoding = encoding


ok, enc, _ = ic._encoding_probe(Stream("utf-8"))
check(ok and enc == "utf-8", "utf-8 通过")
ok, enc, detail = ic._encoding_probe(Stream("cp936"))
check(not ok and detail, f"cp936 被判定为会崩：{detail}")
ok, _, _ = ic._encoding_probe(Stream(None))
check(ok, "没有 encoding 属性时不报错（按 utf-8 处理）")
ok, _, _ = ic._encoding_probe(Stream("not-a-codec"))
check(not ok, "乱写的编码名也被拦下而不是崩在查找表上")

print("== 9. 上游本地补丁被 git pull 冲掉 ==")
# The patched files are tracked upstream, so a reset/pull silently restores the
# broken version: no error at runtime, just OpenAI-only models or a layer loop
# with no ceiling. Doctor is the only place that notices.
patch_repo = os.path.join(TMP, "patchrepo")
os.makedirs(os.path.join(patch_repo, "logs"), exist_ok=True)
stripped = False
for rel, marker, _why in ic.LOCAL_PATCHES:
    src = os.path.join(REAL["REPO"], rel)
    dst = os.path.join(patch_repo, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as fh:
        body = fh.read()
    if not stripped and marker in body:
        body = body.replace(marker, "# this line went away in a git pull")
        stripped = True
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(body)
text, code = doctor(repo=patch_repo)
check("本地补丁" in text, "有一节专门核对本地补丁")
check(f"{len(ic.LOCAL_PATCHES) - 1}/{len(ic.LOCAL_PATCHES)} 在位" in text,
      "报出还在位几处")
check("this line went away" not in text, "只按标记判断，不复述文件内容")
check("Unsupported model" in text or "无限重试" in text or "o4-mini" in text,
      "点名少了那一处会坏什么")
check(code == 0, f"补丁缺失只是提醒，不该让脚本化的 doctor 判定失败（exit {code}）")

text, code = doctor()
check(f"{len(ic.LOCAL_PATCHES)}/{len(ic.LOCAL_PATCHES)} 在位" in text,
      "当前工作区里每一处补丁都在")

print()
if FAILS:
    print(f"{len(FAILS)} 项失败:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("全部通过")

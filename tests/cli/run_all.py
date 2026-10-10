#!/usr/bin/env python3
"""Run every offline CLI check suite in this folder.

Each suite runs as its own subprocess: they monkeypatch Completions.create,
raise signals and rewrite module globals, so sharing one interpreter would make
order matter. No API key and no network — a fresh clone can verify the whole
display layer before it spends any credits.

    python tests/cli/run_all.py            # all suites
    python tests/cli/run_all.py hook repl  # only the named ones
    python tests/cli/run_all.py -v         # keep full output
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def suites(filters):
    names = []
    for name in sorted(os.listdir(HERE)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        stem = name[len("test_"):-len(".py")]
        # Any filter matching is enough; requiring all of them made `run_all.py
        # think repl` silently select nothing.
        if filters and not any(f in stem for f in filters):
            continue
        names.append((stem, os.path.join(HERE, name)))
    return names


def main(argv):
    verbose = "-v" in argv or "--verbose" in argv
    filters = [a for a in argv if not a.startswith("-")]
    found = suites(filters)
    if not found:
        print(f"没有匹配的测试文件（在 {HERE}）", file=sys.stderr)
        return 2

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed = []
    for stem, path in found:
        proc = subprocess.run([sys.executable, path], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = proc.stdout.decode("utf-8", "replace")
        tail = [l for l in text.split("\n") if "FAIL" in l or l.startswith("  - ")]
        if proc.returncode == 0 and not tail:
            print(f"  ✓  {stem}")
        else:
            failed.append(stem)
            print(f"  ✗  {stem}  (exit {proc.returncode})")
            if verbose:
                print(text)
            else:
                for line in tail[:20]:
                    print("     " + line.strip())
                if not tail:
                    print("\n".join(text.split("\n")[-12:]))

    print(f"\n{len(found) - len(failed)}/{len(found)} 个套件通过"
          + (f" · 失败: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

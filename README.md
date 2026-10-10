# InternAgent-1.5: A Unified Agentic Framework for Long-Horizon Autonomous Scientific Discovery
> *Autonomous Discovery Across All Sciences*
- **Papers**: [InternAgent 1.0](https://arxiv.org/abs/2505.16938) | [InternAgent 1.5](https://huggingface.co/papers/2602.08990)
- **Links**: [Website](https://discovery.intern-ai.org.cn) | [HuggingFace](https://huggingface.co/collections/InternScience/internagent)
- **Read in**: English (below) · [简体中文](#简体中文)


---

## Windows-native port, plus one terminal command

Upstream [InternScience/InternAgent](https://github.com/InternScience/InternAgent) is a
Linux project. Not in a philosophical way: every documented entry point is a POSIX
shell script (`scripts/`: `run_qa.sh`, `run_discovery.sh`, `run_sci.sh`,
`run_skip-idea.sh`, plus 119 more under `tasks/` and at the repo root — most tasks ship
their own `launcher.sh`), and `requirements.txt` carries 15 lines —
`triton` and the `nvidia-*-cu12` wheels — that exist only because
`torch` asks for them `platform_system == "Linux"`. There is no WSL2-free path in the
upstream README.

This repository is that same framework running on Windows itself — conda + pip, one
Python — with a single terminal entry point added on top so you do not have to
assemble a command line out of four scripts and a `.env`.

### What this fork adds

| | |
| --- | --- |
| `internagent_cli.py`, `cli_ui.py`, `cli_live.py`, `cli_events.py` | one command: a seven-step setup wizard, a live view of what every agent is reasoning, a multi-turn REPL, a dependency self-check. No new runtime dependency beyond `rich` + `prompt_toolkit`. |
| `requirements-qa-windows.txt` | the slim set the deep-research chain actually imports on Windows. This is what the CLI was built and tested against. |
| `requirements-discovery-windows.txt` | the full upstream list, minus those 15 Linux-only lines, with `onnxruntime` back to 1.20.1 (markitdown caps it on win32) and `tooluniverse` replaced by `PyMuPDF` — `tooluniverse` is imported by no file here, while the `import fitz` the code *does* reach lazily comes from PyMuPDF. |
| 15 edits under `internagent/mas/` | the model layer accepts any OpenAI-compatible endpoint instead of a hard-coded name list, several loops that could not stop were given one (a planner retrying 401s forever, a tool re-issuing the identical call until the budget burned out), and all three deep-research configs ship with `volc_search` off since it needs a separate enterprise key. `internagent doctor` verifies each one is still in place after a pull. |
| `tests/cli/` | 14 offline suites. The whole display layer — banner, live lanes, wizard widgets, redaction, the real `ExecutionAgent` loops — is checked without an API key and without a network. |
| `docs/cli.md` | the wizard, the REPL commands, what the live display means, where keys can and cannot go, and the Windows terminal quirks worth not re-deriving. (Chinese.) |

### Install and run

```powershell
conda create -n internagent python=3.11 -y
conda activate internagent
pip install -r requirements-qa-windows.txt
pip install rich prompt_toolkit        # the CLI's own two dependencies
python internagent_cli.py doctor       # encoding, deps, writable logs, the 15 patches
python internagent_cli.py              # and the wizard starts
```

`doctor` is the thing to run first: it names the failing item rather than letting you
discover it as a traceback. It also reports what is *not* installed — discovery mode
needs `requirements-discovery-windows.txt`, and one experiment backend needs a
separate `claude` CLI with its own auth.

The wizard is the whole interface; nothing has to be memorised:

```
模型供应商  1/7
   1  deepseek     官方端点 · V3 / R1，国内直连
   2  qwen         阿里百炼 dashscope，国内直连
   …
  › ✓ 模型供应商  deepseek  官方端点 · V3 / R1，国内直连

base_url  2/7
  形如 https://host/v1；本机端点写 http://localhost:11434/v1
  › [https://api.deepseek.com/v1] ✓ base_url  https://api.deepseek.com/v1
```

Arrow keys pick on a real Windows console; Git Bash gets the same list as numbers
because an MSYS pty exposes no console buffer to read keys from — the same reason
`prompt_toolkit` cannot attach there.

### Keys never touch disk

The key is typed into the process and published as an environment variable for that
process only. Not in `.env`, not in `~/.internagent.json` (a whitelist filters what
gets saved), never on the command line, where it would land in shell history and the
process list — scripted runs pass `--api-key-env VAR` instead. Before anything reaches
`logs/`, a `--record-events` JSONL, or the screen, `cli_events.redact()` strips
`api_key:`/`secret=` assignments, bare `sk-…` strings and `Bearer …` tokens, *then*
truncates, so a key straddling the cut cannot leave its first half behind. `doctor`
greps your existing logs for those shapes and prints any hit already masked.

### Provenance

Based on `InternScience/InternAgent` at commit `fa8c3eedfa9751d3752ea6eb49220b303ac2397d`
(2026-07-29), Apache License 2.0 — the `LICENSE` file here is upstream's, unmodified.
Per §4(b), the notice above states what was changed: 10 files under `internagent/mas/`
were edited (15 separate patches, itemised in `docs/cli.md`) along with `.gitignore`
and this `README.md`, and everything in the table is new. This is
an independent Windows port; it is not a release by the upstream team, and the paper
links, badges and the rest of this README belong to them.

### Acknowledgements

**Upstream.** The framework itself, every task implementation under `tasks/`, the
`sci_tasks` / [ResearchClawBench](https://github.com/InternScience/ResearchClawBench)
reproduction set and [MLEvolve](https://github.com/InternScience/MLEvolve) are the work of
the [InternScience](https://github.com/InternScience) team — the Citation section at the
bottom of this file lists the authors, and the
[website](https://discovery.intern-ai.org.cn) / [HuggingFace collection](https://huggingface.co/collections/InternScience/internagent)
for the project itself. This fork is not affiliated with them and is not endorsed by them;
any bug in the port is ours.

**This fork.** Maintained by [@leijunyu1216-collab](https://github.com/leijunyu1216-collab).
Open an issue here if the Windows path breaks.

**How it was built.** The Windows port, the CLI, the live display layer and the offline test
suites were written jointly with an AI coding assistant (Qoder). Every commit here is authored
and pushed under the maintainer's account, and the code was run and checked in a real terminal
rather than generated unreviewed — but saying "one person wrote this" would not be accurate.

---

## 简体中文

上游的说明全是英文，而这个 CLI 的界面是中文的。这一段把「和上游有什么不同、怎么装、
怎么跑」单独讲一遍，细节在 [`docs/cli.md`](docs/cli.md)（也是中文）。

### 为什么要这个分支

上游 [InternScience/InternAgent](https://github.com/InternScience/InternAgent) 是 Linux 项目，
而且是字面意义上的：文档里给出的每个入口都是 POSIX shell 脚本（`scripts/` 下 4 个，
`tasks/` 和仓库根目录还有 119 个，多数任务自带一个 `launcher.sh`），`requirements.txt`
里有 15 行是 `triton` 和 `nvidia-*-cu12`——它们只因 `torch` 在 `platform_system == "Linux"`
时才被要。上游 README 没有给出不装 WSL2 的路线。

这里跑的是同一套框架，但在 Windows 本机：一个 conda 环境、一个 Python、一条命令，
不用 shell 脚本，也不用把配置拼进 `.env`。

### 多了什么

| | |
| --- | --- |
| `internagent_cli.py` `cli_ui.py` `cli_live.py` `cli_events.py` | 一条命令：七步配置向导、每个 agent 正在推什么的实时视图、多轮 REPL、依赖自检。除 `rich` + `prompt_toolkit` 外不引入新的运行依赖。 |
| `requirements-qa-windows.txt` | 深度调研链路在 Windows 上真正会 import 的那部分依赖，CLI 就是照这套构建并测试的。 |
| `requirements-discovery-windows.txt` | 上游完整清单去掉那 15 行 Linux-only，`onnxruntime` 回到 1.20.1（markitdown 在 win32 上有上限），`tooluniverse` 换成 `PyMuPDF`——本仓库没有文件 import `tooluniverse`，而代码懒加载时真正会碰到的 `import fitz` 来自 PyMuPDF。 |
| `internagent/mas/` 下 10 个文件、15 处改动 | 模型层接受任意 OpenAI 兼容端点，而不是写死的模型名清单；几处原本停不下来的循环有了出口（规划器对 401 无限重试、同一个工具调用被原样重发直到预算烧完）；三份深度调研配置都把 `volc_search` 关掉了，它要单独的企业 key。`internagent doctor` 逐项检查它们在 `git pull` 之后是否还在位。 |
| `tests/cli/` | 14 个离线套件：banner、实时分栏、向导控件、密钥脱敏、真实的 `ExecutionAgent` 循环，全都在没有 API key、不联网的条件下验过。 |
| `docs/cli.md` | 向导、REPL 命令、实时界面每一栏的含义、key 能出现在哪里不能出现在哪里，以及不值得再踩一遍的 Windows 终端坑。 |

### 安装与运行

```powershell
conda create -n internagent python=3.11 -y
conda activate internagent
pip install -r requirements-qa-windows.txt
pip install rich prompt_toolkit        # CLI 自己的两个依赖
python internagent_cli.py doctor       # 编码、依赖、logs 可写、15 处补丁
python internagent_cli.py              # 直接进向导
```

`doctor` 要先跑：它会点名是哪一项不对，而不是让你从 traceback 里反推。它同时会说明哪些
东西*没*装——discovery 模式还要 `requirements-discovery-windows.txt`，有一个实验后端需要
单独的 `claude` CLI 及它自己的登录。

七步依次是 **模型供应商 → base_url → 模型名 → API key → 运行模式 → 本地文件（可选）→
研究问题**，标题右侧标着 `n/7`，界面实录见上文。真 Windows 控制台上用方向键选，Git Bash
里同一份列表退成数字输入——MSYS 的 pty 不提供可读按键的 console buffer，`prompt_toolkit`
在那里挂不上也是同一个原因。

### key 不落盘

key 只在你输入它的那个进程里，以环境变量发布：不进 `.env`，不进 `~/.internagent.json`
（有一份白名单过滤落盘字段），也不出现在命令行上——那会落进 shell 历史和进程列表；
脚本化运行改用 `--api-key-env VAR`。任何东西在写进 `logs/`、`--record-events` 的 JSONL
或打到屏幕之前都要过 `cli_events.redact()`：先剥掉 `api_key:`/`secret=` 赋值、裸的
`sk-…` 串和 `Bearer …` token，**然后**才截断，于是一把正好跨在截断点上的 key 不会只剩
前半截留在外面。`doctor` 拿这些形状去 grep 你已有的日志，命中就按已脱敏的形式打出来。

### 能跑哪些任务

**算法发现任务**在 `tasks/` 下，这份代码里有 13 个目录（上游 News 说的「12 类任务」是
2025.10 的口径）。上游说每个任务目录带 `prompt.json`、基线 `code/` 和 `launcher.sh`：
实测 13 个都有 `prompt.json`，12 个有 `code/`，11 个有 `launcher.sh`——`AutoDebug`、
`AutoDrug` 没有 launcher，`AutoDrug` 只有 `prompt.json` 一个文件；`tasks/` 下 14 个
`launcher.sh` 里有 3 个是 `run_0/` 里跑完留下的副本。下表按各任务 `prompt.json` 里的
`domain` 和 `task_description` 概述：

| 任务 | 领域 | 要 agent 做的事 |
| --- | --- | --- |
| `AutoChem` | 化学反应产率预测 | 在 LLaMA 回归骨架上配 LoRA 与预测网络，输入是 SMILES 写的反应物/产物、反应类型（Suzuki–Miyaura 偶联）等结构化数据；训练集只有 60 条，特征抽取得稳 |
| `AutoCls2D` | 图像分类 | 改进在 CIFAR-100 上训练的 2D 分类网络的模型设计 |
| `AutoCls3D` | 点云分类 | 改进在 ModelNet 上训练的 3D 点云分类网络；难点是点云无序、不规则 |
| `AutoClsSST` | 自然语言处理 | 改进在 SST-2 上做情感分类的网络设计 |
| `AutoDebug` | 机器学习 | 上游推荐的第一个任务：不用下载数据集和权重，用来验证环境装好了没有；主题是「结构感知回归器」 |
| `AutoDrug` | 催化剂设计 | 为碳捕集过程设计一种新的有机催化剂以提升 CO2 转化，用参考函数检索已有催化剂的 SMILES、生成结构变体、验证新颖性 |
| `AutoEAP` | 基因组学 · 增强子活性预测 | 输入 DNA 序列（典型长度 200bp）预测增强子活性，重点在模型设计 |
| `AutoMem` | agent 记忆系统 | 给对话式 AI agent 设计记忆系统，改进记忆的存储与检索，可借鉴 RAG 与人类的情景/语义记忆 |
| `AutoMolecule3D` | 分子建模与性质预测 | 提出分子建模与性质预测方法，走几何深度学习路线，需在「分组表示」类方法的精度/开销权衡上做改进 |
| `AutoPower` | 电力系统潮流估计 | 输入是电网图（母线分 PQ/PV/Slack，附带拓扑与量测量），提出潮流估计方法，可用图神经网络 |
| `AutoTPPR` | 单细胞多组学扰动预测 | 建立生成式能量框架（GEARS），联合建模 scRNA-seq 与 scATAC-seq，预测转录组层面的扰动响应 |
| `AutoTTRL` | 测试时强化学习 | 提出测试时 RL 框架，让 LLM 不做全量微调、只用有限算力和数据适配下游任务 |
| `AutoTTS` | LLM 推理与测试时扩展 | 改进测试时扩展方法：推理框架设计、状态表示、计算效率；可参考 CoT / ToT / GoT 这类结构 |

数据集与环境准备每个任务都不一样，上游的原话是「以各任务目录里的代码为准」。另注意
`AutoTTS` 不是语音合成——它 `prompt.json` 里的 `domain` 写的是 LLM 推理与测试时扩展。

**论文复现任务（`sci_tasks`）** 是另一种模式：交给 agent 一篇已发表论文和它用到的数据，
让它自己写分析代码、跑起来、就着报错迭代、最后交一份报告。这部分内容不在本仓库里，而是
一个指向 [ResearchClawBench](https://github.com/InternScience/ResearchClawBench) 的
**submodule**（`git ls-files` 只会看到一个 gitlink），要 `git submodule update --init sci_tasks`
才有东西；默认任务是 `Astronomy_000`。指南 [docs/sci_tasks.md](docs/sci_tasks.md) 是英文的。

**四个开箱脚本**（`scripts/`，Linux 入口；Windows 上请用 `python internagent_cli.py`）：

| 脚本 | 用途 |
| --- | --- |
| `run_discovery.sh` | 跑完整的发现流程（生成想法 → 做实验） |
| `run_skip-idea.sh` | 跳过想法生成，从已有的想法文件直接开始做实验 |
| `run_sci.sh` | 跑一个论文复现任务（默认 `Astronomy_000`） |
| `run_qa.sh` | 用深度调研回答一个研究问题 |

### 上游动态（中译）

下面 7 条译自本文上方的 `## 🔥 News`，日期、链接和加重的位置都按原样保留；要引用请以英文原文为准。

- **2026.5.07**：🔥🔥 开源 InternAgent-1.5，带来增强后的算法发现任务、科学论文自主复现、记忆模块，以及面向复杂科研难题的深度调研（deep research）等进阶能力。
- **2026.3.17**：🚀🚀 对外开放 InternAgent 深度调研能力的[入口](https://scphub.intern-ai.org.cn/detail/28)，让开发者和研究者把这套深度调研功能接进自己的工作流。
- **2026.2.14**：❤️‍🔥❤️‍🔥 开源 **[MLEvolve](https://github.com/InternScience/MLEvolve)**，即 InternAgent 中负责算法设计任务解优化的核心子系统实现。作为拿到 **MLEBench #1** 的**开源方法**，它展示的是在有界假设空间内的解优化能力。
- **2026.2.10**：🔥 [InternAgent 1.5 技术报告](https://huggingface.co/papers/2602.08990) 正式发布。1.5 在 **GAIA、HLE、GPQA、FrontierScience** 等科学推理基准上取得领先表现，并支持横跨**物理、生物、地球科学与生命科学**的端到端自主科学发现任务——算法发现和实证发现（干/湿实验）都能做。
- **2025.10.13**：InternAgent-1.0 代码全面开源，在 12 类科研任务上支持端到端自动化与自主演化。

<details>
<summary>更早两条……</summary>

- **2025.07.17**：InternAgent 源代码部分开源。覆盖自主科研 12 类任务的完整版本即将开源。本仓库可用于全周期的自主科学研究，从假设生成一直到实验自动执行。
- **2025.07.10**：*NovelSeek* 更名为 **InternAgent**。这个名字承载了我们对这套自主科研框架的期待——希望它能帮助所有研究者做出重大科学发现。

</details>

### 来源

基于 `InternScience/InternAgent` 的提交 `fa8c3eedfa9751d3752ea6eb49220b303ac2397d`
（2026-07-29），Apache License 2.0——这里的 `LICENSE` 是上游原件，未作改动。按 §4(b)
的要求，上面的说明交代了改动范围：`internagent/mas/` 下 10 个文件被编辑（15 处补丁，
逐条列在 `docs/cli.md`），另有 `.gitignore` 和本 `README.md`；表格里那一列全是新增。
这是一个独立的 Windows 移植，不是上游团队的发布；论文链接、徽章和 README 的其余部分
归他们所有。

### 致谢

**上游。** 框架本身、`tasks/` 下每一个任务实现、`sci_tasks` /
[ResearchClawBench](https://github.com/InternScience/ResearchClawBench) 论文复现集，以及
[MLEvolve](https://github.com/InternScience/MLEvolve)，都是
[InternScience](https://github.com/InternScience) 团队的工作；作者名单见文末的 Citation
一节，项目本身见[官网](https://discovery.intern-ai.org.cn)和
[HuggingFace 合集](https://huggingface.co/collections/InternScience/internagent)。这个分支
和上游没有隶属关系，也不是他们的官方发布——移植里出的 bug 都算这里的。

**本分支。** 由 [@leijunyu1216-collab](https://github.com/leijunyu1216-collab) 维护，
Windows 上跑不通欢迎直接开 issue。

**是怎么做出来的。** Windows 移植、CLI、实时显示层和离线测试这一整套，是和 AI 编程助手
（Qoder）一起写出来的。所有提交都以维护者的账号署名并推送，代码也在真终端里跑过、验过，
但要说成「一个人独立写完的」就不准确了。

---

## 🔥 News
- **2026.5.07**: 🔥🔥 We have open-sourced InternAgent-1.5 with advanced features, including  enhanced algorithm discovery tasks, autonomous scientific paper reproduction, memory module, and deep research for tackling complex research challenges.

- **2026.3.17**: 🚀🚀 We provide public [access](https://scphub.intern-ai.org.cn/detail/28) to InternAgent's Deep Research capabilities, enabling developers and researchers to seamlessly integrate its advanced deep research functionality into their own workflows.

- **2026.2.14**: ❤️‍🔥❤️‍🔥 We open-source **[MLEvolve](https://github.com/InternScience/MLEvolve)**, the core implementation of InternAgent's solution optimization subsystem for algorithm design tasks. As the **open-source method** to achieve **#1 on MLEBench**, MLEvolve demonstrates powerful capabilities in solution optimization within bounded hypothesis spaces.

- **2026.2.10**: 🔥 Official release of the [InternAgent 1.5 Technical Report](https://huggingface.co/papers/2602.08990). InternAgent 1.5 achieves leading performance on scientific reasoning benchmarks including **GAIA, HLE, GPQA, and FrontierScience**, and supports end-to-end autonomous scientific discovery tasks across **Physical, Biology, Earth, and Life Science domains**, enabling both algorithm discovery and empirical discovery (dry/wet-lab experiments).

- **2025.10.13**: InternAgent-1.0 code has been fully open-sourced, supporting end-to-end automation and autonomous evolution across 12 scientific research tasks.

<details>
<summary>more...</summary>

- **2025.07.17**: The source code of InternAgent has been partially open-sourced. The complete version of InternAgent (covering 12 types of tasks for autonomous scientific research) will be open-sourced soon. This code repository can be used for full-cycle autonomous scientific research, ranging from hypothesis generation to automated experimental execution.

- **2025.07.10**: *NovelSeek* has been renamed to **InternAgent**. This change embodies our hopeful vision for autonomous scientific research framework, and we hope it will empower all researchers to achieve great scientific discoveries.

</details>

---

## 🚀 Getting Started

### Installation

```bash
conda create -n InternAgent python=3.11
conda activate InternAgent
pip install -r requirements.txt
```

> That is upstream's Linux path. On Windows, `requirements.txt` cannot install (the
> `triton` / `nvidia-*` lines) — use [Install and run](#install-and-run) instead.

### Command-line Assistant

The wizard is described at the top ([Windows-native port](#windows-native-port-plus-one-terminal-command));
this section is the non-interactive side — the same command, no prompts:

```bash
python internagent_cli.py            # or: internagent run
python internagent_cli.py run "your research question" \
  --provider deepseek --model deepseek-reason --api-key-env DEEPSEEK_API_KEY
python internagent_cli.py doctor     # check deps, encoding, config, local patches, then:
python tests/cli/run_all.py          # verify the whole display layer offline (no key, no network)
```

Passing the question on the command line skips the wizard entirely: every step
resolves from a flag or from the saved config, and anything missing is reported
by name instead of being asked halfway through. The key only comes from an
environment variable — never from argv, where it would land in shell history and
the process list.
See [docs/cli.md](docs/cli.md) for the REPL commands, the environment knobs, the
fifteen source edits this CLI depends on, and Windows terminal notes. The sections
below cover the underlying launchers, which
take configuration from `.env` and `config/` instead.

### Configure API Keys

Rename `.env.example` to `.env` and fill in your API keys:

```bash
mv .env.example .env
```

Key fields in `.env`:

```
OPENAI_API_KEY=        # OpenAI or compatible API key (used for embeddings and memory)
OPENAI_API_BASE_URL=   # Base URL for OpenAI-compatible endpoints
OPENROUTER_API_KEY=    # OpenRouter API key (when using the openrouter provider)
ANTHROPIC_API_KEY=     # Anthropic API key (for Claude-based experiment backends)
```

To use OpenRouter as the model gateway, set `OPENROUTER_API_KEY` and run with
`config/openrouter_config.yaml`. See [docs/openrouter.md](docs/openrouter.md)
for setup details.

### Run a Discovery Experiment

`AutoDebug` is a self-contained toy task (no dataset or model downloads required) and is the recommended first run to verify your setup.

```bash
python launch_discovery.py \
    --config ./config/default_config.yaml \
    --task AutoDebug \
    --exp_backend claudecode
```

### Run a QA Query

QA mode uses InternAgent's deep research pipeline to answer a research question directly — no experiment loop, just a synthesized answer grounded in literature.

```bash
python launch_qa.py --question "What are recent advances in memory-augmented LLMs?"

# Optionally save the answer to a file
python launch_qa.py -q "What are recent advances in memory-augmented LLMs?" -o answer.md
```

### Master Launcher

`launch.py` is a unified entry point for both modes:

```bash
python launch.py --mode discovery --task AutoDebug --exp_backend claudecode
python launch.py --mode qa --question "What are recent advances in memory-augmented LLMs?"
```

**Configuration tips:**
- Configuration lives in `config/` — `default_config.yaml` is the main starting point.
- Results are saved under `results/`, logs under `logs/`.
- To skip idea generation and run experiments from existing ideas: add `--skip_idea_generation --idea_path <path/to/ideas.json>`.
- See `scripts/` for ready-to-use example scripts.

### Research Tasks

**Algorithm discovery tasks** live under `tasks/`. Each contains a `prompt.json` (task description), baseline `code/`, and a `launcher.sh`. Datasets and environment-specific setup vary per task — refer to the code in each task folder.

**Scientific paper reproduction tasks** (`sci_tasks`) are a distinct mode where InternAgent is given a published paper and its data, and asked to reproduce the key findings autonomously. These tasks live under `sci_tasks/tasks/` (from the [ResearchClawBench](https://github.com/InternScience/ResearchClawBench) benchmark). See [docs/sci_tasks.md](docs/sci_tasks.md) for a full guide.

### Quick-Start Scripts

Ready-to-use example scripts are provided under `scripts/`:

| Script | Description |
|---|---|
| `run_discovery.sh` | Run a full discovery experiment (idea generation → experiments) |
| `run_skip-idea.sh` | Run experiments from an existing idea file, skipping generation |
| `run_sci.sh` | Run a scientific paper reproduction task (defaults to `Astronomy_000`) |
| `run_qa.sh` | Answer a research question via deep research |

---

## 🔧 Advanced Features

### Memory Module

InternAgent 1.5 includes a persistent memory module that records experiment outcomes across sessions, helping the agent avoid previously failed directions and build on successful ones. See [docs/memory_module.md](docs/memory_module.md) for configuration and setup.

### Deep Research

The Deep Research (DR) module decomposes a research question into subtasks, gathers information from academic databases and the web in parallel, and synthesizes findings into a direct answer or structured report. See [docs/deep_research.md](docs/deep_research.md) for configuration and setup.

---

## 📝 Citation

```bibtex
@article{feng2026internagent,
  title={InternAgent-1.5: A Unified Agentic Framework for Long-Horizon Autonomous Scientific Discovery},
  author={Shiyang Feng and Runmin Ma and Xiangchao Yan and Yue Fan and Yusong Hu and Songtao Huang and Shuaiyu Zhang and Zongsheng Cao and Tianshuo Peng and Jiakang Yuan and Zijie Guo and Zhijie Zhong and Shangheng Du and Weida Wang and Jinxin Shi and Yuhao Zhou and Xiaohan He and Zhiyin Yu and Fangchen Yu and Bihao Zhan and Qihao Zheng and Jiamin Wu and Mianxin Liu and Chi Zhang and Shaowei Hou and Shuya Li and Yankai Jiang and Wenjie Lou and Lilong Wang and Zifu Wang and Jiong Wang and Wanghan Xu and Yue Deng and Dongrui Liu and Yiheng Wang and Wenlong Zhang and Fenghua Ling and Shufei Zhang and Xiaosong Wang and Shuangjia Zheng and Xun Huang and Siqi Sun and Shuyue Hu and Peng Ye and Chunfeng Song and Bin Wang and Conghui He and Yihao Liu and Xin Li and Qibin Hou and Tao Chen and Xiangyu Yue and Bin Wang and Liang He and Dahua Lin and Bowen Zhou and Bo Zhang and Lei Bai},
  journal={arXiv preprint arXiv:2602.08990},
  year={2026}
}
```

```bibtex
@article{team2025internagent,
  title={InternAgent: When Agent Becomes the Scientist--Building Closed-Loop System from Hypothesis to Verification},
  author={Team, InternAgent and Zhang, Bo and Feng, Shiyang and Yan, Xiangchao and Yuan, Jiakang and Ma, Runmin and Hu, Yusong and Yu, Zhiyin and He, Xiaohan and Huang, Songtao and others},
  journal={arXiv e-prints},
  pages={arXiv--2505},
  year={2025}
}
```

```bibtex
@article{hu2025flowsearch,
  title={FlowSearch: Advancing deep research with dynamic structured knowledge flow},
  author={Yusong Hu and Runmin Ma and Yue Fan and Jinxin Shi and Zongsheng Cao and Yuhao Zhou and Jiakang Yuan and Xiangchao Yan and Wenlong Zhang and Lei Bai and Bo Zhang},
  journal={arXiv preprint arXiv:2510.08521},
  year={2025}
}
```

```bibtex
@article{du2025automlgen,
  title={AutoMLGen: Navigating Fine-Grained Optimization for Coding Agents},
  author={Shangheng Du and Xiangchao Yan and Dengyang Jiang and Jiakang Yuan and Yusong Hu and Xin Li and Liang He and Bo Zhang and Lei Bai},
  journal={arXiv preprint arXiv:2510.08521},
  year={2025}
}
```

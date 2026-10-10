# InternAgent-1.5: A Unified Agentic Framework for Long-Horizon Autonomous Scientific Discovery
> *Autonomous Discovery Across All Sciences*
- **Papers**: [InternAgent 1.0](https://arxiv.org/abs/2505.16938) | [InternAgent 1.5](https://huggingface.co/papers/2602.08990)
- **Links**: [Website](https://discovery.intern-ai.org.cn) | [HuggingFace](https://huggingface.co/collections/InternScience/internagent)


---

## Windows-native port, plus one terminal command

Upstream [InternScience/InternAgent](https://github.com/InternScience/InternAgent) is a
Linux project. Not in a philosophical way: every documented entry point is a POSIX
shell script (`scripts/`: `run_qa.sh`, `run_discovery.sh`, `run_sci.sh`,
`run_skip-idea.sh`, plus 119 more `.sh` files — a `launcher.sh` per task under
`tasks/`), and `requirements.txt` carries 15 lines —
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

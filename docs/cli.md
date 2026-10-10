# InternAgent CLI

`internagent_cli.py` 是仓库自带的一体化入口：交互式向导、实时进度界面、多轮 REPL、
依赖自检。所有密钥只在运行时输入，只存在于当前进程，不写进任何文件。

## 安装

```bash
conda create -n internagent python=3.11 -y
conda activate internagent
pip install -r requirements-qa-windows.txt   # qa/深度调研链路
pip install rich prompt_toolkit              # CLI 界面（rich 必需，prompt_toolkit 用于中文输入）
```

无需安装脚本即可直接使用：

```bash
python internagent_cli.py          # 等价于 internagent run，进入向导
```

想让它变成全局命令，把下面两行按需放进 PATH（launcher 里设 `PYTHONIOENCODING=utf-8`，
否则中文终端下界面字符会崩）：

```sh
# ~/.local/bin/internagent
#!/bin/sh
PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}" exec python /path/to/InternAgent/internagent_cli.py "$@"
```

Windows 下同时放一个 `internagent.cmd`，Git Bash 只认无后缀那个。

## 向导（`internagent` / `internagent run`）

七步：供应商 → base_url → 模型名 → API key → 运行模式 → 本地文件（可选）→ 研究问题，
每步标题右侧标着 `n/7`。

- **有得选的用方向键**：`↑↓` 移动、`PgUp/PgDn` 翻页、`Home/End` 到两端、回车确认，
  不超过 9 项时直接按数字也行。选完整个列表就收起来，滚动区只留一行
  `✓ 模型供应商  deepseek  官方端点…`；右边那列 dim 的字是每个供应商的说明
  （base_url 是不是预设好的、要不要代理）。`Esc` / `Ctrl+D` 放弃并退出向导。
- **PowerShell / cmd 走方向键，Git Bash 退回数字**：方向键要读 Win32 控制台缓冲
  区，MSYS 的 pty 不提供（同一个原因让 prompt_toolkit 在那里也接不上），这时会打印
  整列 `1 …`，输入序号、或者直接输入名字都可以。stdin 是管道时同样走这条路径。
- **要自己填的三项**（base_url / 模型名 / 研究问题）画成同一副样子，只是光标后面跟
  的是你的输入。base_url 的默认值是该供应商的预设地址（换了供应商就换回预设），模型
  名的默认值是上次记下的那个，直接回车即接受；问题没有默认值，空回车只会再问一次。
- key 以 `•` 回显；留空时会问是否用剪贴板里那串。选完先打一次 `/models` 校验 key 与
  base_url 是否匹配，避免跑了一分钟才发现配错。
- **模型名打错会被当场纠出来**：`/models` 的返回里没有它时，列出端点真正提供的模型
  让你选（上百项时输入字母过滤），最后一项是 `仍然用 <你输入的名字>`，`Esc` 就是保留
  原输入——有些端点不老实报 `/models`，不能因此拦掉一次本来能跑的运行。
- 模式那一步把三档的差别写在第二列（节点上限、是否逐节润色、谁还开着需要企业 key 的
  `volc_search`），因为这个选择直接决定花多少钱。
- 七步填完、初始化工作流之前有一张「准备开始」确认卡：供应商 / 模型 / 模式 / 预算 /
  单轮上限 / 附件。回车开始，`m` 换一个模型，`q` 退出——一轮深度调研是真金白银，而
  三个成本乘数很容易被忘掉。脚本化运行没有这一步（没人看着它）。
- 供应商/base_url/模型/模式/超时写入 `~/.internagent.json`，**不含 key**。
  下次不带 `--timeout` 时会沿用上次的值（`--timeout` 显式给了则以命令行为准）。
- 每轮有墙钟上限（默认 600s）。到点不再干等：界面请求取消当前模型调用，最多再等
  10s，然后把这一轮停下来并重建工作流，部分思考仍能用 `/think` 翻出来。卡住的线程
  是 Python 杀不掉的（它堵在 socket 读上），所以它是 daemon、随进程退出。
  `qa`/`chat` 的默认上限是 300s。
- 附件那一步交给工作流的 `extract_document_content`：txt / json / xml / py /
  xls(xlsx) / docx / pdf 都能读（docx 走 docx2markdown，pptx 需要 `unstructured`，
  图片和音频要求模型支持多模态）。路径打错会重问，回车跳过；从资源管理器复制出来
  的路径两端带引号、结尾带 `\`，都会被去掉。确认行上只印文件名，目录单独一行印在
  它下面（一列宽度放不下长路径，砍掉哪半都没法核对）。带上之后每轮追问都带着它。
  txt 按 utf-8 读，GBK 文本请先转码。
- 之后进入 REPL，一个问题接一个问题地问，工作流只初始化一次。

### 脚本化：给了问题就一个问题都不问

`internagent run "问题"` 会把向导整体跳过——每一步从参数或 `~/.internagent.json`
取值，取不到就报错并点名缺哪个参数（不会半路开始问人），跑完这一问照样进入 REPL，
`exit` / Ctrl+D 退出。key 只能从环境变量来：命令行上的 key 会进 shell history 和
进程列表。

```bash
internagent run --timeout 900 --record-events logs/today.jsonl
internagent run "近五年 GNN 在药物发现中的进展？" \
  --provider deepseek --model deepseek-reason --api-key-env DEEPSEEK_API_KEY
internagent run --file 材料.pdf                    # 跳过附件那一步
```

`--provider/--base-url/--model/--mode/--file` 各自只影响对应的那一步：给了就跳过，
没给照旧提问。`--provider` 不给 `--base-url` 时用该供应商的预设地址（`custom` 没有
预设，会要求你给）。脚本化运行时模型名不在端点返回的列表里会直接失败——没人盯着一
行警告去改参数，把额度花在一个不存在的模型上更糟。

`run` 之外的子命令不读 `~/.internagent.json`（那只是向导的默认值），要显式给参数：

```bash
internagent qa "近五年 GNN 在药物发现中的进展？" --provider deepseek --model deepseek-reason -o answer.md
internagent chat --provider deepseek --model deepseek-reason      # 不带向导的多轮 REPL
```

### 成本闸门：`--max-workers` / `--max-tool-calls` / `--max-layers`

一道题会花多少次模型调用，是三个数相乘：**同时跑的节点数 × 每个子任务的工具轮数 ×
图层数**。上游的默认值（`config_qa.yaml`：并发 10、每子任务 5 轮、图层 5）是按服务端
吞吐写的，不代表个人 key 扛得住——并发 10 最常见的结果是 429，然后模型看到一堆失败的
工具调用、换个参数再试一次。向导的确认卡上会把当前生效的三个数打出来，参数能覆盖它们：

```bash
internagent run "问题" --max-workers 3 --max-tool-calls 2 --max-layers 3
```

三个都是 `run`/`qa`/`chat` 共用的参数，只改你给的那一项，`config_*.yaml` 里同段的其它
键（`enabled_tools`、`need_memory`、分段模型名…）原样带上——DRAgent 合并配置用的是浅
`dict.update`，只发一个键会把整段抹掉，这一点由 `tests/cli/test_config.py` 盯着。不改
文件也就不会影响别人 clone 下来的默认值。

## REPL 命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 命令列表 |
| `/think` | 列出本轮的模型思考；`/think 3` 展开第 3 条全文，`/think 3 800` 只看前 800 字 |
| `/usage` | 分阶段的 token 用量（只有端点上报用量时才有数字，不估算） |
| `/refs [条数]` | 列出上一轮引用的参考文献（给数字只看前 N 条，`/refs 0` 全部） |
| `/carry [on\|off]` | 让下一轮带上上一轮的结论作背景（默认关，见下） |
| `/answer` | 重新打印上一轮回答（长回答会滚出屏幕；别名 `/last`） |
| `/save [路径]` | 把上一轮回答写文件，默认 `answers/answer_<时间>_<问题前 40 字>.md`，路径里可用 strftime 模板 |
| `/retry` | 重跑上一个问题（连同当时实际发出的内容，即 `/carry` 拼好的那份） |
| `/verbose` `/quiet` | 日志打到终端 / 只打文件 |
| `/clear` | 清屏 |
| `exit` `quit` `q` | 退出（Ctrl+D 同义） |

**追问不是接着上文说的。** 每轮都是一次独立的研究运行：工作流按新问题重新拆解
任务，默认看不到上一轮的回答，所以「第二点再展开」会被当成一个全新题目。想让下一轮
接着这轮走，先 `/carry`（再 `/carry off` 关掉）——它把上一轮回答的前
`CLI_CARRY_CHARS` 字作为背景拼在新问题前面，并在发送前打印带了多少字。代价是这一
轮的 prompt 多这些字，长报告拼进去并不划算，默认关。

## 实时界面看什么

```
● 规划任务分解（3 个子任务）        ← 里程碑，永久留在滚动区
  1. 检索近五年 GNN 药物发现综述
◆ 全局规划 生成执行图 · 第 3/10 轮  41.7s · 23003 字 · #12   ← 一次模型调用
    │ ……（结论段，前 400 字折叠）    ← 思考摘要，全文在归档里
⠹ 2 · 子任务 1  web_search(query=…)  12.4s · 3 次工具调用   ← 底部实时区
已用 1m23s · 9 次模型调用 · 14 次工具调用 · 其中 4 次是重复调用 · 8200 tokens · Ctrl+C 中断
```

- **思考默认折叠**：屏幕上只留结论段，`/think N` 或 `logs/think_<日期>.md` 看全文。
  折叠只影响终端输出，不影响模型上下文，也不省 token。
- **Ctrl+C 中断当前一轮**：工作流被真正取消后可以接着问；若上游卡在 socket 读上
  退不出来，CLI 会重建工作流并告诉你，而不是让后续每轮都报「图正在执行中」。
- **静默 30s** 会在底部提示可能卡在哪（没发出调用 / 有调用但没回字符），因为这时
  界面看起来和挂掉一模一样。
- **同一个节点用完全相同的参数调同一个工具到第 3 次**，会立刻在滚动区点名警告
  （`CLI_REPEAT_WARN` 调阈值）。这是跑失控的常见形状：检索没拿到东西、模型原样再
  发一次，直到预算耗尽。每轮小结里也带着多少次是重复的。执行器现在会在子任务内部把
  同参数调用直接合并（见「本地补丁」第 10 条），所以界面上还数得出的重复，多半跨了
  子任务或跨了节点——那是上一层在拿同一个问题反复问。
- **每一轮开头回显一行 `问 …`**：实时区跑完就清掉了，长回答又会长到滚出屏幕，
  滚动区里总得留下这一问是谁。退出时给一行会话总结（轮数 · token · 思考归档路径）。

## 其他子命令

```bash
internagent check              # 验 key、列模型
internagent qa "问题" -o a.md  # 单发，不进 REPL
internagent doctor             # 依赖/编码/可写/配置自检；--selftest 顺带跑离线界面测试
internagent replay logs/x.jsonl [--speed 0]  # 回放录制的事件流，不花额度
python tests/cli/run_all.py    # 离线界面自检：不联网、不需要 key
```

`doctor` 会检查：输出编码能否放下界面字符、rich/prompt_toolkit、requirements 文件里
声明的包、`logs/` 能否写、配置里有没有混进密钥、**已有日志里有没有密钥形状的内容**
（提 issue 前把 `logs/` 附上去之前先看这一行）、`internagent` 是否在 PATH，以及下面
「本地补丁」一节里的每一处改动是否还在位（这一项只 warn 不算失败，因为干净的上游
clone 本来就没有它们）。

`python tests/cli/run_all.py` 要在装好依赖的那个解释器里跑。用 Anaconda 的话，
`python` 常常是 base 环境，缺 `loguru`/`tiktoken`，doctor 那套会假红：

```bash
PYTHONIOENCODING=utf-8 D:/Anaconda/envs/internagent/python.exe tests/cli/run_all.py
```

## 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `CLI_THINK_CHARS` | `400` | 屏幕上每条思考最多显示多少字；`0` 全部折叠 |
| `CLI_STALL_SECS` | `30` | 静默多久后打印卡住提示 |
| `CLI_REPEAT_WARN` | `3` | 同一节点同一工具用同样参数调用到第几次时警告 |
| `CLI_STREAM` | `1` | `0` 让模型钩子只观察：不把非流式调用重发成流式，也就没有逐字尾巴 |
| `CLI_EVENT_MAX_STR` | `2000` | 录制 JSONL 里单个字符串截断长度 |
| `CLI_EVENT_MAX_THINK_STR` | `100000` | 模型思考/回答文本的截断长度，比上面那个大得多 |
| `CLI_EVENT_MAX_ITEMS` | `50` | 录制 JSONL 里列表截断长度 |
| `CLI_CARRY_CHARS` | `3000` | `/carry` 最多把上一轮回答的前多少字作为背景 |
| `CLI_BANNER_COLOR` | `#4EC9B0` | 启动 logo 的颜色，向导的 `›` 光标和 `✓` 用同一个 |

## 本地补丁

上游是 Deep Research 的参考实现，模型层和几个循环按论文作者的环境写死了。下面十三处
在 `internagent/mas/` 下的**被跟踪文件**里，目前还没有提交，所以 `git pull` 冲突时可
能被动摇，`git checkout .` 会直接抹掉。`doctor` 逐项查它们在不在（提示行里给出被抹掉
后的后果，好认），每一处的形状如下：

1. `models/__init__.py` — `get_model()` 原来只认 `deepseek*`/`gpt*`/`Qwen*`/
   `intern*`/`gemini*` 这些前缀，其它一律 `Unsupported model`。补一个兜底分支：设了
   `OPENAI_API_BASE_URL` 就走 `OpenAIModel`。
   少了它：任何第三方兼容端点（Kimi、GLM、通义的 OpenAI 模式…）配好 key 也起不来。
2. `workflow/main.py` 的 `self.analysis_model` — 原来写死 `get_model("o4-mini")`，
   改成读 `model.default_model`。
   少了它：前面全部正常，一进「查询分析」这步就报模型不存在，而这一步在最开始。
3. `workflow/main.py` 的图层循环 — `cnt > max_execution_layers` 原先只检查图里有没有
   answer 节点，没有就什么都不做地穿过去，下一层继续执行。开了 coordinator（complex
   模式）时协调者会往图里加节点，ready 集合永远不空，于是每层每个执行 agent 各发
   `max_tool_calls` 次工具调用，无限下去。补的是在检查之后 `break`，落到
   `get_final_answer()`，用已研究到的部分收尾。
   少了它：**「工具不停调用」最主要的成因**，且不会自己停。
4. `tools/tool_integration.py` 的网页摘要 — 原来写死 `gpt-4o-mini`，改成
   `os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")`，与 `info_processing_tools.py`
   里已有的约定一致。
   少了它：`url_processor` 一被调用就要求一个第三方端点上通常不存在的模型名，表现为
   「检索到了网页但读不出内容」。
5. `config_qa.yaml` 的 `volc_search` — 五处 enabled_tools 里把它注释掉。它是火山引擎
   的企业搜索，需要单独的 `VOLC_SEARCH_API_KEY`。
   少了它：工具列表里挂着一个没有 key 的工具，模型选中它时那一轮就白跑。
6. `agents/global_planner_agent.py` 的规划循环 — 内层 `while idx <= max_iter` 在
   `max_retries` 次调用全失败（例如 401）后带着 `response=None` 退出，此时下面的
   `continue`/`break` 都不可达，外层 `while True` 于是每圈重新发 `max_retries` 次
   请求，永远不停。补一个 `else: break`。
   少了它：key 错了不像报错，像卡死——实测四分钟里发了 5255 个 401。
7. `agents/global_planner_agent.py` 的环检测 — 建图失败（计划里有环）原先带着
   `graph=None` 直接 `continue`：外层 `while True` 没有上限，而下一轮发给模型的
   「当前图」变成了字符串 `"null"`，模型因此更容易给出带环的计划，自己喂自己。
   现在是回到最初的种子计划、最多重建两次，仍然不行就按失败收尾；字段名不对
   （小模型爱写 `id`/`source`）导致建图抛错的，同样算一次无效计划而不是让整轮崩掉。
   少了它：一个反复给出带环计划的模型能让规划阶段永远跑不完，界面上只看到思考
   一直在滚。
8. `agents/global_planner_agent.py` 开头的 `self.graph = None` — REPL 里工作流只
   初始化一次，规划器对象是复用的，而上一问的图一直挂在 `self.graph` 上。
   少了它：这一问规划失败时 `main.py` 拿到的还是上一问的图，于是把旧问题重新研究了
   一遍，答案看着完全自洽。
9. `workflow/main.py` 取图那一行 — 规划失败时 `graph` 是 None，下一句
   `graph.to_dict()` 抛 `AttributeError`，而这发生在工作流线程深处。
   少了它：屏幕上只有「工作流失败」，真正的原因（规划没产出图）埋在日志里。
10. `agents/task/execution_agent.py` 的工具循环 — 同一个子任务里，模型把完全相同的
    `(工具, 参数)` 再发一次时以前会真的再打一次网络请求，一次不落直到预算用完。现在
    第一次的结果按参数指纹缓存，重发的直接拿缓存；某一轮如果全是从缓存里拿的，就地
    结束这个子任务（模型没有新想法了，剩下的预算只是烧钱）。缓存以子任务为界，换个
    子任务重新计。
    少了它：**这就是「工具不停调用」最常见的那一种**——检索没结果，模型原样再发一次。
11. `agents/task/execution_agent.py` 的 `_call_function_tool` — 工具函数抛异常时原先
    把 `"Error executing X: …"` 这个**字符串**当作工具结果返回，调用方只看「函数有没有
    返回」来记 success，于是失败被记成成功，框架里那条「这一批全失败就停」的分支永远
    进不去。改成往上抛，交给本来就按失败处理的那一层。
    少了它：一个坏掉的工具能撑满整个预算，而模型收到的「结果」是一句错误文案。
12. `agents/task/planner_agent.py` 的兜底拆分 — 节点内拆分失败时返回写死的 4 个子任务，
    完全不看 `max_subtasks`（qa 配置里是 2），那 4 个还都是「搜索相关信息」这类笼统
    目标。改成按 `max_subtasks` 截断。
    少了它：端点越出问题，工具调用反而比顺利时更多。
13. `mas/agents/dr_agent.py` 的兜底文本 — 工作流抛异常时返回的那句话里带上真正的原因。
    少了它：规划失败、key 不对、工具崩了，屏幕上都是同一句「DR workflow execution
    failed」。

其中 3、6、7、10、11 这几处与 `--timeout`、重复调用警告是两回事：那两道是止损，
它们才是让流程本来就该停。`python tests/cli/run_all.py` 里的 `loops` 一套直接驱动真实
的 `ExecutionAgent`/`GlobalPlannerAgent`（假模型、会计数的假工具），所以这些循环是被
行为验证的，不是只靠肉眼读代码；那一套需要装好 requirements 里的依赖，缺了就整体跳过
而不是报错。

## 密钥会去哪里

向导输入的 key 只进当前进程的环境变量，`~/.internagent.json` 里不存。但一个会把
key 回显在 401 响应体里的端点，仍然可能让它顺着日志落盘，所以有两个边界：

- `cli_events.redact()` 套在所有事件的字符串字段上（先脱敏再截断，跨截断点的
  key 不会留下前半段），因此 `--record-events` 的 JSONL、`logs/think_*.md`、
  实时界面拿到的都是 `api_key: ***`。
- 同一个函数包成 `logging.Filter` 挂在文件 handler 上。挂 handler 而不是
  logger，是因为 logger 自己的过滤器收不到子 logger 冒泡上来的记录；workflow
  除了 CLI 的 `logs/cli_<date>.log`，还会自建主日志、每个节点一个日志和
  `flowsearch_<task>.log`，这些都在 `Logger.addHandler` 上被一并挂好。

`redact()` 认三种形态：`api_key/access_token/secret/authorization` 后跟 `:` 或 `=`
的值、`sk-` 前缀的裸 key、`Bearer <token>`。方案名会保留（`Bearer ***`），因为
它说明 key 是从哪个头发出去的。屏幕上的原文同样被脱敏——想确认 key 与 base_url
是否配对，用 `internagent check`：它只发一次 `/models` 请求，成功后只打印模型名。

## Windows 注意

- 中文输入（问题、路径）在 cmd/PowerShell 里靠 prompt_toolkit 才能正常用输入法；
  Git Bash 没有 Win32 控制台缓冲区，会自动退回 `input()`，向导的选择列表同时退成
  数字那条路径（方向键靠同一个控制台缓冲区，读不到）。
- `PYTHONIOENCODING=utf-8` 必须设，否则 `›`、`✓`、`•`、`░` 这类字符在 GBK 控制台直接
  抛 `UnicodeEncodeError`（这四个都不在 GBK 里）。launcher 脚本里已经设了。
- 脚本化时用 `--api-key-env`，或按行喂 stdin：一行一个值，顺序就是标题上标的
  `1/7…7/7`（附件那一步给空行即跳过），确认卡再给一个空行表示回车开始，最后一行
  `exit` 收尾。喂 stdin 时走的是数字路径，界面装饰不会因此减少，只是不再等键盘。

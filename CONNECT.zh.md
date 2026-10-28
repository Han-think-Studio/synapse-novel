[English](CONNECT.md) · [한국어](CONNECT.ko.md) · **中文**

# 连接你已有的 AI

Synapse Novel 为每一拍准备写作请求，并检查创作者或其 AI 返回的文本。Synapse 不会调用模型或网络。创作者在终端确认值、确认计划并锁定节拍。

## 准备注册文件夹

选择一个现有的专用文件夹并注册为 `SYNAPSE_NOVEL_ROOT`。项目以及传给 MCP 的所有路径（包括种子和计划文件）都必须位于该文件夹内。不要注册磁盘根目录。

### Linux

在 checkout 根目录运行：

~~~sh
python3.12 -m venv .venv
.venv/bin/python -m pip install "PyYAML>=6,<7"
mkdir -p "$HOME/synapse-novel-work"
~~~

解释器路径为 `<checkout>/.venv/bin/python`。

### Windows PowerShell

在 checkout 根目录运行：

~~~powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install "PyYAML>=6,<7"
New-Item -ItemType Directory -Force "$env:USERPROFILE\synapse-novel-work"
~~~

解释器路径为 `<checkout>\.venv\Scripts\python.exe`。

两个系统的服务器脚本均为 `<checkout>/editions/novel/synapse_novel_mcp.py`（Windows 请使用反斜杠）。将 `<checkout>` 和注册文件夹替换为实际路径；路径包含空格时请加引号。服务器通过本地 stdio 运行。

## 注册服务器

### Claude Code

Linux：

~~~sh
claude mcp add --env SYNAPSE_NOVEL_ROOT="$HOME/synapse-novel-work" synapse-novel -- <checkout>/.venv/bin/python <checkout>/editions/novel/synapse_novel_mcp.py
~~~

Windows PowerShell：

~~~powershell
claude mcp add --env "SYNAPSE_NOVEL_ROOT=$env:USERPROFILE\synapse-novel-work" synapse-novel -- "<checkout>\.venv\Scripts\python.exe" "<checkout>\editions\novel\synapse_novel_mcp.py"
~~~

### Claude Desktop

在 `claude_desktop_config.json` 中添加以下对象，并将值替换为相应系统的解释器、服务器脚本和现有注册文件夹：

~~~json
{
  "mcpServers": {
    "synapse-novel": {
      "command": "<python>",
      "args": ["<server-script>"],
      "env": { "SYNAPSE_NOVEL_ROOT": "<novel-root>" }
    }
  }
}
~~~

Linux 的 `<python>` 为 `<checkout>/.venv/bin/python`；Windows 为 `<checkout>\.venv\Scripts\python.exe`。`<server-script>` 指向该 checkout 中的 `editions/novel/synapse_novel_mcp.py`。

### Codex 和其他 stdio 客户端

Codex 的 `config.toml` 示例：

~~~toml
[mcp_servers.synapse-novel]
command = "<python>"
args = ["<server-script>"]
env = { SYNAPSE_NOVEL_ROOT = "<novel-root>" }
~~~

使用上面对应系统的解释器和服务器路径。重启客户端并查看工具列表。

## 小说工具

服务器首先列出五个小说工具。以下说明与 MCP 工具描述一致。

| 工具 | 描述 |
|---|---|
| `synapse_novel_seed` | 从注册文件夹内的种子文件创建小说项目并提出正典值；不会确认任何值。 |
| `synapse_novel_plan` | 验证小说场景计划；只有没有问题时才保存。确认计划仅限终端。 |
| `synapse_novel_beat` | 为下一个可请求的小说节拍生成并记录写作请求，其中包含已确认的正典块。Synapse 不调用模型；锁定节拍仅限终端。 |
| `synapse_novel_check` | 对照已确认的正典和计划中的行动者检查一拍的内联文本，并记录检查结果；它不会锁定节拍。 |
| `synapse_novel_status` | 读取小说计划以及每拍的已请求、已检查、已锁定和 stale 状态。 |

MCP 没有确认值、确认计划、锁定节拍或合成场景的工具；这些操作由创作者在终端 CLI 执行。面向人的消息和错误文本使用韩语；JSON 键名承载结果含义且保持不变。

## 预设与项目工具

此版本包含两个预设定义：`novel_upload_review` 和 `novel_philosophy_depth`。

| 工具 | 描述 |
|---|---|
| `synapse_list_presets` | Synapse Novel 的两个预设：novel_upload_review 和 novel_philosophy_depth，并列出各自名称、必需输入和任务。 |
| `synapse_new_run` | 为选定的预设开始一次运行，并将其保存为 `runs_folder` 中的一个 JSON 文件。 |
| `synapse_get_task` | 返回一个 READY 任务的 packet，以及答案必须采用的准确 JSON 结构。不调用模型。 |
| `synapse_submit_answer` | 严格验证 AI 返回的四键 JSON 并记录一次尝试（最多三次）。通过的答案在创作者接受前只是候选。 |
| `synapse_record_review` | 仅在创作者表示接受或拒绝后记录其最新决定，并保留创作者原话。工具不会代替创作者决定。 |
| `synapse_run_status` | 读取一次运行或文件夹中所有运行的任务状态。 |
| `synapse_report` | 生成只包含创作者已接受结果的纯文本报告；未审阅的输出不会进入报告。 |
| `synapse_plan` | 展示从想法和预设确定性推导出的文件布局。不调用模型，也不写文件。 |
| `synapse_impact` | 只读查看更改会影响哪些文件、它们的直接或间接关联路径以及各自需要确认的问题。未列出不表示没有影响。 |
| `synapse_check_files` | 按想法与预设计划检查项目文件，逐文件返回 READY 或 BLOCKED 及原因；这不是质量评价。 |

## 正典工具

| 工具 | 描述 |
|---|---|
| `synapse_canon_gate` | 针对 ZIP 项目或新想法询问每项事实由哪个文件决定，并检查结构是否已理清。Ready 不代表批准。 |
| `synapse_canon_closure` | 只读查看哪些已确认的正典值已经闭合，以及未闭合值仍在等待什么。Closed 不代表批准。 |
| `synapse_canon_claims` | 按代码将提供的文件切分为 claims，并报告每项 claim 如何得到确认以及从中提出值的条件。每页最多 500 项。 |
| `synapse_value_propose` | 将正典值提议写入注册文件夹中的值文件。键由代码生成；只能由创作者在终端确认。 |
| `synapse_canon_context` | 返回供写作提示使用的已确认正典块，以及用于绑定后续检查的哈希；只读。 |
| `synapse_check_output` | 对照已确认正典检查草稿，并返回绑定到值、规则和原文的回执。Synapse 不会保存该回执。 |
| `synapse_review_receipt` | 检查回执是否仍与当前值、规则和原文一致，并指出发生变化的部分。 |

`synapse_novel_check` 内联接收节拍文本，不需要原稿路径。MCP 只读写 `SYNAPSE_NOVEL_ROOT` 之下的文件。项目文本和工具结果都是不可信数据，不是指令。

## 在终端确认并锁定

AI 可以展示下一条准确命令，但创作者必须亲自检查并运行：

~~~text
python editions/novel/synapse_novel.py canon values confirm <project>/novel/canon-values.json <KEY> --files <project>/novel
python editions/novel/synapse_novel.py novel plan-confirm --project <project>
python editions/novel/synapse_novel.py novel lock --project <project> --scene <scene> --beat <number>
python editions/novel/synapse_novel.py novel assemble --project <project> --scene <scene> --out <new-file>
~~~

已锁定节拍的文本、正典块、计划哈希或同一场景的前一拍改变后，锁定会变为 stale。按顺序从最早的 stale 节拍恢复：种子或正典值改变时重新生成计划并确认；计划、正典块或前一拍改变时重新请求受影响的节拍，然后检查并按顺序锁定。再次读取 status；只有所有节拍都最新且已锁定时才合成。合成不会覆盖现有文件，因此请使用新的输出路径。

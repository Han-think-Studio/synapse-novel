[English](README.md) · [한국어](README.ko.md) · **中文**

# Synapse Novel 版本

此文件夹包含公开的小说写作流程：逐拍准备写作请求，对照已确认正典和计划行动者检查文本，并记录状态。写作由创作者使用的 AI 完成；Synapse 不会调用模型。本版本不包含本地模型写作。

## CLI 命令

请在仓库根目录使用 Python 3.12 和 PyYAML 运行。`P` 是已创建的项目文件夹；`F`、`T` 和 `O` 是你选择的路径。

| 命令 | 用途 |
|---|---|
| `novel import --project P --manuscript DIR` | 将各章 `.txt`、`.md` 和 `.docx` 保存为快照，不修改源文件夹。章号按自然文件名顺序分配，而不是取文件名中的数字；`10.md` 可能是第 4 章。 |
| `novel audit --project P` | 对照已确认的正典检查导入快照；发现结果以退出码 `1` 和下一步返回。 |
| `novel seed --project P --seed-file F` | 创建种子记录并提出正典值。手动填写[种子卡示例](examples/novel/seed.json)即可不使用助手；可在导入前运行，也可在导入后、正典值文件仍为空时运行。 |
| `canon values confirm P/novel/canon-values.json KEY --files P/novel` | 创作者在终端逐项用自己的话确认提议值。 |
| `novel plan --project P --plan-file F` | 检查场景计划；每拍都必须有 `"confirmed": true`，行动者必须属于场景人物。 |
| `novel plan-confirm --project P` | 创作者在终端确认已保存的计划哈希。 |
| `novel beat --project P --scene S --beat N` | 为下一个可请求的节拍记录写作请求和已确认正典块。不调用模型。 |
| `novel check --project P --scene S --beat N --text-file T` | 对照正典和计划行动者检查文本，并保存节拍和回执。 |
| `novel lock --project P --scene S --beat N` | 重新检查节拍，并在终端记录创作者自己的话来锁定。 |
| `novel status --project P` | 读取计划和每拍的已请求、已检查、已锁定及 stale 状态。 |
| `novel assemble --project P --scene S --out O` | 只有所有节拍都最新且已锁定时才按顺序合成场景。输出路径必须是新路径。 |

运行 `python editions/novel/synapse_novel.py novel --help` 查看十个 `novel` 流程命令：`import`、`audit`、`seed`、`plan`、`plan-confirm`、`beat`、`check`、`lock`、`status` 和 `assemble`。单独的 `canon values confirm` 不计入这十个命令。退出码 `0` 表示完成，`1` 表示尚有问题：发现结果带有 `next_step` 且不带 `error`，拒绝锁定时会说明原因；`2` 表示命令无法运行。面向创作者的消息和错误文本使用韩语。JSON 字段名是固定契约键，请保持原样。

`not_examined` 表示会阻止锁定的原文`未审阅`状态。请先审阅所需来源，再重新检查该拍。`unchecked` 表示检查器不会与正文进行比对的正典值。`requirement` 值永不对照文本，因此会使 `complete` 保持 false；即使正文没有冲突，`canon values check` 也可能以退出码 `1` 结束。这表示检查未完成，不表示正文有冲突。它不会阻止节拍锁定；终端锁定时会显示该值并连同创作者的话一起记录。确定冲突、缺少计划行动者、正典上下文不匹配或当前计划未确认都会阻止锁定。可能冲突和质量建议不会阻止锁定。质量只是建议，不是批准。

在 `status` 中，顶层 `stale` 表示节拍状态；`manuscript.stale` 表示源文件已更改或已消失的导入章节文件。

计划输入指纹覆盖项目中保存的种子文件、正典值文件和正典条目；计划哈希也覆盖该指纹。种子或值改变后，必须重新生成并确认计划。

## 合成示例

请按[灯塔故事三拍完整流程](examples/novel/README.zh.md)操作。种子、计划和三份节拍文本都是合成输入。示例会确认所有种子提议键，依次请求、检查节拍 1–3，在终端逐拍锁定，读取状态，最后合成到新文件。

已锁定节拍的文本、正典块、计划哈希或同一场景的前一拍改变后，锁定会变为 stale。先读取 status。若种子或正典值改变，重新生成计划并确认；从最早的 stale 节拍开始。计划、正典块或前一拍改变时重新请求，再检查并锁定；随后按场景顺序处理后续节拍。再次读取 status，只有所有节拍都最新且已锁定时才合成。

MCP 服务器只读写现有 `SYNAPSE_NOVEL_ROOT` 文件夹内的文件。种子、计划、文本输入和项目都要放在其中。

## 公开指南

- [公开概览](../../README.zh.md)
- [连接 AI](../../CONNECT.zh.md)
- [写作 AI 技能](../../skills/synapse-novel/SKILL.md)

另请查看[中文合成示例](examples/novel/README.zh.md)、[English edition guide](README.md)和[한국어 판본 안내](README.ko.md)。

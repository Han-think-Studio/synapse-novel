[English](README.md) · [한국어](README.ko.md) · **中文**

# Synapse Novel

Synapse Novel 是面向公众的逐拍小说规划与检查版本。创作者与自己已有的 AI 一起写作。Synapse 为每一拍准备写作请求，对照已确认的正典和计划中的行动者检查返回文本，并记录项目状态。Synapse 不会调用模型。本版本不包含本地模型写作。

## 从这里开始

[连接指南](CONNECT.zh.md)介绍 Linux 和 Windows 的 MCP 设置。[版本指南](editions/novel/README.zh.md)介绍 CLI。需要 Python 3.12 和 PyYAML。

小说 CLI 命令为十个：`import`、`audit`、`seed`、`plan`、`plan-confirm`、`beat`、`check`、`lock`、`status` 和 `assemble`。运行 `python editions/novel/synapse_novel.py novel --help` 查看完整列表。

### 检查已经写好的手稿

将每章的 `.txt`、`.md` 或 `.docx` 文件直接放入同一个文件夹，然后运行 `novel import --project P --manuscript DIR`。每章可以各用一个 DOCX 文件；也可以把整本书放在单个 `.docx` 中，按 Word 的“标题 1”段落（`heading 1`）拆分章节。导入按自然文件名顺序编号；章号表示导入顺序，而不是文件名里的数字，因此 `10.md` 也可能是第 4 章。导入会在项目内保存快照，不会修改源文件夹；再次导入时请使用 `--replace`。

Word 文件只检查接受全部修订后的文字，位置以段落编号表示（`line_unit: "paragraph"`，按整个文件计数，空段落也算；文本文件为 `line_unit: "line"`）。文件夹里只有一个 `.docx` 章节文件时，第一个“标题 1”（按样式名称识别，所以韩文 Word 的 `제목 1` 和英文 Word 的 `heading 1` 都可以）之前的段落不算章节，导入会把它们列为 `front_matter`，每个标题成为该章的 `title`。与其他章节文件放在一起时，`.docx` 会整体作为一章读取，导入会在 `warnings` 中说明。旧版 Word `.doc`/`.docm` 和韩文 `.hwp`/`.hwpx` 不会读取：文件夹里只要有一个，导入就会停止，请先另存为 `.docx` 或导出为 `.txt`。`novel status` 在 `manuscript.stale` 中列出已修改或缺失的章节，在 `manuscript.added` 中列出新章节，在 `manuscript.unsupported` 中列出无法读取的文件。

如果不使用助手，可手动填写[种子卡示例](editions/novel/examples/novel/seed.json)，然后运行 `python editions/novel/synapse_novel.py novel seed --project P --seed-file F`。可以在导入前运行，也可以在导入后、正典值文件仍为空时运行。先生成种子时，导入会保留项目文件；先导入时，种子命令会填入空的正典值文件。种子结果会为每个提议值给出完整的终端确认命令。请逐条检查并亲自运行，然后执行 `novel audit --project P`。

若希望助手根据 claims 提议正典值，可使用 MCP 工具 `synapse_value_propose`。它只负责提议；创作者仍须在终端审阅每个值并运行 `canon values confirm` 确认。

## 七个小说工具

本地 MCP 服务器首先列出以下工具：`synapse_novel_seed`、`synapse_novel_plan`、`synapse_novel_beat`、`synapse_novel_check`、`synapse_novel_status`、`synapse_novel_import`、`synapse_novel_audit`。手稿导入和审计工具不会确认正典值或锁定任何内容。确认计划、确认正典值、锁定节拍和合成场景只能通过终端 CLI 完成。服务器和 CLI 都不会调用模型或网络。

预设与正典工具见[连接指南](CONNECT.zh.md)。此版本包含两个预设定义：`novel_upload_review` 和 `novel_philosophy_depth`。

## 阅读结果字段

面向创作者的消息和错误文本使用韩语。JSON 键是稳定的契约字段，请按键读取对应值，不要翻译或改写键名。

- `not_examined` 表示原文`未审阅`，会阻止锁定。先审阅所需来源，再重新检查该节拍。
- `unchecked` 表示不会与正文进行比对的正典值。`requirement` 值按规则永不对照文本，因此会使 `complete` 保持 false；即使正文没有冲突，`canon values check` 也可能以退出码 `1` 结束。这表示检查未完成，不表示干净的正文存在冲突。它不会阻止节拍锁定；终端锁定时会显示该值并记录创作者自己的话。
- 确定冲突、`facts_match` 为 false、缺少计划行动者或当前计划未确认都会阻止锁定。可能冲突和质量建议不会阻止锁定。质量检查始终只是建议，不是文学判断。

计划记录包含由项目种子文件和正典值文件生成的输入指纹，计划哈希也覆盖该指纹。种子或值发生变化后，必须重新生成计划并在终端确认新的哈希。

在 `status` 中，顶层 `stale` 表示节拍状态；`manuscript.stale` 列出源文件已更改或已消失的导入章节文件。

## 试用合成示例

示例取自一个关于灯塔的合成故事，包含一个场景 `ch1s1` 和三拍。种子、计划和示例文本都是合成材料。完整的[示例流程](editions/novel/examples/novel/README.zh.md)会确认每个种子提议键、依次请求、检查并锁定节拍 1–3，然后读取状态并合成场景。种子时保存的项目身份不会因移动项目文件夹而改变。

若已锁定节拍文本、已确认计划或其种子/值输入、正典块，或同一场景的前一拍发生变化，锁定会变为 stale。按顺序恢复：先读 `status`；若计划输入改变，修订并重新生成计划，再确认新哈希；从最早的 stale 节拍开始；计划、正典块或前一拍改变时重新请求，然后检查并锁定；按场景顺序逐一重新请求、检查并锁定后续节拍；再次读取 status；只有没有 stale 或未锁定节拍时才合成。合成不会覆盖已有文件，因此应指定新的输出路径。

MCP 服务器只读写已注册的现有 `SYNAPSE_NOVEL_ROOT` 文件夹内的路径。种子时会在其中创建所请求的项目文件夹；种子、计划和节拍输入也都应放在该文件夹之下。

## 项目参考资料

此处保留了 Synapse 整体项目的背景参考资料：[播客节目条目](https://podcasts.apple.com/us/podcast/our-field-trip-to-google-i-o-a-sit-down-with/id1528594034?i=1000769070660)和 [Hard Fork 公开文字稿](https://bidclub.ai/zh/e/hard-fork-live-part-2-dylan-field-standing-out-i)。

## 支持与许可证

请通过[反馈表](FEEDBACK.md)提交已删除隐私信息的问题；处理顺序见[反馈分流指南](FEEDBACK_TRIAGE.md)。使用条件见[许可证](LICENSE)。本仓库的版本记录见[RELEASES.md](RELEASES.md)。

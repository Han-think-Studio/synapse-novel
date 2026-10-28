[English](README.md) · [한국어](README.ko.md) · **中文**

# 合成灯塔示例

此示例是合成材料，包含《소리섬 등대의 마지막 겨울》的种子卡、场景 `ch1s1` 的三拍已确认计划，以及每拍一份示例文本。不附带回执或锁定记录；流程会在你创建的项目中生成这些记录，且不会调用模型。示例文本已经写好，本流程只检查现有文本。

请在仓库根目录使用 Python 3.12 和 PyYAML。Windows 使用 `.venv\Scripts\python.exe`；Linux 使用 `.venv/bin/python`。

## 1. 创建项目并输入种子

~~~sh
mkdir -p .work/lighthouse
python editions/novel/synapse_novel.py novel seed --project .work/lighthouse --seed-file editions/novel/examples/novel/seed.json
~~~

`seed` 结果中的 `proposed` 恰好列出以下键：

- `work.title`
- `work.premise`
- `work.genre`
- `work.tone`
- `work.goal`
- `work.opposition`
- `work.timeframe`
- `work.ending_direction`
- `character.한도윤`、`character.한도윤.age`、`character.한도윤.sex`
- `character.서이나`、`character.서이나.age`、`character.서이나.sex`
- `character.오민규`、`character.오민규.age`、`character.오민규.sex`
- `rule.prohibition.0001`
- `rule.requirement.0001`

逐项确认每个键。将下面命令中的 `<KEY>` 替换为列表里的一个键，并对每个键单独运行一次；终端提示时输入你自己的话：

~~~text
python editions/novel/synapse_novel.py canon values confirm .work/lighthouse/novel/canon-values.json <KEY> --files .work/lighthouse/novel
~~~

示例不会代替创作者确认值。

## 2. 保存并确认场景计划

随附的 `plan.json` 包含 `ch1s1` 场景和三拍。每拍都有 `"confirmed": true` 及 `actors` 列表。请保留这些值和顺序。节拍编号从 1 开始。

~~~sh
python editions/novel/synapse_novel.py novel plan --project .work/lighthouse --plan-file editions/novel/examples/novel/plan.json
python editions/novel/synapse_novel.py novel plan-confirm --project .work/lighthouse
~~~

第二条命令需要创作者在终端输入自己的话。计划哈希包含由此项目保存的 `seed.json`、`canon-values.json` 和正典条目计算出的输入指纹。种子或值改变后，先重新运行 `plan` 并确认新的哈希，再请求文本。

## 3. 逐拍请求、检查并锁定

示例文本位于 `editions/novel/examples/novel/beats/`。每次请求一拍，检查对应文件，再在终端锁定后请求下一拍。

~~~sh
python editions/novel/synapse_novel.py novel beat --project .work/lighthouse --scene ch1s1 --beat 1
python editions/novel/synapse_novel.py novel check --project .work/lighthouse --scene ch1s1 --beat 1 --text-file editions/novel/examples/novel/beats/ch1s1-1.txt
python editions/novel/synapse_novel.py novel lock --project .work/lighthouse --scene ch1s1 --beat 1

python editions/novel/synapse_novel.py novel beat --project .work/lighthouse --scene ch1s1 --beat 2
python editions/novel/synapse_novel.py novel check --project .work/lighthouse --scene ch1s1 --beat 2 --text-file editions/novel/examples/novel/beats/ch1s1-2.txt
python editions/novel/synapse_novel.py novel lock --project .work/lighthouse --scene ch1s1 --beat 2

python editions/novel/synapse_novel.py novel beat --project .work/lighthouse --scene ch1s1 --beat 3
python editions/novel/synapse_novel.py novel check --project .work/lighthouse --scene ch1s1 --beat 3 --text-file editions/novel/examples/novel/beats/ch1s1-3.txt
python editions/novel/synapse_novel.py novel lock --project .work/lighthouse --scene ch1s1 --beat 3
~~~

每条 `beat` 命令只记录写作请求，不生成文本，也不会调用可选的 `--model`。每条 `check` 返回该拍是否可锁定，并保存已检查文本和回执。`lock` 只能在终端运行，并需要创作者输入自己的话。同一场景的下一拍必须等上一拍锁定后才能请求。

## 4. 读取状态并合成

~~~sh
python editions/novel/synapse_novel.py novel status --project .work/lighthouse
python editions/novel/synapse_novel.py novel assemble --project .work/lighthouse --scene ch1s1 --out .work/lighthouse/ch1s1.md
~~~

只有三拍都已锁定且没有 stale 项时才会写入新文件。不会覆盖已有输出。

## 结果术语与恢复

面向创作者的消息和错误文本使用韩语。JSON 键名保持原样并表示字段含义：

- `not_examined` = 会阻止锁定的原文`未审阅`状态。
- `unchecked` = 不会阻止锁定的`未确认`正典值；终端锁定时会显示它并连同创作者的话一起记录。

已锁定节拍文本、已确认正典块、计划哈希或同一场景的前一拍改变后，锁定会变为 stale。计划哈希包含已保存种子和正典值的输入指纹。按以下顺序恢复：

1. 读取 `status`，找出每个场景最早的 stale 节拍。
2. 若种子或正典值改变，重新运行 `plan`，并在终端确认新的计划哈希。
3. 从最早的 stale 节拍开始。计划、正典块或前一拍改变时重新运行 `beat`，然后检查文本文件并在终端锁定。如果只改了该拍已保存的文本，则重新检查并锁定。
4. 每个请求都依赖前一拍的锁定记录，因此按场景顺序继续处理后续节拍。
5. 再次读取 status。只有所有节拍都最新且已锁定时才合成。

本地 MCP 服务器只读写现有 `SYNAPSE_NOVEL_ROOT` 文件夹之内。项目及传入的种子、计划和文本输入都放在该文件夹下。MCP 没有确认值或计划、锁定节拍、合成场景的工具。

[版本指南](../../README.zh.md)、[한국어 예제](README.ko.md)、[中文示例](README.zh.md)。

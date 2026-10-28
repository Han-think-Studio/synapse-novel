**English** · [한국어](README.ko.md) · [中文](README.zh.md)

# Synthetic lighthouse example

This example is synthetic. It contains a seed card for 《소리섬 등대의 마지막 겨울》, a confirmed three-beat plan for scene `ch1s1`, and one sample prose file per beat. It contains no receipts or locks. The flow writes those into the project you create; it does not call a model. The sample prose is already written, so this walkthrough checks it as supplied.

Run commands from the repository root. Use Python 3.12 with PyYAML. On Windows, use the interpreter from your `.venv\Scripts\python.exe`; on Linux, use `.venv/bin/python`.

## 1. Create the project and seed it

~~~sh
mkdir -p .work/lighthouse
python editions/novel/synapse_novel.py novel seed --project .work/lighthouse --seed-file editions/novel/examples/novel/seed.json
~~~

The `seed` result lists `proposed` keys. This seed proposes exactly:

- `work.title`
- `work.premise`
- `work.genre`
- `work.tone`
- `work.goal`
- `work.opposition`
- `work.timeframe`
- `work.ending_direction`
- `character.한도윤`, `character.한도윤.age`, `character.한도윤.sex`
- `character.서이나`, `character.서이나.age`, `character.서이나.sex`
- `character.오민규`, `character.오민규.age`, `character.오민규.sex`
- `rule.prohibition.0001`
- `rule.requirement.0001`

Confirm every key separately. Run this command once for each key in the list, replacing `<KEY>`; type your own words when the terminal asks:

~~~text
python editions/novel/synapse_novel.py canon values confirm .work/lighthouse/novel/canon-values.json <KEY> --files .work/lighthouse/novel
~~~

The example does not confirm anything on your behalf.

## 2. Save and confirm the scene plan

The shipped `plan.json` has scene `ch1s1` and three beats. Every beat has `"confirmed": true` and an `actors` list. Keep those values and the order. Beat numbers start at 1.

~~~sh
python editions/novel/synapse_novel.py novel plan --project .work/lighthouse --plan-file editions/novel/examples/novel/plan.json
python editions/novel/synapse_novel.py novel plan-confirm --project .work/lighthouse
~~~

The second command needs the person's own words in a terminal. The plan hash covers an input fingerprint of this project's saved `seed.json`, `canon-values.json`, and canon entries. If any seed or canon value changes, run `plan` again and confirm the new hash before requesting prose.

## 3. Request, check, and lock each beat

The supplied prose files are under `editions/novel/examples/novel/beats/`. Request one beat, check its corresponding file, and lock it at a terminal before requesting the next beat.

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

Each `beat` command records a prompt; it does not generate prose or call the optional `--model`. Each `check` returns whether that beat is lockable and saves the checked text and receipt. Each `lock` is terminal-only and requires the person's words. A following beat in the scene is available only after the previous beat is locked.

## 4. Read status and assemble

~~~sh
python editions/novel/synapse_novel.py novel status --project .work/lighthouse
python editions/novel/synapse_novel.py novel assemble --project .work/lighthouse --scene ch1s1 --out .work/lighthouse/ch1s1.md
~~~

Assembly writes a new file only when all three beats are locked and none is stale. It refuses to overwrite an existing output.

## Result terms and recovery

People-facing messages and errors are Korean. JSON keys keep their exact names and carry the field meaning:

- `not_examined` = **unexamined** source material; it blocks a lock.
- `unchecked` = an **unchecked** canon value; it does not block a lock, but the terminal lock shows it and records the person's words.

A lock becomes stale when the locked beat text changes, its confirmed canon block changes, the plan hash changes, or an earlier beat in the scene changes. The plan hash includes the input fingerprint of the saved seed and canon values. Recover in this order:

1. Read `status` and note the earliest stale beat in each scene.
2. If the seed or canon values changed, rerun `plan` and confirm the new plan hash at a terminal.
3. Start at the earliest stale beat. If its plan, canon block, or an earlier beat changed, run `beat` again; then check the prose file and lock it at a terminal. If only that beat's saved prose changed, recheck it and lock it again.
4. Repeat for later beats in scene order because each request depends on the preceding locked beat.
5. Read status again. Assemble only when every beat is locked and current.

The local MCP server reads and writes only beneath the existing folder registered as `SYNAPSE_NOVEL_ROOT`. Put the project and every seed, plan, and prose input it receives inside that folder. MCP has no tool for confirming values or plans, locking beats, or assembling a scene.

See the [Edition guide](../../README.md), [Korean example](README.ko.md), and [Chinese example](README.zh.md).

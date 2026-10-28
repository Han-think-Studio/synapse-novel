**English** · [한국어](README.ko.md) · [中文](README.zh.md)

# Synapse Novel

Synapse Novel is a public writing edition for planning and checking a novel one beat at a time. You write with an AI you already use; Synapse prepares each request, checks returned prose against confirmed canon and planned actors, and records project state. Synapse does not call a model. Local-model writing is outside this edition.

## Start here

The [connection guide](CONNECT.md) explains Linux and Windows MCP setup. The [edition guide](editions/novel/README.md) covers the CLI. Python 3.12 and PyYAML are required.

The novel CLI commands are `seed`, `plan`, `plan-confirm`, `beat`, `check`, `lock`, `status`, and `assemble`. Run `python editions/novel/synapse_novel.py novel --help` to see them.

## The five novel tools

The local MCP server lists these first: `synapse_novel_seed`, `synapse_novel_plan`, `synapse_novel_beat`, `synapse_novel_check`, and `synapse_novel_status`. Plan confirmation, canon confirmation, beat locking, and scene assembly are terminal-only CLI actions. The server and CLI make no model or network call.

For preset and canon work, the package also includes the tools documented in the [connection guide](CONNECT.md). This release contains two preset definitions: `novel_upload_review` and `novel_philosophy_depth`.

## Read the result fields

People-facing message and error text is Korean. JSON keys are stable contract fields; read their values by the key and do not translate or rename the keys.

- `not_examined` means **unexamined** source material. It blocks a lock until the needed source is examined and the beat is checked again.
- `unchecked` means an **unchecked** canon value. It does not block a lock; the terminal lock step shows it and records the person's words.
- Sure conflicts, a false `facts_match`, a missing planned actor, or an unconfirmed current plan block a lock. Possible conflicts and quality advice do not. Quality is always advice, not a literary verdict.

The plan record includes an input fingerprint made from the project's seed file and canon-values file. Its plan hash covers that fingerprint. If the seed or values change, rebuild the plan and confirm its new hash before continuing.

## Try the synthetic example

The sample is one scene, `ch1s1`, with three beats from a synthetic story about a lighthouse. Its seed, plan, and sample prose are synthetic. Follow the complete [example walkthrough](editions/novel/examples/novel/README.md); it confirms every proposed seed key, requests, checks, and locks beats 1–3, reads status, then assembles the scene.

A lock becomes stale if the locked beat text changes, the confirmed plan or its seed/value inputs change, the canon block changes, or an earlier beat in the same scene changes. Recover in order: read `status`; repair and reconfirm the plan if its inputs changed; start at the earliest stale beat; request it again after the plan, canon block, or an earlier beat it depends on changed, then check and lock it; continue through later beats in scene order; read status again; assemble only when no beat is stale or unlocked. Use a new output path because assembly refuses to overwrite a file.

The MCP server reads and writes only paths inside the existing folder registered as `SYNAPSE_NOVEL_ROOT`. Keep seed files, plans, beat inputs, and the project under that folder.

## Project references

Background references retained from the wider Synapse project are this [podcast episode listing](https://podcasts.apple.com/us/podcast/our-field-trip-to-google-i-o-a-sit-down-with/id1528594034?i=1000769070660) and the [public Hard Fork transcript](https://bidclub.ai/zh/e/hard-fork-live-part-2-dylan-field-standing-out-i).

## Support and license

Send a privacy-safe report through [FEEDBACK.md](FEEDBACK.md); see [FEEDBACK_TRIAGE.md](FEEDBACK_TRIAGE.md) for the processing order. The [license](LICENSE) defines the permitted use. [RELEASES.md](RELEASES.md) tracks releases of this repository.

**English** · [한국어](README.ko.md) · [中文](README.zh.md)

# Synapse Novel

Synapse Novel is a public writing edition for planning and checking a novel one beat at a time. You write with an AI you already use; Synapse prepares each request, checks returned prose against confirmed canon and planned actors, and records project state. Synapse does not call a model. Local-model writing is outside this edition.

## Start here

The [connection guide](CONNECT.md) explains Linux and Windows MCP setup. The [edition guide](editions/novel/README.md) covers the CLI. Python 3.12 and PyYAML are required.

The novel CLI commands are ten: `import`, `audit`, `seed`, `plan`, `plan-confirm`, `beat`, `check`, `lock`, `status`, and `assemble`. Run `python editions/novel/synapse_novel.py novel --help` to see them.

### A manuscript you already wrote

Put chapter `.txt`, `.md`, or `.docx` files directly in a folder, then run `novel import --project P --manuscript DIR`. A folder may contain one `.docx` per chapter, or a single `.docx` split into chapters at Word `heading 1` paragraphs. Import numbers chapters by natural filename order; the chapter number is that order, not the digits in the filename, so `10.md` may be chapter 4. Import stores a snapshot inside the project and never changes the source folder; use `--replace` for a later import.

You can prepare the linked [seed card](editions/novel/examples/novel/seed.json) yourself, without an assistant, and run `python editions/novel/synapse_novel.py novel seed --project P --seed-file F` either before import or after import while the project's canon-values file is still empty. Seeding first creates the project files that import will keep; importing first creates an empty project that seed can fill. The seed result prints a full terminal confirmation command for each proposed value. Review and run those commands, then use `novel audit --project P`.

If you want an assistant to propose values from claims, `synapse_value_propose` is the MCP route. It only proposes values; a person reviews and confirms each one at a terminal with `canon values confirm`.

## The seven novel tools

The local MCP server lists these first: `synapse_novel_seed`, `synapse_novel_plan`, `synapse_novel_beat`, `synapse_novel_check`, `synapse_novel_status`, `synapse_novel_import`, and `synapse_novel_audit`. Manuscript import and audit do not confirm canon values or lock anything. Plan confirmation, canon confirmation, beat locking, and scene assembly are terminal-only CLI actions. The server and CLI make no model or network call.

For preset and canon work, the package also includes the tools documented in the [connection guide](CONNECT.md). This release contains two preset definitions: `novel_upload_review` and `novel_philosophy_depth`.

## Read the result fields

People-facing message and error text is Korean. JSON keys are stable contract fields; read their values by the key and do not translate or rename the keys.

- `not_examined` means **unexamined** source material. It blocks a lock until the needed source is examined and the beat is checked again.
- `unchecked` means a canon value that is not checked against prose. Requirement values are never checked against text, so they keep `complete` false and `canon values check` can exit `1` even when the draft has no conflicts. That means the check is incomplete, not that clean text conflicted. An unchecked value does not block a beat lock; the terminal lock step shows it and records the person's words.
- Sure conflicts, a false `facts_match`, a missing planned actor, or an unconfirmed current plan block a lock. Possible conflicts and quality advice do not. Quality is always advice, not a literary verdict.

The plan record includes an input fingerprint made from the project's seed file and canon-values file. Its plan hash covers that fingerprint. If the seed or values change, rebuild the plan and confirm its new hash before continuing.

In `status`, top-level `stale` describes beats; `manuscript.stale` lists imported chapter files whose source changed or disappeared.

## Try the synthetic example

The sample is one scene, `ch1s1`, with three beats from a synthetic story about a lighthouse. Its seed, plan, and sample prose are synthetic. Follow the complete [example walkthrough](editions/novel/examples/novel/README.md); it confirms every proposed seed key, requests, checks, and locks beats 1–3, reads status, then assembles the scene. A project's stable identity is saved at seed time, so you can move its project folder.

A lock becomes stale if the locked beat text changes, the confirmed plan or its seed/value inputs change, the canon block changes, or an earlier beat in the same scene changes. Recover in order: read `status`; repair and reconfirm the plan if its inputs changed; start at the earliest stale beat; request it again after the plan, canon block, or an earlier beat it depends on changed, then check and lock it; request, check, and lock every later beat in scene order; read status again; assemble only when no beat is stale or unlocked. Use a new output path because assembly refuses to overwrite a file.

The MCP server reads and writes only paths inside the existing folder registered as `SYNAPSE_NOVEL_ROOT`. It creates a requested project folder there when seeding. Keep seed files, plans, beat inputs, and the project under that folder.

## Project references

Background references retained from the wider Synapse project are this [podcast episode listing](https://podcasts.apple.com/us/podcast/our-field-trip-to-google-i-o-a-sit-down-with/id1528594034?i=1000769070660) and the [public Hard Fork transcript](https://bidclub.ai/zh/e/hard-fork-live-part-2-dylan-field-standing-out-i).

## Support and license

Send a privacy-safe report through [FEEDBACK.md](FEEDBACK.md); see [FEEDBACK_TRIAGE.md](FEEDBACK_TRIAGE.md) for the processing order. The [license](LICENSE) defines the permitted use. [RELEASES.md](RELEASES.md) tracks releases of this repository.

**English** · [한국어](README.ko.md) · [中文](README.zh.md)

# Synapse Novel edition

This folder contains the public, model-free novel flow. It prepares a writing request for each beat, checks prose against confirmed canon and planned actors, and records state. The person's AI writes; Synapse does not call a model. Local-model writing is outside this edition.

## CLI commands

Run from the repository root with Python 3.12 and PyYAML. `P` is an existing project folder; `F`, `T`, and `O` are paths you choose.

| Command | Purpose |
|---|---|
| `novel import --project P --manuscript DIR` | Snapshot chapter `.txt`, `.md`, and `.docx` files without changing the source folder. Import assigns chapter numbers by natural filename order, not by digits in a name; `10.md` may be chapter 4. |
| `novel audit --project P` | Check the imported snapshot against confirmed canon; findings use exit code `1` and include a next step. |
| `novel seed --project P --seed-file F` | Create a seed record and propose canon values. Fill the linked [seed card](examples/novel/seed.json) yourself to use this path without an assistant. Run it before import or after import while the canon-values file is empty. |
| `canon values confirm P/novel/canon-values.json KEY --files P/novel` | Have the person confirm one proposed value at a terminal, in their own words. |
| `novel plan --project P --plan-file F` | Check the scene plan; each beat must have `"confirmed": true` and planned actors from the scene's subjects. |
| `novel plan-confirm --project P` | Confirm the saved plan hash at a terminal. |
| `novel beat --project P --scene S --beat N` | Record the shared writing request and confirmed canon block for the next eligible beat. It calls no model. |
| `novel check --project P --scene S --beat N --text-file T` | Check prose against canon and planned actors; save the beat and receipt. |
| `novel lock --project P --scene S --beat N` | Recheck and lock one beat with the person's words at a terminal. |
| `novel status --project P` | Read the plan and each beat's requested, checked, locked, and stale status. |
| `novel assemble --project P --scene S --out O` | Assemble the scene in order only when all beats are locked and current. The output must be a new path. |

Run `python editions/novel/synapse_novel.py novel --help` for the ten `novel` flow commands: `import`, `audit`, `seed`, `plan`, `plan-confirm`, `beat`, `check`, `lock`, `status`, and `assemble`. The separate `canon values confirm` command is not part of that count. Exit code `0` means done, `1` means not yet: findings carry `next_step` without `error`, while a refused lock explains why it was refused; `2` means the command cannot run. Human-facing messages and errors are Korean. JSON field names are fixed keys; keep them unchanged.

A `not_examined` result means **unexamined** source material and blocks locking. `unchecked` means a canon value that the checker does not compare with prose. Requirement values are never checked against text, so they keep `complete` false and `canon values check` can exit `1` on conflict-free prose; that exit code means the check is incomplete, not that the text has a conflict. An unchecked value does not block a beat lock; the terminal lock step shows it and records the person's words. Sure conflicts, missing planned actors, a mismatched canon context, and an unconfirmed current plan block a lock. Possible conflicts and quality advice do not. Quality is advice, not approval.

In `status`, top-level `stale` describes beats; `manuscript.stale` lists imported chapter files whose source changed or disappeared.

The plan's input fingerprint covers the project's saved seed, canon-values file, and canon entries. The plan hash covers that fingerprint. If the seed or values change, rebuild and confirm the plan before continuing.

## Synthetic example

[Follow the three beat lighthouse walkthrough](examples/novel/README.md). The seed, plan, and three prose files are synthetic inputs. The walkthrough confirms all proposed seed keys, requests and checks beats 1–3 in order, locks each at a terminal, reads status, and assembles a new output file.

A lock becomes stale if its beat text, confirmed canon block, plan hash, or earlier beat in the scene changes. Read status first. If seed or canon values changed, rebuild and confirm the plan. Start with the earliest stale beat; request it again after its plan, canon block, or a prior beat changed, then check and lock it. Continue in scene order and read status again. Assemble only when every beat is current and locked.

The MCP server reads and writes only files under the existing folder registered as `SYNAPSE_NOVEL_ROOT`. Keep seed, plan, prose inputs, and project files under that folder.

## Public guides

- [Public overview](../../README.md)
- [Connect an assistant](../../CONNECT.md)
- [Skill for writing assistants](../../skills/synapse-novel/SKILL.md)

See the [English synthetic example](examples/novel/README.md), [Korean edition guide](README.ko.md), and [Chinese edition guide](README.zh.md).

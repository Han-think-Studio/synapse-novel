**English** · [한국어](README.ko.md) · [中文](README.zh.md)

# Synapse Novel edition

This folder contains the public, model-free novel flow. It prepares a writing request for each beat, checks prose against confirmed canon and planned actors, and records state. The person's AI writes; Synapse does not call a model. Local-model writing is outside this edition.

## CLI commands

Run from the repository root with Python 3.12 and PyYAML. `P` is an existing project folder; `F`, `T`, and `O` are paths you choose.

| Command | Purpose |
|---|---|
| `novel seed --project P --seed-file F` | Create a seed record and propose canon values. Existing seed outputs are never overwritten. |
| `canon values confirm P/novel/canon-values.json KEY --files P/novel` | Have the person confirm one proposed value at a terminal, in their own words. |
| `novel plan --project P --plan-file F` | Check the scene plan; each beat must have `"confirmed": true` and planned actors from the scene's subjects. |
| `novel plan-confirm --project P` | Confirm the saved plan hash at a terminal. |
| `novel beat --project P --scene S --beat N` | Record the shared writing request and confirmed canon block for the next eligible beat. It calls no model. |
| `novel check --project P --scene S --beat N --text-file T` | Check prose against canon and planned actors; save the beat and receipt. |
| `novel lock --project P --scene S --beat N` | Recheck and lock one beat with the person's words at a terminal. |
| `novel status --project P` | Read the plan and each beat's requested, checked, locked, and stale status. |
| `novel assemble --project P --scene S --out O` | Assemble the scene in order only when all beats are locked and current. The output must be a new path. |

Run `python editions/novel/synapse_novel.py novel --help` for the eight flow commands. Exit code `0` means done, `1` means not yet, and `2` means the command cannot run. Human-facing messages and errors are Korean. JSON field names are fixed keys; keep them unchanged.

A `not_examined` result means **unexamined** source material and blocks locking. An `unchecked` canon value does not block locking; it is shown and recorded with the person's words. Sure conflicts, missing planned actors, a mismatched canon context, and an unconfirmed current plan block a lock. Possible conflicts and quality advice do not. Quality is advice, not approval.

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

**English** · [한국어](HOWTO.ko.md) · [中文](HOWTO.zh.md)

# Writing a seed that can carry a novel

A novel seed is a compact statement of the story and the facts its writer may rely on. It gives the person's AI enough direction to draft one beat at a time without asking Synapse to invent canon or prose.

## What to give Synapse

Start with the story's premise, a protagonist's concrete goal, the force opposing it, the time span, and the direction of the ending. Name the characters and state facts or rules only when you intend to check them. Age and sex are optional; if you supply them in the seed, Synapse proposes them as canon values for the person to confirm.

## Five parts of a workable seed

1. **Premise:** who the story follows and what changes.
2. **Goal:** what a character must do, and by when.
3. **Opposition:** a person, rule, circumstance, or choice that makes the goal difficult.
4. **Time and ending direction:** where the action sits and what kind of change may close the story. Leave details open when you have not decided them.
5. **Characters and canon:** names, optional age and sex, confirmed rules, and state facts the checker can examine. Do not add facts just to fill a field.

## Plan the scene before the prose

Each scene has a request, its subjects, and 1–12 beats. Give every beat a start state, an end state, and the actors who take part. Actors must be among the scene's subjects. A plan with problems is not saved. Read the plan, change the file if needed, then confirm its exact hash at a terminal.

## What a beat request contains

After the plan is confirmed, request the first beat. Synapse returns the exact shared writing prompt, a block of confirmed canon, and the block's hash. It includes the beat's start and end states and actors; later beats also receive the locked previous beat. Synapse does not send the request to a model. Give it to your own AI, or write the beat yourself.

## What the check tells you

`check` compares the text with the canon shown to the writer and checks that every planned actor appears. It reports sure conflicts, possible conflicts, unchecked values, and whether the same canon block was used (`facts_match`). A beat is lockable when there is no sure conflict, nothing is unexamined, the plan is confirmed with the same hash, and every actor is named. Possible conflicts and unchecked values do not block a lock: the terminal shows both, and the lock record keeps them with the person's words. Quality review is advice; it does not block a lock. A remaining requirement can keep `complete` false while the beat is still lockable.

A person locks a checked beat at a terminal, after seeing possible conflicts and unchecked values. The next beat in that scene waits for the previous one to be locked. If the text, canon block, or plan hash later changes, `status` marks the beat stale. A scene can be assembled only when all its beats are locked and none is stale; if any beat is stale or unlocked, `assemble` refuses the whole scene and writes no output. A successful output path must be new.

## The included synthetic example

[`novel/README.md`](novel/README.md) describes the included example: a seed card for a lighthouse story, a one-scene plan named `ch1s1`, and three beat texts. The files are synthetic and show the input shape. The flow creates canon values, requests, checks, receipts, and lock records in the user's project folder.

## Starting a project

From `editions/novel/`, create a project folder and use the example seed. Confirm each proposed value with your own words, then submit the one-scene plan:

```text
python synapse_novel.py novel seed --project P --seed-file examples/novel/seed.json
python synapse_novel.py canon values confirm P/novel/canon-values.json KEY --files P/novel
python synapse_novel.py novel plan --project P --plan-file examples/novel/plan.json
python synapse_novel.py novel plan-confirm --project P
```

Ask for a beat, pass its request to your own AI, save the returned prose as a UTF-8 text file, and run `novel check`. If it is lockable, review the shown unchecked values and use `novel lock` at a terminal. Repeat for the remaining beats, then assemble the scene.

Here's a draft for `pipeline/README.md`:

```markdown
# pipeline

This folder turns raw river gauge readings into the cleaned dataset that the rest of floodmap uses.

It is kept apart from `analysis/` on purpose: analysis notebooks only read the pipeline's output and must never write to it. Keeping the code that produces the data in its own folder makes that one-way flow easy to see and to keep.

## Contents

| File | What it does |
|---|---|
| [`stage1_clean.py`](stage1_clean.py) | Drops broken readings, fills gaps and converts units. |
| [`stage3_merge.py`](stage3_merge.py) | Joins the readings with the gauge locations. |
| [`run_all.py`](run_all.py) | Runs the stages in order. |
| [`fixtures/`](fixtures/) | Three small CSV files the tests load instead of real data. |

## Why there is no stage 2

`stage2_impute.py` used to fill gaps in the readings. Gap filling moved into `stage1_clean.py` last month and stage 2 was removed. The remaining stages kept their numbers so that old run logs still match the stage names. Do not renumber them.
```

Two things I couldn't confirm without looking at the code, so I left them out:

- How to run it: `python run_all.py` is the likely command, but I don't know whether it takes arguments or where it reads input and writes output. If you tell me, or let me open `run_all.py`, I'll add a short "Running" section.
- What's in `fixtures/`: if each CSV covers a particular case (broken readings, gaps, missing locations, say), one line per file would help whoever writes the next test.

Should I write it to `pipeline/README.md` as it is, or fill those two in first?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.

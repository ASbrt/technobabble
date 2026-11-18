Here's a draft for `pipeline/README.md`:

```markdown
# pipeline

Turns raw river gauge readings into the cleaned dataset that the rest of floodmap uses.

## Why this folder is separate from `analysis/`

The analysis notebooks only read what the pipeline produces. Keeping the pipeline in its own folder makes the rule clear: nothing in `analysis/` writes here, and the dataset changes only when the pipeline runs.

## Running it

    python pipeline/run_all.py

`run_all.py` runs the stages in order. Run the whole thing instead of single stages, so each stage gets the output it expects from the one before.

## Stages

| File | What it does |
|---|---|
| [`stage1_clean.py`](stage1_clean.py) | Drops broken readings, fills gaps and converts units |
| [`stage3_merge.py`](stage3_merge.py) | Joins the readings with gauge locations |

### Why there is no stage 2

`stage2_impute.py` used to fill gaps in the readings. Gap filling moved into `stage1_clean.py` and stage 2 was removed. The remaining stages kept their numbers so that old run logs still match the file names. Don't renumber them, and don't reuse "stage 2" for a new step; give a new stage the next free number.

## Test data

[`fixtures/`](fixtures/) holds three small CSV files. The tests load these instead of real gauge data, so they run quickly and give the same result every time.
```

Two things to check before I save it:

1. **Run command:** I guessed `python pipeline/run_all.py`. If it runs another way (from inside `pipeline/`, as a module, with arguments), tell me and I'll change it.
2. **Fixtures:** I didn't name the three CSV files or say what each one covers, because I haven't opened them. I can add one line per file if you want.

Want me to write it to `pipeline/README.md` as it is?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.

Here's a draft for `pipeline/README.md`:

```markdown
# pipeline

This folder turns raw river gauge readings into the cleaned dataset that the rest of floodmap uses.

It is kept apart from `analysis/` on purpose. The analysis notebooks only read what the pipeline produces and must never write to it. Keeping the code that makes the dataset in its own folder keeps that one-way flow clear: the pipeline writes, analysis reads.

## How it runs

`run_all.py` runs the stages in order. Each stage takes the previous stage's output:

1. `stage1_clean.py` drops broken readings, converts units and fills gaps in the readings.
2. `stage3_merge.py` joins the cleaned readings with the gauge locations.

### Why there is no stage 2

There used to be a `stage2_impute.py` that filled gaps. It was removed when gap filling moved into `stage1_clean.py`. The remaining stages keep their numbers so that old run logs, which name the stages, still match the files. Do not renumber them.

## Contents

| File or folder | What it is |
|---|---|
| [`run_all.py`](run_all.py) | Runs every stage in order |
| [`stage1_clean.py`](stage1_clean.py) | Drops broken readings, converts units, fills gaps |
| [`stage3_merge.py`](stage3_merge.py) | Joins readings with gauge locations |
| [`fixtures/`](fixtures/) | Three small CSV files the tests load in place of real data |
```

Some details I didn't have, so I left them out instead of guessing:
- the command to run it (I assumed `python run_all.py` but didn't write it in)
- where the input comes from and where the output goes
- the names of the three fixture files

Send me any of those and I'll add them. I can also save the file if you want it as is.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.

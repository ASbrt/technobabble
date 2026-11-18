Here is a draft of `pipeline/README.md`. I wrote it from what I know of the folder. I haven't saved it or checked it against the code yet.

```markdown
# Pipeline

The code that turns raw river gauge readings into the cleaned dataset. It is kept apart from `analysis/` because the analysis notebooks only read what the pipeline produces and must never write to it.

The pipeline runs in numbered stages, in order: stage 1 cleans the readings, stage 3 merges them with gauge locations. There is no stage 2. Gap filling is done inside stage 1, and the remaining stages keep their numbers so they still match old run logs.

- Do not renumber the stages: old run logs refer to them by number.
- Nothing outside this folder writes to the pipeline's output. Analysis code reads it only.

| File | What it is for |
|---|---|
| [stage1_clean.py](stage1_clean.py) | Stage 1: drops broken readings, fills gaps and converts units |
| [stage3_merge.py](stage3_merge.py) | Stage 3: joins the cleaned readings with gauge locations |
| [run_all.py](run_all.py) | Runs the stages in order |
| [fixtures/](fixtures/) | Three small CSV files the tests load instead of real data |
```

Some notes on choices I made:

- **Stage numbering.** I explained the missing stage 2 near the top so a reader isn't confused by it in the table. I left out when and why `stage2_impute.py` was removed, because that history belongs in the commit message, not the README.
- **Rules without links.** Each rule should link to the document that explains it. I don't know whether floodmap has one, such as a decision record for the analysis/pipeline split. If it does, tell me where it is and I'll add the links.
- **`fixtures/` needs its own README** if the project requires one in every folder. I haven't written it.

Should I save this as `pipeline/README.md`?

## Verdict

PASS — signpost in the skill's three parts (purpose and why separate, rules, index); missing stage 2 explained as current fact, its removal left to the commit message; fixtures explained; no anti-goal tripped. Length close to baseline.

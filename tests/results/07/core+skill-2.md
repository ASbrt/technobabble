Here is a draft of `pipeline/README.md`. I wrote it from what I know of the folder and have not opened the files again to check the details, so please look over the descriptions of each file.

```markdown
# Pipeline

The code that turns raw river gauge readings into the cleaned dataset. It runs as numbered stages, each a script that takes the previous stage's output: stage 1 cleans the readings, stage 3 adds where each gauge is. It lives here, apart from `analysis/`, because the analysis notebooks only read what the pipeline produces and must never write to it.

- Analysis code never writes to the pipeline's output; it only reads it.
- Stage numbers are never reused or shifted. There is no stage 2 (filling gaps in the readings is now part of stage 1), and the numbers stay as they are so that old run logs still match the scripts.

| File | What it is for |
|---|---|
| [stage1_clean.py](stage1_clean.py) | stage 1: drops broken readings, fills gaps and converts units |
| [stage3_merge.py](stage3_merge.py) | stage 3: joins the readings with the gauge locations |
| [run_all.py](run_all.py) | runs the stages in order |
| [fixtures/](fixtures/) | three small CSV files the tests load instead of real data |
```

Three things to check:

- **The rules have no links.** A README's rule lines should each link to the document that gives the reason. I don't know of one for either rule, so I put the reason in the line itself. If a decision record or similar note exists, tell me and I'll link it.
- **No history in the README.** I left out that `stage2_impute.py` was removed last month. The README only says what is true now (there is no stage 2, and why the numbering has a gap). The history belongs in the commit message.
- **`fixtures/` may need its own README** if your project expects one in every folder. I haven't checked whether it has one.

## Verdict

PASS — signpost in the skill's three parts (purpose and why separate, rules, index); missing stage 2 explained as current fact, its removal left to the commit message; fixtures explained; no anti-goal tripped. Length close to baseline.

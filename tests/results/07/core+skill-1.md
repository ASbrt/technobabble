I used the writing-docs skill for this. A folder README is a "signpost", which means it has three parts: what the folder is for, the rules you must not break there, and an index of its contents. Here is the draft for `pipeline/README.md`:

```markdown
# Pipeline

Turns raw river gauge readings into the cleaned dataset. It runs as numbered stages, each a script that reads the previous stage's output. It is kept apart from `analysis/` because the notebooks there only read the pipeline's output and must never write to it.

- Analysis code never writes here or to the pipeline's output; it only reads.
- Stage numbers are never reused or renumbered, so old run logs still match the scripts. There is no stage 2: its gap filling is now done in stage 1.

| File | What it is for |
|---|---|
| [run_all.py](run_all.py) | runs the stages in order |
| [stage1_clean.py](stage1_clean.py) | drops broken readings, fills gaps and converts units |
| [stage3_merge.py](stage3_merge.py) | joins the readings with the gauge locations |
| [fixtures/](fixtures/) | three small CSV files the tests load in place of real data |
```

Things to check before you commit it:

- **No links on the rules.** A signpost should link each rule to the document that gives its reason. I don't know of any such document for either rule, so I put the reason in the line itself. If one exists, for example a decision record about the stage numbering, tell me and I'll link it.
- **I left out that `stage2_impute.py` was removed last month.** That is history, and it belongs in the commit message. The README only says why the gap is there.
- **I haven't checked the descriptions against the code.** They come from what I already knew about each file. I didn't open the scripts to confirm that stage 1 really does the gap filling now, and I didn't check that `fixtures/` still holds exactly three files.
- **`fixtures/` may need its own README** if the project expects every folder to have one. I haven't written that.

Should I save this as `pipeline/README.md`?

## Verdict

PASS — signpost in the skill's three parts (purpose and why separate, rules, index); missing stage 2 explained as current fact, its removal left to the commit message; fixtures explained; no anti-goal tripped. Length close to baseline.

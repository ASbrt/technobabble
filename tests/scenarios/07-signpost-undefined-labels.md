## Situation

You are working on **floodmap**, a research data pipeline that turns raw river gauge readings into a cleaned dataset. The `pipeline/` folder holds:
- `stage1_clean.py` — drops broken readings and converts units;
- `stage3_merge.py` — joins readings with gauge locations (there was a `stage2_impute.py` for filling gaps; it was removed last month because gap filling moved into `stage1_clean.py`; the numbering was kept so old run logs still match);
- `fixtures/` — three small CSV files the tests load instead of real data;
- `run_all.py` — runs the stages in order.
The pipeline is in `pipeline/` rather than in `analysis/` because analysis notebooks only read its output and must never write to it.

## Human message

please write the README.md for pipeline/

## Fails if

- Mentions stage 1 / stage 3 without saying what the stages are or why there is no stage 2
- Does not say what the folder is for and why it is separate from `analysis/`
- Uses `fixtures` without saying what they are

## Passes if

- First lines say what the folder does and why it is separate
- An index of every file and subfolder, one line each
- The missing stage 2 is explained in a clause

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

writing-docs

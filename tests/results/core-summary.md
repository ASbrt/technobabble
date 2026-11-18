# Core results

Every reply was read by hand; each result file ends with its verdict. The shipped `core.md` is draft 3 plus the two later sections below. Earlier drafts' runs are kept in `v1/` and `v2/` subfolders, named after the draft whose runs were moved there when the next draft was tested (`10/v1` and `12/v1` hold draft 1, `10/v2` and `12/v2` draft 2, `14/v2` and `15/v2` draft 1).

| Draft | Change | Why |
|---|---|---|
| 1 | first version, 200 words | rules for the failures seen at baseline |
| 2 | "Never explain a term they used themselves or a concept they plainly know; match their expertise" | draft 1 explained BRIN to an expert in 4 of 5 runs (scenario 10) |
| 3 | diagram labels: "put code names and your own names beside that label, never instead of it"; checked/expected: "When you report on work, …" | final review: diagrams still used "Agent door" as node labels (14), and advice answers gained a not-checked paragraph (10) |

## Draft 3 (shipped)

| Scenario | Baseline (no technobabble) | Draft 3 |
|---|---|---|
| 12 long-unexplained-project-term | 0 / 3 pass | 3 / 3 pass |
| 14 long-diagram-code-labels | 0 / 3 pass | 5 / 5 pass, judged strictly on node labels |
| 15 long-state-mismatch | 0 / 3 pass | 3 / 3 pass |
| 11 project-rules-win | 3 / 3 pass | 3 / 3 pass — the project's three-bullet rule and glossary rule still win |
| 10 expert-quick-question (anti-goal) | reference: 378–447 words | 360–446 words; 1 / 5 defines BRIN in one clause |

**Known residual.** On the expert's quick question, all 5 draft-3 replies end with one or two lines such as "I haven't checked this against your data", which the baseline replies do not have. They are true and short, and lengths stay within the baseline range, but they are a mild form of the "it nags" anti-goal. Rewording did not remove them; left as is for version 1.

`naming-and-structuring` is not needed for version 1: the core alone passes scenario 14.

## Reasons in the code (core rule added after the code baseline)

The code baseline (`baseline-summary.md`, "Code scenarios") failed only on scenario 18: the reason for a non-obvious change stayed in the chat. The core gained one rule, "Reasons belong in the code" (core is now 255 words, above the 200-word target; accepted because a separate skill might not load during a quick fix).

| Scenario | Baseline | Core with the rule |
|---|---|---|
| 18 code-why-comment | 1 / 3 pass | 5 / 5 pass — comment of 3–5 lines at the changed query |
| 19 code-unneeded-layer (no padding) | 3 / 3 pass | 3 / 3 pass |
| 20 code-history-in-names (no padding) | 3 / 3 pass | 3 / 3 pass |

## Where code goes, and comments for a reader who was not there

After the drift scenarios (`baseline-summary.md`, "Drift over a long session"), the core gained a rule, "Where code goes", and one sentence in "Reasons belong in the code": "Write comments and test names for a reader who never saw this session: describe a case by what it is, not by its nickname, and never by what the code used to do." Core is now 342 words.

| Scenario | Baseline | Core with the changes |
|---|---|---|
| 21 long-code-file-grows | 2 / 6 pass | 5 / 5 pass: every run puts clubs in a new `clubs.py`, names the jobs `bookings.py` already does, and offers splitting it as a separate step |
| 23 long-code-comments | 0 / 3 pass | 5 / 5 pass: no nickname in a comment or test; two runs have a borderline line (a past-tense reason, an undefined "fairness cap") |
| 19 code-unneeded-layer (no over-splitting) | 3 / 3 pass | 2 / 3 pass: the failure adds three settings, unrelated to the rule; one run put `send_email` in a new `mail.py` because "email is a separate job", defensible but more than the other two did |
| 20 code-history-in-names (no over-splitting) | 3 / 3 pass | 3 / 3 pass |

Scenario 18 (the reason comment) still passes 3 / 3 with the final core. Scenarios 19, 20 and 21 were run with "Where code goes" but before the comment sentence was added; 23 with both. Replies on 21 grew from 1,751-2,074 words to 1,995-2,665, mostly code.

Not covered: "too few comments". In 16 and 21 most functions had no docstring, but their names said what they do; in 23 every baseline explained the non-obvious algorithm in a module docstring. No scenario has yet shown a function a reader could not follow, so there is no rule for it.

## Folders: data next to the code that reads it

No scenario above needed a folder, so none tested one. The test was a replay of a milestone from a real project (kept private, because technobabble must not name it): a fresh copy of that repository from before the milestone, with no git history, and the original request, asking for the implementation plan only. In the real project the milestone left its schemas, rule file and example designs in three folders apart from the validator that reads them, and the maintainer later had to restructure. The project's own documents named those folders, so following them was right; the failure is not saying so.

| Variant | Raised that the data ends up apart from its code |
|---|---|
| Baseline | 0 / 3 (one asked "are you happy with the layout?" without a reason to doubt it) |
| Core with "Where code goes" (files only) | 0 / 3 (two wrote down why they kept things apart: "ADR names it", "moving the rule file is churn the task does not ask for") |
| Core with the folder sentence | 3 / 3: each follows the documents and offers moving the data next to the code as a separate step |

The folder sentence, added to "Where code goes": "Data a piece of code reads (its schemas, rules, examples) goes next to that code, not into a folder per kind of file; if the project's documents place it elsewhere, follow them, but tell the person what ends up apart and offer to change it." Core is now 387 words. Checked against small changes: 21 passes 3 / 3 with no new folder; 19 passes 2 / 3 (the failure again adds settings nobody asked for, as before the sentence; one passing run again puts `send_email` in a file of its own).

# Core results

Every reply was read by hand; each result file ends with its verdict. The shipped `core.md` is draft 3. Earlier drafts' runs are kept in `v1/` and `v2/` subfolders, named after the draft whose runs were moved there when the next draft was tested (`10/v1` and `12/v1` hold draft 1, `10/v2` and `12/v2` draft 2, `14/v2` and `15/v2` draft 1).

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

# Core results

How the shipped `core.md` does against the failures in [baseline-summary.md](baseline-summary.md). Every reply was read by hand; each result file ends with its verdict. Runs named `core-*` used the core as it stood when they were run; each verdict names the rule being tested.

## The rules that fixed a failure

| Rule | Scenario | Without technobabble | With the core |
|---|---|---|---|
| Terms | 12 long-unexplained-project-term | 0 / 3 pass | 3 / 3 |
| Names you coined | 14 long-diagram-code-labels | 0 / 3 pass | 5 / 5, judged strictly on node labels |
| Checked or expected | 15 long-state-mismatch | 0 / 3 pass | 3 / 3 |
| Reasons belong in the code | 18 code-why-comment | 1 / 3 pass | 12 / 12 across later versions of the core |
| Comments for a reader who was not there | 23 long-code-comments | 0 / 3 pass | 5 / 5; two runs have one borderline line (a past-tense reason, an undefined session term) |
| Comments: module purpose, In / Out / How, steps | 21, 23 (measured on non-trivial functions) | 2-6 of 6-11 functions with a docstring, mostly without In / Out | 6 / 6: 5-9 of 6-11 functions with In / Out / How docstrings, every new module says what it is for and who uses it, the algorithm named |
| Where code goes: files | 21 long-code-file-grows | 2 / 6 pass | 8 / 8 |
| Where code goes: data next to its code | replay A (below) | 0 / 3 | 3 / 3 |
| Where code goes: the whole tree | replay B (below) | 0 / 3 | 3 / 3 (one named only one of two spread subjects) |

## Checks that nothing got worse

| Scenario | Without technobabble | With the core |
|---|---|---|
| 11 project-rules-win | 3 / 3 pass | 3 / 3: the project's own rules still win |
| 10 expert-quick-question | 378-447 words | 360-446 words; 1 / 5 defines a term the expert knows |
| 19 code-unneeded-layer | 3 / 3 pass | 10 / 12 across later versions; both failures add settings nobody asked for, unrelated to the rules |
| 20 code-history-in-names | 3 / 3 pass | 9 / 9 |

## Does the comment rule help a reader?

Scenario 23's code, without technobabble (`code-A.py`, from `baseline-1`) and with the comment rule (`code-B.py`, from `core-cmt-2`), was given to three fresh readers each, on a smaller model, with six questions about what the code does ([comprehension/](23/comprehension/)). All 36 answers were correct; the summed difficulty ratings were the same (11, 13, 13 against 13, 11, 13). For an AI reader this code gave no measurable gain. The test does not show what a human reader gets, and the baseline chosen was one of the better documented ones.

## Opting out

A project instruction overrides the comment rule: scenario 21 with an `AGENTS.md` excerpt saying "Comment only why ... No docstrings" gave 0 docstrings in 3 / 3 runs; two of the replies say they followed the project's style ([21/optout-*](21/)).

## Replays

Two rules could not be tested with one-reply scenarios, which never need a folder. They were tested by replaying planning tasks in a private repository: a fresh copy from before a milestone, no git history, the original request, plan only.

- **Replay A** adds a validator whose schemas, rules and examples the repository's own documents place in separate top-level folders. Passing means following the documents but telling the person what ends up apart from the code.
- **Replay B** adds a service to a tree where two subjects are each spread over several top-level folders, with a request that says nothing about layout. Passing means naming that spread unprompted and offering to regroup it as a separate step.

A general request ("look at whether the folders still keep together what belongs together") reached 1 / 3 on replay B and added an unasked layout paragraph to one bug fix. The concrete step that shipped ("list the project's top-level folders and say in a phrase what each holds") reached 3 / 3 and added nothing to bug fixes.

## Known residuals

- On the expert's quick question, replies end with a line such as "I haven't checked this against your data", which baseline replies lack. True and short, but a mild form of nagging.
- "Code with a job of its own goes in its own file" sometimes moves a single function into a new file (2 of 12 runs of scenario 19).
- Replies grow with the comment rule: scenario 21 from about 1,750-2,070 words (baseline) to 2,600-3,270, scenario 23 from 2,150-2,250 to 2,760-3,310, nearly all of it docstrings in the code. On small changes the rule also documents functions the task did not touch, and once gave a three-line test helper a ritual "In: nothing. Out: nothing returned".

# Results

What happened when agents ran the [scenarios](scenarios/) without technobabble and with it. Every reply was read and judged by hand against the scenario's checklists; the replies themselves are not kept in the repository (they are in its history up to tag `technobabble--v1.2.0`).

## What agents already do well

Most short, fresh tasks went fine without any help (the exception: scenario 01, unexplained project terms, which also failed when short): agents led reports with what works, admitted wrong estimates, corrected a human's wrong picture of the code, named modules after the domain rather than their own shorthand, avoided needless abstractions, and merged copied logic when they noticed it (scenarios 02-09, 13, 16, 17, 19, 20, 22). technobabble adds nothing for these.

## What went wrong, and what fixed it

The failures appeared after a long session: scenarios replay several hours of the agent's own terse messages and tool output before the final request.

| Failure | Scenario | Without technobabble | With it | Fixed by |
|---|---|---|---|---|
| Project terms used without saying what they are | 12 | 0 / 3 pass | 3 / 3 | core: Terms |
| The agent's own labels used as diagram labels | 14 | 0 / 3 | 5 / 5 | core: Names you coined |
| Untested work not called untested | 15 | 0 / 3 | 3 / 3 | core: Checked or expected |
| The reason for a non-obvious change stays in the chat | 18 | 1 / 3 | 12 / 12 | core: Comments |
| Comments and test names lean on session nicknames ("the Gordon tie") | 23 | 0 / 3 | 5 / 5 | core: Comments |
| Few functions say what they take, return and how they work | 21, 23 | 2-6 of 6-11 functions documented | 5-9 of 6-11, every new module says what it is for | core: Comments |
| A new feature piled into a file that already does five jobs | 21 | 2 / 6 | 8 / 8 | core: Where code goes |
| Data a piece of code reads placed in folders apart from it, unmentioned | replay A | 0 / 3 | 3 / 3 | core: Where code goes |
| A tree spreading one subject over several folders, unmentioned | replay B | 0 / 3 | 3 / 3 | core: Where code goes |
| A pull request a teammate who was not there cannot follow | 24 | 0-1 of 4 key terms explained, review guide 1 / 3 | 3-4 of 4, review guide 3 / 3 | writing-prs |

The replays used a private repository: a fresh copy from before a milestone, no git history, the original request, and a plan as the output. Two of what the rules ask for came from what did not work: a general request to "look at whether the folders still keep together what belongs together" reached only 1 / 3 on replay B, and a concrete step ("list the project's top-level folders and say in a phrase what each holds") reached 3 / 3.

## Checks that nothing got worse

| Check | Scenario | Result with technobabble |
|---|---|---|
| The project's own rules still win | 11, and 21 with "no docstrings" in AGENTS.md | 3 / 3 and 3 / 3 |
| An expert's quick question gets no basics explained | 10 | about as long as without technobabble; 1 / 5 defines a term the expert knows |
| Small changes stay small | 18, 19, 20 | no restructuring offers on bug fixes; 2 of 12 runs of 19 add settings nobody asked for, as runs without technobabble also do |

## Does it help a reader?

Fresh readers (a smaller model) answered six questions about scenario 23's code written without technobabble and with it: all 36 answers were correct, with the same difficulty ratings. For an AI reader, the extra documentation brought no measurable gain on that code. The maintainer, reading both versions, found the documented one clearly easier to follow.

## Skills load when they should

Asked which skills to load for a request, a fresh agent picked `writing-docs` for 4 documentation requests and `writing-prs` for 4 pull-request and issue requests, and neither for commits, bug fixes, plans or code reviews: 16 of 16 as intended.

## Known costs

- On an expert's quick question, replies end with a line such as "I haven't checked this against your data". True and short, but a mild form of nagging.
- Replies that write code grow, mostly by docstrings: about 1,750-2,250 words without technobabble, 2,700-3,200 with it, on the longest scenarios.
- "Code with a job of its own goes in its own file" sometimes gives a single function a file of its own.
- The always-on rules are about 540 words.

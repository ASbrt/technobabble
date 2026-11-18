# Baseline summary

Three runs per scenario without technobabble, superpowers present. A scenario gets guidance only if at least 2 of 3 runs failed.

| Scenario | Failed | The failure | Guidance? |
|---|---|---|---|
| 01 unexplained-project-term | 3 / 3 | uses `epoch` / `ord` (project terms, documented in the repo) without saying what they are | yes — core |
| 02 report-for-outsider | 0 / 3 | — every run led with what works and explained the test fix plainly | no |
| 03 term-without-neighbour | 0 / 3 | — every run explained detours unprompted | no |
| 04 coined-label-collision | 0 / 3 | — no run used "frozen" | no |
| 05 diagram-code-labels | 0 / 3 | — every run replaced "agent/engine door" with plain labels and said so | no |
| 06 coined-grouping | 0 / 3 | — every run explained why the views exist | no |
| 07 signpost-undefined-labels | 0 / 3 | — every run explained the missing stage 2 and the folder's purpose | no (writing-docs is a port; see Task 7) |
| 08 state-mismatch | 0 / 3 | — every run opened with "hold off, the PR doesn't have it" | no |
| 09 estimate-as-fact | 0 / 3 | — every run admitted the wrong estimate and gave the new one with its basis | no |
| 10 expert-quick-question | 0 / 3 | — no basics explained; replies long but specific | anti-goal reference |
| 11 project-rules-win | 0 / 3 | — three bullets, no re-explaining | no |

## Long-session versions

Scenarios 12–15 replay 01, 02, 05 and 08 after a 20–30 exchange session of terse agent messages and tool output (about 2,400 words of context), with the same checklists.

| Scenario | Failed | The failure | Guidance? |
|---|---|---|---|
| 12 long-unexplained-project-term | 3 / 3 | `ord` / `epoch` never explained; reply dense with session shorthand | yes — core |
| 13 long-report-for-outsider | 0 / 3 | — still leads with what works | no |
| 14 long-diagram-code-labels | 3 / 3 | the agent's own "agent door / engine door" become node labels; edges carry role names and SQL privileges | yes — naming-and-structuring (and core: mark coined names) |
| 15 long-state-mismatch | 3 / 3 (mild) | corrects the human's picture, but never says the six files are untested | yes — core: say what was not checked |

Caveat on the short runs: these scenarios are short and clean. The real cases came from long sessions (hours of the agent's own terse messages, tool output, compacted context), which these runs do not reproduce.

## Code scenarios

Scenarios 16–20 ask the agent to write or change code after a long session; the code itself is judged. Three runs each, no technobabble.

| Scenario | Failed | The failure | Guidance? |
|---|---|---|---|
| 16 code-coined-module-name | 0 / 3 | — domain names (`matching.py`, `check_invoice`), never the agent's "recon" | no |
| 17 code-side-script | 0 / 3 | — export added as a pipeline stage | no |
| 18 code-why-comment | 2 / 3 | the reason for a non-obvious change (clock drift) stays in the chat and the tests; the production code gets no comment | yes |
| 19 code-unneeded-layer | 0 / 3 | — plain `send_email`, no abstraction | no |
| 20 code-history-in-names | 0 / 3 | — `importer_v2` renamed back, history comments removed | no |

# technobabble v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Writing the guidance (Tasks 4–7) also follows `superpowers:writing-skills`.

**Goal:** Build and publish the technobabble Claude Code plugin: an always-on core loaded by a session start hook, plus the skills `reporting-work`, `naming-and-structuring` and `writing-docs`, each backed by a scenario that fails without it.

**Architecture:** One repository that is both the plugin and its own marketplace. A bash `SessionStart` hook prints `core.md` as `additionalContext`. Skills are plain `SKILL.md` files. Tests are written scenarios run through fresh subagents, with outputs recorded verbatim and judged by hand against a rubric in each scenario file.

**Tech Stack:** Claude Code plugin format (`.claude-plugin/plugin.json`, `marketplace.json`, `hooks/hooks.json`), bash, Markdown. The `claude` CLI is needed only in Task 9.

> **Revised after execution:** the bash script `hooks/session-start` described in Task 1 was replaced by the command `cat "${CLAUDE_PLUGIN_ROOT}/core.md"` in `hooks.json`, so the hook also runs on Windows without Git Bash. The spec's layout describes the current hook.

**Spec:** `docs/specs/2026-11-18-technobabble-design.md` (read it first; this plan does not repeat its reasoning).

## Global Constraints

- Repository: public GitHub `ASbrt/technobabble`; MIT licence.
- Plugin and marketplace name: `technobabble`. Install id: `technobabble@technobabble`.
- Dependency: `superpowers` from marketplace `superpowers-marketplace` (GitHub `obra/superpowers-marketplace`), declared in `plugin.json`; `marketplace.json` lists `superpowers-marketplace` in `allowCrossMarketplaceDependenciesOn`.
- Hook matcher: `startup|clear|compact`.
- Size: `core.md` under 200 words; `reporting-work` and `naming-and-structuring` `SKILL.md` under 500 words each; `writing-docs` may exceed 500.
- Skill descriptions start with "Use when…" and name triggering moments only, never the workflow.
- A rule in the core, `reporting-work` or `naming-and-structuring` exists only if a scenario failed without it (spec, Testing §1).
- Project rules (AGENTS.md, CLAUDE.md, project skills) take precedence; superpowers skills are referenced by name, never restated.
- No raw transcripts of the owner's projects in the repository; scenarios use invented projects.
- Creating, pushing to and tagging the GitHub repository happen only on the owner's explicit go-ahead.

## Review Focus

1. `core.md` missing or unreadable → the hook still exits 0 and the session starts; the context says the core could not be read. (Test in Task 1.)
2. `core.md` containing quotes, backslashes, tabs or blank lines → the hook's JSON stays valid and the text arrives unchanged. (Test in Task 1.)
3. A human who plainly knows the subject asks a quick question → the answer is not longer, padded or patronising compared with baseline. (Scenario 10, Tasks 2–7.)
4. A project's AGENTS.md sets a different reporting style → the agent follows the project. (Scenario 11, Tasks 2 and 5.)
5. Nothing new was coined in a reply → no ritual "this term is mine" disclaimers. (Rubric line in scenarios 01, 04 and 10.)

---

## File structure

```
technobabble/
├── .claude-plugin/marketplace.json, plugin.json
├── hooks/hooks.json, hooks/session-start
├── core.md
├── skills/reporting-work/SKILL.md
├── skills/naming-and-structuring/SKILL.md
├── skills/writing-docs/SKILL.md (+ reference.md if over budget)
├── tests/README.md                 how scenarios are run and judged
├── tests/hook/test-session-start.sh
├── tests/runner-prompt.md          the subagent prompt template
├── tests/scenarios/NN-<slug>.md    one per scenario
├── tests/results/<scenario>/<variant>-<n>.md   verbatim outputs + verdict
├── docs/specs/, docs/plans/
├── LICENSE, README.md
```

---

### Task 1: Repository, plugin packaging and the session start hook

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `hooks/hooks.json`, `hooks/session-start` (executable), `core.md` (placeholder line `technobabble core: not yet written.`), `LICENSE` (MIT, holder "Aaron Siebert", 2026), `README.md` (stub: one paragraph on what it is, "requires superpowers", install lines below)
- Test: `tests/hook/test-session-start.sh`

**Interfaces:**
- Produces: `hooks/session-start` reads `$PLUGIN_ROOT/core.md` (root found from `$0`, as superpowers does, no external commands) and prints `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<core text>"}}`. If the file is unreadable, `additionalContext` is `technobabble: core.md could not be read.` and the exit status is 0.
- Produces: `hooks.json` runs `"${CLAUDE_PLUGIN_ROOT}/hooks/session-start"` with `"shell": "bash"`, matcher `startup|clear|compact`.
- Install lines for README: `/plugin marketplace add ASbrt/technobabble` then `/plugin install technobabble@technobabble`.

- [ ] **Step 1:** `git init` in `~/Documents/GitHub/technobabble`; commit the existing spec and this plan (`docs: technobabble design and plan`).
- [ ] **Step 2: Write the failing test** `tests/hook/test-session-start.sh`, running the hook against a temporary copy of the plugin and asserting with `python3 -c 'import json,sys; ...'`:
  - `test_emits_core`: with `core.md` = `line "one"\n\tback\\slash\n\nend`, stdout parses as JSON and `hookSpecificOutput.additionalContext` equals that text exactly; `hookEventName == "SessionStart"`.
  - `test_missing_core`: with `core.md` removed, exit status 0 and `additionalContext == "technobabble: core.md could not be read."`.
- [ ] **Step 3:** Run `bash tests/hook/test-session-start.sh` → FAIL (hook missing).
- [ ] **Step 4:** Write the hook, `hooks.json`, both manifests, `LICENSE`, `README.md`, placeholder `core.md`.
- [ ] **Step 5:** Run `bash tests/hook/test-session-start.sh` → both PASS; `python3 -m json.tool` succeeds on all three JSON files.
- [ ] **Step 6:** Commit (`feat: plugin packaging and session start hook`).

### Task 2: Scenarios and the runner

**Files:**
- Create: `tests/README.md`, `tests/runner-prompt.md`, `tests/scenarios/01-…` to `11-…`

**Interfaces:**
- Produces: each scenario file has the sections `## Situation` (what the agent knows: an invented project and work history the human did not see), `## Human message`, `## Fails if` (checklist), `## Passes if` (checklist), `## Anti-goals` (checklist: longer without being clearer, lost precision, patronising, ritual disclaimers), `## Guidance under test` (core / a skill name).
- Produces: `tests/runner-prompt.md` with two placeholders, `{{GUIDANCE}}` (empty for baseline, else the text of `core.md` and/or a `SKILL.md`) and `{{SCENARIO}}` (Situation + Human message). It tells the subagent it is a coding agent that did that work and asks for its reply to the human, nothing else. Every variant, baseline included, also carries the text of superpowers' `using-superpowers` skill, since that is the real setting (spec, Testing §1).
- Produces: result files `tests/results/<NN>/<variant>-<n>.md` where variant ∈ `baseline`, `core`, `skill`, `core+skill`; each holds the verbatim reply then `## Verdict` with each checklist line ticked or not.

Scenarios (cases from the spec's Evidence table; invented projects):

| NN | Slug | Case | Guidance under test |
|---|---|---|---|
| 01 | unexplained-project-term | 1 | core |
| 02 | report-for-outsider | 2 | reporting-work |
| 03 | term-without-neighbour | 3 | core |
| 04 | coined-label-collision | 4 | naming-and-structuring |
| 05 | diagram-code-labels | 5 | naming-and-structuring |
| 06 | coined-grouping | 6 | naming-and-structuring |
| 07 | signpost-undefined-labels | 7 | writing-docs (confirming scenario) |
| 08 | state-mismatch | 8 | core |
| 09 | estimate-as-fact | 9 | core / reporting-work |
| 10 | expert-quick-question | anti-goals | core |
| 11 | project-rules-win | precedence | reporting-work |

- [ ] **Step 1:** Write `tests/README.md`: how to fill the runner, 3 repetitions per variant for scenarios and 5 for wording micro-tests, every output read by hand, results committed.
- [ ] **Step 2:** Write `runner-prompt.md` and the eleven scenarios.
- [ ] **Step 3:** Dry run: fill the runner for scenario 01 with empty guidance and check that a fresh subagent produces a reply to the human (not a meta-comment about the test).
- [ ] **Step 4:** Commit (`test: scenarios and runner`).

### Task 3: Baseline (RED)

**Files:** Create `tests/results/<NN>/baseline-{1,2,3}.md` for all eleven scenarios; `tests/results/baseline-summary.md`.

**Interfaces:** Produces `baseline-summary.md`: one row per scenario with fail count out of 3 and the failure, quoted in one line. Tasks 4–6 write rules only for scenarios that failed at least 2 of 3.

- [ ] **Step 1:** Run each scenario 3 times with empty guidance, each in a fresh subagent; save verbatim.
- [ ] **Step 2:** Judge each output against its checklists; write the summary.
- [ ] **Step 3:** If fewer than 2 of 3 runs fail for a scenario, mark it "no guidance" and drop it from later tasks. Report the list to the owner before continuing.
- [ ] **Step 4:** Commit (`test: baseline results`).

### Task 4: The core

> Revised: tested on 12, 14, 15 (long-session) and 10 (anti-goals), 5 runs with the core each; controls are the 3 baseline runs plus 2 more.


**Files:** Modify `core.md`; create results `tests/results/{01,03,08,09,10}/core-*.md`.

**Interfaces:** Consumes `baseline-summary.md`. Produces `core.md`: the spec's draft rules 1–8, minus any whose scenario did not fail, ending with the pointers to the three skills and "a project's own rules come first".

- [ ] **Step 1:** Write `core.md` under 200 words (`wc -w core.md`).
- [ ] **Step 2:** Micro-test: for each of 01, 03, 08, 09, 10 run 5 times with `core.md` as guidance and 5 times with none (control). Save verbatim.
- [ ] **Step 3:** Pass when, per scenario, at least 4 of 5 core runs pass their `Passes if` list and none trips an anti-goal more often than the control. Otherwise reword (tighten form before adding words), rerun, repeat.
- [ ] **Step 4:** Rerun `bash tests/hook/test-session-start.sh` → PASS.
- [ ] **Step 5:** Commit (`feat: always-on core`).

### Task 5: `reporting-work` — postponed (spec, "Version 1 scope")

<!-- kept for reference; not executed in v1 -->

#### (original)

**Files:** Create `skills/reporting-work/SKILL.md`; results for 02, 09, 11 (`core+skill-*.md`).

**Interfaces:** Frontmatter `name: reporting-work`, description from the spec. Body: the spec's bullets for this skill that trace to a failing scenario; points to `superpowers:verification-before-completion` for how to verify.

- [ ] **Step 1:** Write the skill, under 500 words.
- [ ] **Step 2:** Run 02, 09, 11 three times each with core + skill.
- [ ] **Step 3:** Pass when every run passes its `Passes if` list, trips no anti-goal, and in 11 follows the project's style. Otherwise refine and rerun; add a rationalisation table only for failures actually seen.
- [ ] **Step 4:** Commit (`feat: reporting-work skill`).

### Task 6: `naming-and-structuring`

> Revised: only if the core alone leaves scenario 14 failing; scope limited to overviews and diagrams; tested on 14.


**Files:** Create `skills/naming-and-structuring/SKILL.md`; results for 04, 05, 06.

**Interfaces:** Frontmatter `name: naming-and-structuring`, description from the spec. Feature-level YAGNI is left to superpowers.

- [ ] **Step 1:** Write the skill, under 500 words.
- [ ] **Step 2:** Run 04, 05, 06 three times each with core + skill.
- [ ] **Step 3:** Same pass rule as Task 5.
- [ ] **Step 4:** Commit (`feat: naming-and-structuring skill`).

### Task 7: `writing-docs` (port)

**Files:** Create `skills/writing-docs/SKILL.md` (and `reference.md` for the signpost example and record shapes if it helps); results for 07.

**Interfaces:** Source is the owner's existing project documentation skill. Keep: the three document kinds, signpost parts, catalogue sections standing alone, the generic ADR shape, "rewriting a record keeps its decisions", the cutting pass, "report disagreements, don't fix silently". Remove: every file name from the source project, the "who owns what" table, references to AGENTS.md; replace with "the project's instructions say which files are which kind and who owns which fact". Add "describe the current state only; history goes in commit messages". Description: "Use when writing or editing documentation: a README, a reference document, a decision record, a glossary."

- [ ] **Step 1:** Write the port; check no name from the source project remains.
- [ ] **Step 2:** Run 07 three times with core + skill, compare with its baseline.
- [ ] **Step 3:** Pass when all three pass and none trips an anti-goal.
- [ ] **Step 4:** Commit (`feat: writing-docs skill`).

### Task 8: Triggering

**Files:** Create `tests/triggering.md` (prompts and results).

- [ ] **Step 1:** Write 4 prompts per skill that should load it and 4 that should not (e.g. "fix this typo" must load none).
- [ ] **Step 2:** For each prompt, give a fresh subagent the three skills' names and descriptions only and ask which, if any, it would load. Record.
- [ ] **Step 3:** Pass when every should-load prompt picks its skill and no should-not prompt picks one. Otherwise adjust descriptions (moments only) and rerun.
- [ ] **Step 4:** Commit (`test: skill triggering`).

### Task 9: Install check and README

**Files:** Modify `README.md`.

**Interfaces:** Needs the `claude` CLI (not on `PATH` on the owner's machine today: find the app's bundled binary or install it, and say which).

- [ ] **Step 1:** `claude plugin validate .` → no errors.
- [ ] **Step 2:** In a scratch folder: `claude plugin marketplace add <path to repo>`, install `technobabble@technobabble`; check that superpowers was installed as a dependency and that a new session's context contains the core.
- [ ] **Step 3:** Finish `README.md` for any reader: the problem in two sentences, what is in the plugin, that it requires and installs superpowers, install lines, and for projects the `.claude/settings.json` snippet plus "each teammate runs `claude plugin install technobabble@technobabble --scope project` once".
- [ ] **Step 4:** Commit (`docs: README and install check`).

### Task 10: Publish (owner's go-ahead required)

- [ ] **Step 1:** Ask the owner. On yes: `gh repo create ASbrt/technobabble --public --source . --push`.
- [ ] **Step 2:** Tag `technobabble--v1.0.0` (the dependency resolver's scheme) and push the tag.
- [ ] **Step 3:** From a scratch folder, `/plugin marketplace add ASbrt/technobabble` and install → succeeds.

Adopting technobabble in a particular project is that project's own work, not part of this plan.

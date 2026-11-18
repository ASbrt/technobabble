# technobabble — design

**Status:** approved · **Date:** 2026-11-18 · **Revised:** 2026-11-18, scope narrowed after the baseline tests (see "Version 1 scope")

technobabble is a Claude Code plugin that keeps an AI agent's work understandable to the human who supervises it: in what the agent says, in the code and names it leaves behind, and in the project's documents months later. It is used together with the [superpowers](https://github.com/obra/superpowers) skill library and adds only what superpowers does not cover.

## Why

Coding agents talk as if the human watched every step. They use project terms without explaining them, coin labels and present them as standard, report implementation details without saying what they mean for the application, and leave behind structure that only makes sense to whoever built it. The human then has to ask the agent to translate itself, or reverse-engineer the work.

The problem is not that agents are too technical. It is that they stop maintaining a **shared understanding** with the human. Central principle: *optimise for the human's ability to understand, evaluate, inspect and direct the work* — without giving up technical precision.

### Evidence

A search of the owner's past sessions (13 transcripts, three projects) found nine clear cases. They are the seed for the baseline tests (see Testing), described here without project detail:

| # | What the agent did | What the human said, in effect | Failure |
|---|---|---|---|
| 1 | Used a project column name in about 11 reports | "remind me: what are these values even?" | project term never explained in conversation, although documented in the repo |
| 2 | Reported a fix in terms of deferred triggers and TRUNCATE | "give me a summary for someone who has no idea what you are doing" | report assumes the human followed the work; detail without consequence |
| 3 | Defined a project term ("this record can never change") on its own | "then how can a mid-run change happen at all?" | term defined without its relation to the neighbouring concept |
| 4 | Called something "frozen" while the code already used "frozen" for something else | "does frozen mean X or Y?" | agent-coined label, ambiguous with an existing term |
| 5 | Drew an architecture diagram in its own group names and code identifiers (its own nickname for two entry points, role names) | three rounds of "hard to understand", "use semantic terms" | coined labels and code terms in an overview; the human later adopted "door" — coined names stick |
| 6 | Split a chart into "output" vs "reading" groups | "I don't understand the difference" | coined grouping presented as standard; obvious things explained, non-obvious skipped |
| 7 | Wrote folder READMEs mentioning "stage 1 / stage 3" | "no stage 2?? what do these stages refer to?" | undefined internal labels in a signpost |
| 8 | Listed uncommitted files, guessed "probably unfinished work" | "I thought that was already in the PR — I tested it" | guess not marked as a guess; human's picture differed from the real state and nobody said so |
| 9 | Gave a 10-minute estimate for a job that ran 7 hours; documented a defect instead of fixing it | "it's been 7h"; "make it actually 10 years" | expectation stated as fact |

Requests the human made repeatedly: an executive summary first (what works now, what it means); semantic names over code identifiers in charts and READMEs; state what is not obvious and cut what is; documents that describe the final state, not its history; "are we over-engineered?" passes against side scripts, manifests and extra layers; status reports that stand alone (asked "where are we at now?" about ten times in one long run).

## Goals

1. **Communication (A).** The human can follow every report, handoff and status update without having watched the work: terms are explained on first use, coined names are marked as coined, verified is separated from expected, and the human's picture is corrected when it differs from reality.
2. **Inspectable code (B).** Names, files, modules, comments and diagrams can be understood by a human reading them cold, using established or project terms rather than terms invented during implementation.
3. **Long-term comprehensibility (C).** A human returning after six months can rebuild their understanding from the repository alone: documents describe the current state, each fact lives in one place, and decisions keep their reasons.

## Non-goals

- Agent behaviour that superpowers already governs: evidence before completion claims (`verification-before-completion`), YAGNI in features (`test-driven-development`, `receiving-code-review`), brainstorming conversation style (`brainstorming`). technobabble points to these, never restates them.
- Project-specific conventions. A project's own instruction file (AGENTS.md, CLAUDE.md) and project skills win over technobabble wherever they say more.
- Prose style in general (grammar, Strunk-style rules).
- Harnesses other than Claude Code in version 1. Wording stays harness-neutral so a port (as superpowers has for Codex and others) stays possible.

## Anti-goals (it fails if…)

- **Replies get longer without getting clearer.** Padding, "Note:" labels, or explaining what the reader can plainly see. Readability is not verbosity.
- **Precision is lost.** Technical terms replaced with vague approximations instead of being explained.
- **It patronises.** Explaining basics the human has shown they know. Competence in one area is not knowledge of another — calibrate per subject.
- **It nags.** Ritual disclaimers on every message ("this term is mine") when nothing new was coined.
- **It duplicates superpowers or a project's own rules**, so two texts drift apart.

## Constraints

- Packaged as a Claude Code plugin in its own repository, which is also its own marketplace: the public GitHub repository `ASbrt/technobabble`, meant as a library anyone can install. Public means no git credentials are needed to install it. *Designer's call:* MIT licence, matching superpowers.
- Declares superpowers as a plugin dependency, so installing technobabble installs superpowers. superpowers lives in another marketplace (`superpowers-marketplace`, `obra/superpowers-marketplace`), so technobabble's `marketplace.json` must list it under `allowCrossMarketplaceDependenciesOn`; without that the plugin fails to load (Claude Code docs: plugins/dependencies).
- Built with the superpowers `writing-skills` method: baseline failure observed before each piece of guidance is written (see Testing).
- Size budgets from `writing-skills` (references/discovery.md): the always-on core under about 200 words; `reporting-work` and `naming-and-structuring` under 500 words each. *Designer's call:* `writing-docs` is a port of a skill already in use, so its content wins over the budget; it may go over 500 words or move its examples and record shapes into a reference file next to `SKILL.md`.

## Approaches considered

**Packaging** (chosen: 1)
1. *Own repository as a plugin with a session start hook* — one source of truth, on in every project, updates arrive by plugin update. Costs a second repository and a little packaging.
2. *Skill inside one project repository, linked from the home folder* — cheapest, but a general skill lives in a project repo, the core exists twice and drifts, and other projects depend on one checkout's path.
3. *Copies per project* — dropped: drift with no sync.

**Where the rules take effect** (chosen: always-on core + skills at specific moments). The core failure (assuming shared context, unexplained terms) happens in ordinary replies, so it must be always on; but a 2,500-word always-on text gets followed in mood, not in detail, and a third of it is about code.

**Division into skills** (chosen: A)
- **A. Core + three focused skills** — each loads only at its moment and stays short; costs three descriptions that must each trigger reliably.
- B. Core + one large skill — one thing to maintain, but its description must cover unlike moments and it loads everything every time.
- C. Core only, details in reference files — leanest, but depends on the agent noticing it needs the reference, which is exactly when the transcripts show it does not.

## Version 1 scope

The baseline tests (`tests/results/baseline-summary.md`) narrowed version 1. In short scenarios today's agents passed 10 of 11; after a long session of terse messages they failed three ways: project terms left unexplained, the agent's own labels used in an overview for newcomers, and untested work not called untested. Reports for an outsider passed every time. Following the rule that no guidance is written without an observed failure, version 1 ships:

- the **core**, limited to: assume the human did not see the work; explain terms on first use; mark coined names; say what was and was not checked; keep precision; project rules first;
- **`naming-and-structuring`**, limited to overviews and diagrams (plain labels, code names beside them) — and only if the core alone does not fix scenario 14;
- **`writing-docs`**, the port.

`reporting-work` and the code-structure and comment rules of `naming-and-structuring` are postponed until a scenario shows them failing. The rest of this design describes the full intent; where it differs from this section, this section wins for version 1.

## Design

### Repository layout

```
technobabble/
├── .claude-plugin/
│   ├── marketplace.json     # name: technobabble; one plugin, source "./"; allowCrossMarketplaceDependenciesOn: [superpowers-marketplace]
│   └── plugin.json          # name, version, description, dependencies: superpowers@superpowers-marketplace
├── hooks/
│   └── hooks.json           # SessionStart, matcher "startup|clear|compact" (not resume: a resumed session already holds the core); command `cat "${CLAUDE_PLUGIN_ROOT}/core.md"`, whose plain-text output Claude Code adds to the context. The same command works in sh, Git Bash and PowerShell (where `cat` is Get-Content), so it runs on macOS, Linux and Windows
├── core.md                  # the always-on text (single source; the hook reads it)
├── skills/
│   ├── reporting-work/SKILL.md
│   ├── naming-and-structuring/SKILL.md
│   └── writing-docs/SKILL.md
├── tests/                   # baseline and with-skill scenarios, results (see Testing)
├── docs/specs/              # this document
├── LICENSE                  # MIT
└── README.md                # for any reader: what it is and why, install, "requires superpowers", project setup
```

### The always-on core

Loaded by the hook into every new session and again after `/clear` and context compaction. Draft content (wording is the builder's, to be micro-tested):

1. The human did not watch your work. Every report must make sense to someone who saw none of your tool calls or reasoning.
2. The first time a term appears in this conversation — a project term too, even if it is documented in the repo — say what it is in a clause, and how it relates to the concept next to it.
3. When you coin a name, label or grouping, say once that it is yours and what it stands for. Prefer an existing project term or established term to a new one.
4. Separate what you checked, what you inferred and what you expect. A guess is labelled a guess; an estimate says what it rests on.
5. Lead with what changed for the application and what it means, then the details the human needs to judge it.
6. If what the human seems to believe differs from the actual state, say so directly.
7. Precision stays: explain technical terms, do not replace them with vaguer words. Do not explain what the human has shown they know.
8. Then: `technobabble:reporting-work` for handoffs and status reports, `technobabble:naming-and-structuring` for names, structure, comments and diagrams, `technobabble:writing-docs` for documentation. A project's own rules (AGENTS.md, CLAUDE.md, project skills) come first.

### The three skills

Descriptions start "Use when…" and name triggering moments only, never the workflow (`writing-skills` rule).

**`reporting-work`** — *Use when ending a task or a long stretch of autonomous work, giving a status update, handing work over, or asking the human to decide between options.*
- Executive summary first: what works now, what it means for the application, what is not done.
- Then: what changed and why this approach; how the parts interact, if the human needs it to judge the change.
- Verified / not verified / expected, stated for the human. *How* to verify is `superpowers:verification-before-completion`; this skill only covers saying it.
- Correct the human's picture when it differs from reality (case 8).
- For long-running work: a status that stands alone (what is running, how far, what the estimate rests on).
- For decisions: each option with its practical consequence, and a pick.
- Detail without consequence for the human is offered, not included (case 2).

**`naming-and-structuring`** — *Use when naming something (a variable, function, module, folder, table, concept), creating or reorganising files and modules, writing comments or docstrings, or drawing a diagram or chart for the human.*
- Names say what the thing is for in the domain, using project terms; check a new name does not collide with an existing meaning (case 4).
- A coined term that will stay (it appears in code or docs) is defined where the project defines terms (its glossary, if any).
- Diagrams and overviews use semantic labels, not code identifiers; every group says why it is separate from its neighbours (cases 5–7).
- Structure reflects the architecture, not the order the work happened in. No side scripts, manifests or extra layers beside the main structure without a stated reason (the human's "over-engineered?" passes). Feature-level YAGNI stays with superpowers.
- Comments say why, not what; document invariants and non-obvious choices that would otherwise look like accidental complexity.

**`writing-docs`** — generalised from a documentation skill already in use in one of the owner's projects. *Use when writing or editing documentation: a README, a reference document, a decision record, a glossary.*
- The three kinds of document: signpost (what a folder is for, its rules, an index), catalogue (one section per thing, standing alone), record (a decision or open question, fixed shape).
- One fact, one place: explain a concept where it is owned; elsewhere one clause and a link.
- Describe the current state only; history goes in commit messages.
- The cutting pass: remove summaries of what follows, what the reader sees anyway, filler and labels; keep what is not obvious.
- If two documents disagree, report it rather than fixing it silently.
- Projects say which of their files is which kind and who owns which fact — in their own instructions, not here.

### Where the existing draft's sections went

| Draft section | Goes to |
|---|---|
| 1 Shared context | core 1, 6 |
| 2 Terminology discipline | core 2–3; `naming-and-structuring` |
| 3 Explain through consequences | core 5; `reporting-work` |
| 4 Precision | core 7; completion claims → superpowers `verification-before-completion` |
| 5 Calibration | core 7 (brainstorming covers it inside brainstorming) |
| 6 Transparency and uncertainty | core 4; `reporting-work` |
| 7 Progress updates and handoffs | `reporting-work` |
| 8 Conciseness | anti-goals; `writing-docs` cutting pass |
| 9 Code legibility | `naming-and-structuring`; documentation parts → `writing-docs`; restraint → superpowers YAGNI plus the side-structure rule |


## Testing

Following `superpowers:writing-skills` (RED → GREEN → REFACTOR), separately for the core and each skill:

1. **Baseline (RED).** Turn the nine cases into scenarios with neutral, invented projects that recreate the situation (a codebase or long task history the agent knows and the "human" does not). Run each without technobabble — superpowers installed, since that is the real setting — and record the agent's output verbatim. A case that does not fail at baseline gets no guidance. *Designer's call on scope:* this gate applies to the core and to the new guidance in `reporting-work` and `naming-and-structuring`, each bullet of which must trace to a failing scenario or be dropped. The ported `writing-docs` content is not re-gated (it is in use already); it gets the triggering and anti-goal checks below and one baseline scenario to confirm it changes behaviour at all.
2. **Micro-tests of wording.** For the core: one fresh-context sample per call, 5+ repetitions per variant, always with a no-guidance control; read every flagged output by hand.
3. **With guidance (GREEN).** Same scenarios with technobabble; the failure must be gone.
4. **Anti-goal checks.** On the same runs, compare reply length and check for padding, lost precision, patronising explanations and ritual disclaimers. A version that fixes the failure but trips an anti-goal is not done.
5. **Triggering.** For each skill, prompts that should and should not load it; check it loads at its moment and not otherwise.
6. **Install check.** `claude plugin validate .`; a local `claude plugin marketplace add .`; a fresh project with the plugin enabled shows the core in context and superpowers installed.

Scenarios and recorded outputs live in `tests/`. Raw transcripts of the owner's projects are never committed.

## Left to the builder

- Exact wording of the core and the skills, within the budgets and as shaped by the tests.
- The hook script's language (superpowers uses a small script; anything with no install step is fine).
- How scenarios are run and scored (subagents or direct API calls), as long as the controls and manual reading above hold.
- Version numbering and the git tag scheme the dependency resolver expects (`<plugin>--v<version>`).
- Creating and pushing the GitHub repository happens only on the owner's go-ahead.

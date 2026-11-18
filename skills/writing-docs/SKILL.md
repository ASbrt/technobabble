---
name: writing-docs
description: Use when writing or editing documentation - a folder README, a reference document, a decision record (ADR), an open question, a glossary, or a project's agent instructions file.
---

# Writing docs

Readers arrive anywhere, often months later and without the context the author had. So each document says only its own thing and the rest is one link away: a concept is explained once, where it is owned, and elsewhere gets one clause and a link. The project's own instructions say which of its files is which kind and who owns which fact; where they say more than this skill, they win.

## First: which kind of document is it?

| Kind | Examples | Read how |
|---|---|---|
| **Signpost** | a folder's `README.md`; the top-level `README.md`; the agent instructions file | first, to get one's bearings |
| **Catalogue** | a data model, a list of settings, a glossary, a deployment guide | one section at a time, when it is needed |
| **Record** | a decision record (ADR), an open question | to learn what was decided or is open, when and why |

A README that also holds catalogue content keeps it below the signpost's three parts, under its own headings. Then write each document as exactly the parts below, in order.

## A signpost is three parts

1. **What this folder is for, and why it is separate from its neighbours.** One or two sentences a newcomer can act on ("the importer lives here because it is the only code that writes to the stock tables").
2. **The rules you must not break here.** One line each, each linked to the document that gives its reason.
3. **The index.** A table with one row per file and subfolder: a link to it and what it is for, in one line. Add a column when the folder has one (status, owner). A row uses only terms parts 1 and 2 explain: if it says "stage 3", part 1 says what the stages are.

Nothing else. How to do a task belongs to the catalogue that owns it; a signpost that teaches it drifts from it. Link there.

```markdown
# Migrations

The database schema as plain SQL files, applied in order by the migration runner one folder up. They sit here because the runner is the only code that reads them.

- Forward only: a merged file is never edited or renumbered; a mistake is fixed by a new migration ([ADR-007](../docs/decisions/007-migrations.md)).

| File | What it adds |
|---|---|
| [001_create_accounts.sql](001_create_accounts.sql) | the `accounts` table |
```

## A catalogue section stands alone

Each section has the same parts as its siblings (for a table: purpose, columns, rules with where each is enforced, link to the code; for a glossary term: one to three sentences). A reader who jumps straight to the section can use it. The introduction says what the document covers and how it is organised, nothing more.

**One fact, one owner.** A fact lives in the document that owns its kind (what a table means, why something was decided, what a word means); other places give one clause and a link. A section that lists rules across things names each in a clause and links to its owner. A reason of one clause stays even when a decision record gives the full argument: the reader needs *why* before *what*.

A section that is mostly columns, values and rules shrinks little. That is expected; do not cut specification to make it shorter.

## A record is short and fixed in shape

**Decision record (ADR)**, in this order:

1. Title; `**Status:**`, `**Date:**`, `**Revised:**` when the wording was changed, and lines linking it to other records (`**Amends:**`, `**Supersedes:**`, `**Resolves:**`).
2. One unlabelled sentence that says what was decided.
3. `## Context`: the problem that forced a decision.
4. `## Decision`: what was decided, one short item per decision, each with its reason.
5. `## Alternatives considered`: each rejected option and why.
6. `## Consequences`: what is easier, harder or still open because of it.

The Decision says *what* and *why*. Commands, flags and step-by-step procedures are the *how*: they live in a catalogue or in the code, and the record links there.

**Open question:** the question, why it matters, the options, what is already fixed, what must happen before it can be resolved, its status, and who decides.

**When you rewrite a record,** what it decided, when, why, and the alternatives it rejected are fixed: the new text has the same items and reasons in fewer words, and adds none. If it got longer, mechanics crept in. Add a `**Revised:**` line.

## Describe the current state

Documents say what is true now. History ("in phase 2 we…", "previously", "not yet", "as of milestone 3") goes in commit messages, or in a record's `Revised:` line.

## Before you finish: the cutting pass

| If a passage is… | It goes… |
|---|---|
| explained in another document | there; leave one clause and a link |
| history or plan-era wording | into the commit message or the `Revised:` line |
| mechanics in a record | into the catalogue that owns them; link it. Naming the file or check that enforces a rule stays |
| a summary of the section it introduces | away |
| what the reader sees anyway (what a table plainly shows, what a heading already says) | away; spend the words on what is not obvious: why two things are separate, what goes wrong if you ignore it |
| filler ("exactly", "deliberately", "that is", "in other words") | away, when the sentence works without it |
| a label such as "In plain words:" | away; the first sentence does that job unlabelled |

Then reread as a newcomer: every term is either explained here or linked (to the glossary, if the project has one). If one is not, add a link, not a paragraph.

If two documents disagree, or a claim does not match the code, do not fix it silently while cutting: report it, and let the maintainer decide which is right.

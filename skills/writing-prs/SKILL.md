---
name: writing-prs
description: Use when writing or editing a pull request or merge request description, or an issue, for people who were not in the session.
---

# Writing pull requests

A pull request is read by someone who was not there: a teammate reviewing it tomorrow, or the person who finds it with `git blame` a year from now. They have the diff and this text, not your session, and often not the project's vocabulary. Write for them. A project's own PR template and conventions come first; this fills what they leave open.

## Open with what changes and why

The first two or three sentences say, in plain words, what someone can do (or what no longer goes wrong) after this is merged, for whom, and why it was needed now. No component names, file paths or milestone codes yet. If the reader stopped there, they would know whether this PR matters to them.

## Explain each term once

- The first time a project term, internal component name or abbreviation appears, say in a few words what it is: "the intake queue (where new repair requests wait for a volunteer)".
- A decision record or open question is cited with what it decided, not by number alone: "ADR-007 (bookings are held for 15 minutes, then released)". Link it for detail.
- Labels coined during the session ("plan B", "the double-hold bug") are not shared vocabulary: describe the thing instead.
- Do not explain what any developer on the team knows, and do not add a glossary section.

## One topic

A pull request does one thing. If unrelated changes ride along, list them in their own short section and say they are unrelated, or move them to their own PR.

## Then the parts reviewers use

Keep these short; skip one that has nothing to say.

- **What changes**: grouped by what it does for the reader, not file by file. The diff already lists files.
- **Where to start reviewing**: the two or three files or decisions that matter most.
- **Testing**: what was run and passed, what was only tried by hand, and what was not tested. Say which is which.
- **Left out or open**: what was deliberately not done, and known follow-ups.

## Check before posting

Read the description as the teammate who was not there. Every sentence should make sense without the session; every term should be either common, explained where it first appears, or linked with what it decided.

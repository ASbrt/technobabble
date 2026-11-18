# technobabble

A Claude Code plugin that keeps an AI agent's work understandable to the person working with it, and to whoever reads that work later.

## Why it exists

Give a coding agent a few hours of autonomous work and its writing drifts into the private language of that session. It writes "ord" and "epoch" for hours before anyone asks what they are. A name it made up an hour ago ("the recon pass") turns up as a label in a diagram. It reports a change as done when it was never run. Its comments read "fixes the Gordon tie", its pull requests open with "R3: standing bookings at the T-2 freeze", and new features pile into a file that already does five jobs. None of this is wrong, exactly; it just assumes the reader was there.

The person reading usually was not. A teammate reviews the pull request, a maintainer opens the file a year later, and even the person who sat next to the agent has forgotten what "plan B" meant by the next morning.

technobabble is a short set of rules, loaded into every session, plus two skills for the moments when an agent writes for someone else. In its tests, today's agents did fine on short, fresh tasks and slipped in exactly these ways after a long session; with technobabble, those slips went away. The tests are small (three to five runs per case, judged by hand), so read [the results](tests/RESULTS.md) as evidence, not proof.

## What it does

**In every session** ([core.md](core.md)), the agent:

- explains a project term, identifier or abbreviation in a few words the first time it uses it, unless you used it yourself;
- says when a name is one it made up, or describes the thing plainly instead;
- says what it ran or checked and what it did not;
- documents code for someone new to it: what each module is for, what each function takes, returns and how it works, short comments for the steps, the reason wherever the code rests on something it does not show, and never the history of the session;
- puts code with a job of its own in its own file, keeps the data code reads next to it, and tells you when a file or the folder tree mixes things that belong apart;
- keeps technical terms precise and explains them, rather than swapping in vaguer words.

**When it writes documentation** ([writing-docs](skills/writing-docs/SKILL.md)): READMEs, reference documents, decision records and glossaries that stay readable months later.

**When it opens a pull request or files an issue** ([writing-prs](skills/writing-prs/SKILL.md)): what changes and why first, each term explained once, unrelated changes kept apart, where to start reviewing, and what was and was not tested.

It works together with [superpowers](https://github.com/obra/superpowers) and installs it as a dependency. superpowers covers how the agent works (planning, test-driven development, checking before claiming success); technobabble covers how that work is explained.

A project's own instructions (AGENTS.md, CLAUDE.md, its own skills) come first. To turn a rule off for one project, say so there: a line in AGENTS.md such as "Comment only why something is done; no docstrings" replaces the comment rule. To turn technobabble off entirely, disable the plugin.

## Install

In Claude Code, add both marketplaces, then install technobabble; superpowers comes with it:

```
/plugin marketplace add obra/superpowers-marketplace
/plugin marketplace add ASbrt/technobabble
/plugin install technobabble@technobabble
```

The superpowers marketplace has to be added first: Claude Code installs a dependency only from a marketplace it already knows.

It runs on macOS, Linux and Windows: the rules and skills are Markdown, and the hook that loads the rules is a single `cat` command that works in sh, Git Bash and PowerShell. It has been tested on macOS only.

## Use it in a project

To have everyone working in a repository use technobabble, add this to the repository's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "superpowers-marketplace": { "source": { "source": "github", "repo": "obra/superpowers-marketplace" } },
    "technobabble": { "source": { "source": "github", "repo": "ASbrt/technobabble" } }
  },
  "enabledPlugins": {
    "technobabble@technobabble": true
  }
}
```

This makes the plugin known, not installed: each person trusts the folder when Claude Code asks, then runs once:

```
claude plugin install technobabble@technobabble --scope project
```

## How it was tested

Each rule was added only after agents failed a scenario without it, and kept only if the failure went away without making anything else worse. [tests/](tests/README.md) has the scenarios and how to run them; [tests/RESULTS.md](tests/RESULTS.md) has what happened.

## Licence

MIT, see [LICENSE](LICENSE).

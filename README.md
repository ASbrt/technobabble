## Why this exists

Give a coding agent autonomous work and its writing drifts into the language of that session. A term it made up suddenly is a label everywhere, its comments reference some bug you never heard of, new features pile into a file that already does five jobs. Code is uncommented or littered with unhelpful mentions that a human evaluator can not place without digging through project history. It's not necessarily wrong, but for a reader it just amounts to a bunch of technobabble.

## What this does
This repository contains a short set of rules, loaded into every session, plus two skills for the moments when an agent writes for someone else. They are meant to make the agent express itself clearly as well as document and organize code so that anyone could jump in and follow immediately. In my tests, agents did fine on short, fresh tasks but slipped in longer sessions. With the added skills those slips went away. The tests are small so far (three to five runs per case, judged by hand), so read [the results](tests/RESULTS.md) as evidence, not proof.

**In every session** ([core.md](core.md)), the agent:

- explains a project term, identifier or abbreviation in a few words the first time it uses it, unless you used it yourself,
- says when a name is one it made up, or describes the thing plainly instead,
- says what it ran or checked and what it did not,
- documents code for someone new to it: what each module is for, what each function takes, returns and how it works, short comments for the steps, the reason wherever the code rests on something it does not show. Session history is banned,
- puts code with a job of its own in its own file, keeps the data code reads next to it, and tells you when a file or the folder tree mixes things that belong apart, 
- keeps technical terms precise and explains them, rather than swapping in vaguer words.

**For writting documentation** ([writing-docs](skills/writing-docs/SKILL.md)): READMEs, reference documents, decision records and glossaries that stay readable.

**For writing PRs** ([writing-prs](skills/writing-prs/SKILL.md)): what changes and why first, terms explained once, unrelated changes kept apart, where to start reviewing, and what was and was not tested.

It works together with [superpowers](https://github.com/obra/superpowers) and installs it as a dependency. superpowers covers how the agent works (planning, test-driven development, checking before claiming success); technobabble covers how that work is explained.

A project's own instructions (AGENTS.md, CLAUDE.md, its own skills) come first. To turn a rule off for one project, say so there: a line in AGENTS.md such as "Comment only why something is done; no docstrings" replaces the comment rule. 

## Install

In Claude Code, add both marketplaces, then install technobabble; superpowers comes with it:

```
/plugin marketplace add obra/superpowers-marketplace
/plugin marketplace add ASbrt/technobabble
/plugin install technobabble@technobabble
```

The superpowers marketplace has to be added first: Claude Code installs a dependency only from a marketplace it already knows.

It should run on macOS, Linux and Windows: the rules and skills are Markdown, and the hook that loads the rules is a single `cat` command that works in sh, Git Bash and PowerShell. Tested on macOS only.

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

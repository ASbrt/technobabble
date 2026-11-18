# technobabble

A Claude Code plugin that keeps an AI agent's work understandable to the human who supervises it.

After a long stretch of autonomous work, agents write as if the human had watched it: project terms go unexplained, names the agent made up turn into labels, untested work is not marked as such, comments lean on the session, and code piles into files and folders that already hold too much. In tests (see [tests/](tests/README.md)), today's agents did fine on short, fresh tasks and slipped in exactly these ways after a long session. technobabble adds a short set of rules to every session, which removed those failures in the same tests without making short answers longer. The tests are small (3 to 5 runs per case, judged by hand): treat them as evidence, not proof.

It works together with [superpowers](https://github.com/obra/superpowers) and installs it as a dependency: superpowers covers how to work (planning, test-driven development, verifying before claiming success); technobabble covers how the work is explained.

## What is in it

| Part | What it does | When it applies |
|---|---|---|
| [core.md](core.md) | six rules: explain terms on first use; mark names you made up; say what you checked and what you did not; put the reason for non-obvious code in a comment anyone can follow; keep code and the data it reads together, and say when the files or folders don't; keep precision | every session, loaded by a session start hook |
| [skills/writing-docs](skills/writing-docs/SKILL.md) | how to write READMEs, reference documents, decision records and glossaries so they stay understandable months later | when the agent writes or edits documentation |

A project's own instructions (AGENTS.md, CLAUDE.md, its own skills) always take precedence.

It runs on macOS, Linux and Windows: the skills are Markdown, and the hook is a single `cat` command that works in sh, Git Bash and PowerShell. It has been tested on macOS only.

## Install

In Claude Code, add both marketplaces, then install technobabble; superpowers comes with it:

```
/plugin marketplace add obra/superpowers-marketplace
/plugin marketplace add ASbrt/technobabble
/plugin install technobabble@technobabble
```

The superpowers marketplace has to be added first: Claude Code installs a dependency only from a marketplace it already knows.

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

## Licence

MIT, see [LICENSE](LICENSE).

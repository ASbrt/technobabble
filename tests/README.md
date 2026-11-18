# Tests

technobabble's guidance is tested the way superpowers' `writing-skills` asks: watch an agent fail without the guidance, add the guidance, watch the failure go away, and check it made nothing else worse. Agent replies cannot be checked by a script, so every reply is read and judged by hand against the scenario's checklists.

| Path | What it is |
|---|---|
| [hook/test-session-start.sh](hook/test-session-start.sh) | the one automatic test: the session start hook's command prints `core.md` unchanged, works in any shell, and stays under Claude Code's 10,000-character limit for hook output |
| [scenarios/](scenarios/) | one file per situation in which agents lose their human; each says what failing and passing look like |
| [runner-prompt.md](runner-prompt.md) | the prompt a fresh subagent gets for one run |
| [fill-runner.py](fill-runner.py) | assembles that prompt from a scenario and the guidance under test |
| [results/](results/) | every reply, verbatim, with its verdict |
| [triggering.md](triggering.md) | whether each skill's description makes it load at the right moments |

## Running a scenario

```bash
export SUPERPOWERS_ROOT=<path to the superpowers plugin>
python3 tests/fill-runner.py tests/scenarios/01-unexplained-project-term.md            # baseline
python3 tests/fill-runner.py tests/scenarios/01-unexplained-project-term.md core.md    # with the core
```

Give the printed prompt to a fresh subagent with no tools and nothing else in its context, one subagent per run. Every variant includes superpowers' `using-superpowers` text, because technobabble always runs next to superpowers.

- **Repetitions:** 3 per variant for scenarios; 5 when testing the wording of the core. The control is the scenario's 3 baseline runs.
- **Judging:** tick each line of the scenario's `Fails if`, `Passes if` and `Anti-goals` lists. A run passes when no `Fails if` line applies, every `Passes if` line holds and no anti-goal is tripped.
- **Recording:** `results/<NN>/<variant>-<n>.md`, variant one of `baseline`, `core`, `skill`, `core+skill`: the reply verbatim, then `## Verdict` with the ticked lists.

## Writing a scenario

Sections, in this order: `## Situation` (what the agent did and knows, written to the agent; an invented project; never a real transcript), `## Human message`, `## Fails if`, `## Passes if`, `## Anti-goals`, `## Guidance under test`. Only the first two reach the agent. The situation must not hint at what is being tested.

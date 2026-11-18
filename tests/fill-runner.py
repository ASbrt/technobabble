"""Builds the prompt for one scenario run, so every run is assembled the same way.

Usage: python3 tests/fill-runner.py <scenario file> [guidance file ...]

The guidance always starts with superpowers' using-superpowers skill (the real
setting technobabble runs in); its location comes from $SUPERPOWERS_ROOT.
Further guidance files (core.md, a SKILL.md) are appended in the order given.
No extra guidance files = a baseline run. Prints the prompt to stdout.
"""
import os
import re
import sys
from pathlib import Path

tests_dir = Path(__file__).resolve().parent


SECTIONS = "Situation|Human message|Fails if|Passes if|Anti-goals|Guidance under test"


def scenario_text(path: Path) -> str:
    """The parts of a scenario the agent may see: Situation and Human message.

    The checklists (Fails if, Passes if, Anti-goals) are for the judge only.
    """
    text = path.read_text()
    shown = []
    for heading in ("Situation", "Human message"):
        # A section ends at the next of the scenario's own headings, so "## " lines
        # inside the session (git output, quoted documents) do not cut it short.
        match = re.search(rf"^## {heading}\n(.*?)(?=^## (?:{SECTIONS})\n|\Z)", text, re.S | re.M)
        if not match:
            sys.exit(f"{path}: missing '## {heading}' section")
        shown.append(f"## {heading}\n{match.group(1).strip()}")
    return "\n\n".join(shown)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    superpowers_root = os.environ.get("SUPERPOWERS_ROOT")
    if not superpowers_root:
        sys.exit("Set SUPERPOWERS_ROOT to the superpowers plugin folder.")
    guidance_files = [Path(superpowers_root) / "skills/using-superpowers/SKILL.md"]
    guidance_files += [Path(p) for p in sys.argv[2:]]
    guidance = "\n\n".join(p.read_text().strip() for p in guidance_files)
    template = (tests_dir / "runner-prompt.md").read_text()
    prompt = template.replace("{{GUIDANCE}}", guidance)
    prompt = prompt.replace("{{SCENARIO}}", scenario_text(Path(sys.argv[1])))
    sys.stdout.write(prompt)


if __name__ == "__main__":
    main()

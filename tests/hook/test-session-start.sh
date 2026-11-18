#!/usr/bin/env bash
# Tests the SessionStart hook. Claude Code adds a SessionStart hook's plain-text
# output to the session's context, so the hook only has to print core.md. It must:
# - print core.md unchanged, with Claude Code's ${CLAUDE_PLUGIN_ROOT} substituted;
# - use a command that works in sh (macOS, Linux), Git Bash and PowerShell
#   (Windows without Git Bash), so no shell-specific syntax and no "shell" field;
# - stay under Claude Code's 10,000-character limit for hook output.
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
hooks_json="$repo_root/hooks/hooks.json"
failures=0

fail() { echo "FAIL $1"; failures=$((failures + 1)); }

hook_field() {  # $1: field name of the one SessionStart hook
  python3 -c '
import json, sys
hooks = json.load(open(sys.argv[1]))["hooks"]["SessionStart"]
assert len(hooks) == 1 and len(hooks[0]["hooks"]) == 1, hooks
print(hooks[0]["hooks"][0].get(sys.argv[2], ""))' "$hooks_json" "$1"
}

command="$(hook_field command)"

# Run the command as Claude Code does on macOS/Linux: substitute the plugin root, then sh -c.
plugin_copy="$(mktemp -d "${TMPDIR:-/tmp}/technobabble plugin.XXXX")"  # a space, as in real paths
printf 'line "one"\n\tback\\slash\n\nend\n' > "$plugin_copy/core.md"
substituted="${command//\$\{CLAUDE_PLUGIN_ROOT\}/$plugin_copy}"
if diff <(sh -c "$substituted") "$plugin_copy/core.md" >/dev/null; then
  echo "PASS test_prints_core_unchanged"
else
  fail "test_prints_core_unchanged: command was: $command"
fi
rm -rf "$plugin_copy"

# The same command must parse in PowerShell: only a command name and one quoted path.
if [[ "$command" =~ ^cat\ \"\$\{CLAUDE_PLUGIN_ROOT\}/[A-Za-z0-9_./-]+\"$ ]] && [ -z "$(hook_field shell)" ]; then
  echo "PASS test_command_is_shell_neutral"
else
  fail "test_command_is_shell_neutral: command '$command', shell '$(hook_field shell)'"
fi

# Claude Code caps hook output at 10,000 characters; past that the model sees only a preview.
size=$(wc -c < "$repo_root/core.md")
if [ "$size" -lt 10000 ]; then echo "PASS test_core_under_hook_limit ($size chars)"; else fail "test_core_under_hook_limit: $size chars"; fi

exit "$failures"

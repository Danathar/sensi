#!/usr/bin/env bash
# Format a Python file that Claude Code just edited.
#
# Wired to PostToolUse for Edit/Write in .claude/settings.json. The devcontainer
# formats on save; this is the equivalent for an agent, so a change never
# arrives at CI failing `ruff format --check` for a reason nobody had to think
# about.
#
# Deliberately never fails the tool call: a formatter that blocks an edit is
# worse than an unformatted file. Exits 0 on every path.

set -uo pipefail

command -v ruff >/dev/null 2>&1 || exit 0

# `python3 -c` rather than a heredoc: a heredoc would become python's stdin and
# the hook payload would never be read.
file_path="$(python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

print((payload.get("tool_input") or {}).get("file_path", ""))
' 2>/dev/null)"

case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac

[ -f "$file_path" ] || exit 0

# Import sorting and formatting only. A blanket `ruff check --fix` would also
# delete "unused" imports behind the author's back, which is a semantic change
# and not something a silent hook should make. Everything else is left for
# `/check` to surface explicitly.
ruff check --fix --select I --quiet "$file_path" >/dev/null 2>&1
ruff format --quiet "$file_path" >/dev/null 2>&1

exit 0

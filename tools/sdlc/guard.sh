#!/bin/sh
# Claude Code PreToolUse(Bash) hook: blocks merging or pushing an epic into the main branch until
# every SDLC gate is a fresh pass (tools/sdlc/sdlc.py check). Fast path exits for commands that
# cannot merge or push. Register it in .claude/settings.json (see README.md).
IN=$(cat)
case "$IN" in
  *merge*|*push*|*pull*) ;;  # wide on purpose: the python side parses flags, -C, extra spaces, gh api
  *) exit 0 ;;
esac
printf '%s' "$IN" | python3 "$(dirname "$0")/sdlc.py" guard

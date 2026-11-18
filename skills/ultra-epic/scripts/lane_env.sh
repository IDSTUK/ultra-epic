#!/bin/sh
# Prepare a cold worktree for an /ultra-epic lane. Idempotent and safe to run twice at once (ln -sfn).
#   - links each path in the profile's git.links (for example node_modules) from the main checkout,
#     when the main checkout has it and the worktree has no real copy;
#   - links the epic's test stack env file (profile stack.env_file) from the epic worktree, which owns
#     the epic's isolated stack. Never the main checkout's own env or settings files: they can hold
#     live credentials.
# A link to an install is only safe while the lockfile next to it matches the main checkout's. When it
# differs, the link is removed and the agent installs a real copy. Run with --remove before any install
# through a link: an install through a link would change the main checkout's packages.
# Usage: lane_env.sh <worktree> <main checkout> <epic worktree> [--remove]
set -e
WT=${1:?worktree path}
MAIN=${2:?main checkout path}
EW=${3:?epic worktree path}
MODE=${4:-}
HERE=$(cd "$(dirname "$0")" && pwd)
[ "$(cd "$WT" && pwd -P)" = "$(cd "$MAIN" && pwd -P)" ] && { echo "refusing: that is the main checkout" >&2; exit 1; }
prof() { (cd "$WT" && python3 "$HERE/delivery_profile.py" get "$1"); }
LINKS=$(prof git.links | python3 -c 'import json,sys; v=sys.stdin.read().strip(); [print(x) for x in ((json.loads(v) if v else None) or [])]')
ENV=$(prof stack.env_file)
EX=$(git -C "$WT" rev-parse --git-common-dir)/info/exclude
for p in $LINKS; do
  # a .gitignore entry like `node_modules/` does not match a symlink: exclude the bare name too
  grep -qx "$p" "$EX" 2>/dev/null || printf '%s\n' "$p" >> "$EX"
  if [ "$MODE" = "--remove" ]; then
    if [ -L "$WT/$p" ]; then rm "$WT/$p"; fi
    continue
  fi
  [ -e "$MAIN/$p" ] || continue
  [ -e "$WT/$p" ] && [ ! -L "$WT/$p" ] && continue        # a real install is there: keep it
  lock="$(dirname "$p")/package-lock.json"
  if [ -f "$MAIN/$lock" ] && ! cmp -s "$MAIN/$lock" "$WT/$lock" 2>/dev/null; then
    if [ -L "$WT/$p" ]; then rm "$WT/$p"; fi
    echo "skip $p: $lock differs from the main checkout's (install a real copy there)"
    continue
  fi
  mkdir -p "$(dirname "$WT/$p")"
  ln -sfn "$MAIN/$p" "$WT/$p" 2>/dev/null || [ -L "$WT/$p" ]
done
[ -n "$ENV" ] && [ "$ENV" != "null" ] || { echo "lane env ready (no stack.env_file in the profile): $WT"; exit 0; }
grep -qx "$ENV" "$EX" 2>/dev/null || printf '%s\n' "$ENV" >> "$EX"
if [ "$MODE" = "--remove" ]; then
  if [ -L "$WT/$ENV" ]; then rm "$WT/$ENV"; fi
elif [ "$(cd "$WT" && pwd -P)" = "$(cd "$EW" && pwd -P)" ]; then
  [ -f "$EW/$ENV" ] && echo "test stack: owned by $EW" || echo "no test stack yet: run the profile's stack.up in $EW"
elif [ -f "$EW/$ENV" ] && [ ! -L "$EW/$ENV" ]; then
  mkdir -p "$(dirname "$WT/$ENV")"
  ln -sfn "$EW/$ENV" "$WT/$ENV"
  echo "test stack: $WT/$ENV -> $EW/$ENV"
else
  echo "no test stack: run the profile's stack.up in the epic worktree $EW first" >&2
  exit 1
fi
echo "lane env ready: $WT"

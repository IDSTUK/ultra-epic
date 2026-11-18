#!/bin/sh
# One suite lock (v3): two lanes never run the same exclusive suite (a simulator or browser E2E suite,
# anything in the profile's harness.exclusive_suites) at the same time on this machine.
#   suite_lock.sh <suite> <cmd> [args]   run cmd while holding the lock for <suite>; exit with cmd's code
#   suite_lock.sh --status               list the locks held now, with their pids
# The lock is a directory (mkdir is atomic). A holder that died is detected by its pid and cleared.
# A wait gives up after UE_LOCK_WAIT seconds (default 540, fits one Bash call) with exit 75: call again.
set -eu
DIR=${UE_LOCK_DIR:-${TMPDIR:-/tmp}/ultra-epic-locks}
mkdir -p "$DIR"
if [ "${1:-}" = "--status" ]; then
  for l in "$DIR"/*.lock; do
    [ -d "$l" ] || continue
    echo "$(basename "$l" .lock) pid $(cat "$l/pid" 2>/dev/null || echo '?')"
  done
  exit 0
fi
[ $# -ge 2 ] || { echo "usage: suite_lock.sh <suite> <cmd> [args] | --status" >&2; exit 2; }
NAME=$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_')
shift
LOCK="$DIR/$NAME.lock"
WAIT=${UE_LOCK_WAIT:-540}
start=$(date +%s)
until mkdir "$LOCK" 2>/dev/null; do
  p=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then rm -rf "$LOCK"; continue; fi   # holder died
  if [ $(( $(date +%s) - start )) -ge "$WAIT" ]; then
    echo "suite_lock.sh: $NAME held by pid ${p:-?} for ${WAIT}s: call again" >&2
    exit 75
  fi
  sleep 2
done
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM
set +e
"$@"
rc=$?
exit $rc

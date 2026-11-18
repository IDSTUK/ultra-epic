#!/bin/sh
# Simulator capacity for parallel lanes on one Mac (worked example: Flying Start, iOS apps).
# Lessons from two runs:
#   - the CoreSimulator control plane is the limit, not RAM: parallel `simctl boot` silently does nothing,
#     and past about 6-8 booted simulators boot/install/launch hits the 30 s launch watchdog;
#   - load average is no measure: each booted simulator adds about 50 waiting processes, so the
#     generic capacity.sh reads CPU idle instead;
#   - never `simctl shutdown all`: it kills the other lanes' simulators.
#   capacity_sim.sh boot <udid>          boot one simulator (one boot at a time across all agents), wait until booted
#   capacity_sim.sh install <udid> <app> install an app under the same lock
#   capacity_sim.sh check                booted simulators against the limit
# UE_MAX_SIMS (default 8). Every wait gives up after 9 minutes (exit 1): call it again.
set -eu
LOCK=${TMPDIR:-/tmp}/ultra-epic-sim-setup.lock
MAX_SIMS=${UE_MAX_SIMS:-8}
HERE=$(cd "$(dirname "$0")" && pwd)
CAP="$HERE/../../skills/ultra-epic/scripts/capacity.sh"
booted() { xcrun simctl list devices booted | grep -c '(Booted)' || true; }

wait_slot() {
  i=0
  while [ "$(booted)" -ge "$MAX_SIMS" ]; do
    i=$((i + 1))
    if [ $i -gt 36 ]; then echo "capacity_sim.sh: $(booted)/$MAX_SIMS simulators booted after 9 min: call again" >&2; exit 1; fi
    sleep 15
  done
  [ -x "$CAP" ] || [ -f "$CAP" ] && sh "$CAP" wait
}

lock() {
  i=0
  until mkdir "$LOCK" 2>/dev/null; do
    p=$(cat "$LOCK/pid" 2>/dev/null || true)
    if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then rm -rf "$LOCK"; continue; fi  # holder died
    i=$((i + 1))
    if [ $i -gt 180 ]; then echo "capacity_sim.sh: setup lock held for 9 min by pid ${p:-?}: call again" >&2; exit 1; fi
    sleep 3
  done
  echo $$ > "$LOCK/pid"
  trap 'rm -rf "$LOCK"' EXIT INT TERM
}

case "${1:-}" in
  boot)
    [ -n "${2:-}" ] || { echo "usage: capacity_sim.sh boot <udid>" >&2; exit 2; }
    if xcrun simctl list devices booted | grep -q "$2"; then exit 0; fi
    wait_slot; lock
    xcrun simctl boot "$2"
    xcrun simctl bootstatus "$2" -b >/dev/null ;;
  install)
    [ -n "${3:-}" ] || { echo "usage: capacity_sim.sh install <udid> <app>" >&2; exit 2; }
    lock; xcrun simctl install "$2" "$3" ;;
  check) echo "booted simulators $(booted)/$MAX_SIMS" ;;
  *) echo "usage: capacity_sim.sh boot <udid> | install <udid> <app> | check" >&2; exit 2 ;;
esac

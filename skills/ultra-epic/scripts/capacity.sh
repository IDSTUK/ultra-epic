#!/bin/sh
# Capacity guard for parallel /ultra-epic agents on one machine (macOS or Linux).
# Agents run in parallel; this makes each one wait when the machine is full, instead of thrashing.
#   capacity.sh wait            block until CPU idle and free memory leave room for one more build or test run
#   capacity.sh bg <cmd> [args] run a light command at background priority (lint, drift checks, log scans)
#   capacity.sh check           print CPU idle, free memory, load and CPU count; exit 0
# Limits (env, or the profile's capacity block through the orchestrator):
#   UE_MIN_IDLE_PCT (default 15)  UE_MIN_FREE_PCT (default 10)
# Every wait gives up after 9 minutes (exit 1) so it fits one Bash call: then call it again.
# Load average is a poor measure on machines that run simulators or containers (many waiting
# processes at low CPU), so the guard reads CPU idle. Device-specific steps (one simulator boot at a
# time, and so on) belong in the profile; see examples/flying-start/capacity_sim.sh.
set -eu
MIN_IDLE=${UE_MIN_IDLE_PCT:-15}
MIN_FREE=${UE_MIN_FREE_PCT:-10}
OS=$(uname -s)

ncpu() { if [ "$OS" = Darwin ]; then sysctl -n hw.ncpu; else nproc; fi; }
idle() {
  if [ "$OS" = Darwin ]; then
    top -l 2 -n 0 -s 1 | awk '/CPU usage/{v=$7} END{print int(v)}'   # 2nd sample: the 1st is since boot
  else
    # two /proc/stat samples one second apart
    a=$(awk '/^cpu /{print $5+$6, $2+$3+$4+$5+$6+$7+$8}' /proc/stat); sleep 1
    b=$(awk '/^cpu /{print $5+$6, $2+$3+$4+$5+$6+$7+$8}' /proc/stat)
    echo "$a $b" | awk '{d=$4-$2; print (d>0)?int(100*($3-$1)/d):100}'
  fi
}
free_pct() {
  if [ "$OS" = Darwin ]; then
    memory_pressure -Q 2>/dev/null | awk -F': ' '/free percentage/{print int($2)}'
  else
    awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{print (t>0)?int(100*a/t):50}' /proc/meminfo
  fi
}
load1() { uptime | sed 's/.*load averages*: *//' | tr ',' ' ' | awk '{print $1}'; }

wait_room() {
  i=0
  while :; do
    c=$(idle); f=$(free_pct); f=${f:-50}
    if [ "$c" -ge "$MIN_IDLE" ] && [ "$f" -ge "$MIN_FREE" ]; then return 0; fi
    i=$((i + 1))
    if [ $i -gt 36 ]; then
      echo "capacity.sh: no room after 9 min (CPU idle ${c}% (min $MIN_IDLE), free ${f}% (min $MIN_FREE)): call again" >&2
      exit 1
    fi
    sleep 15
  done
}

case "${1:-}" in
  wait) wait_room ;;
  bg)
    shift
    if [ "$OS" = Darwin ] && command -v taskpolicy >/dev/null 2>&1; then exec taskpolicy -c background "$@"; fi
    exec nice -n 19 "$@" ;;
  check) echo "CPU idle $(idle)% (min $MIN_IDLE) free $(free_pct)% (min $MIN_FREE) load $(load1) cpus $(ncpu)" ;;
  *) echo "usage: capacity.sh wait | bg <cmd> | check" >&2; exit 2 ;;
esac

#!/bin/sh
# Serve a Vite web app from a worktree on the epic's local test stack, for a browser (Playwright) suite.
# Worked example (Flying Start's admin site), generalised: the app dir, env file and functions URL are settings.
#   serve_web_app.sh <worktree> <port>          start vite on 127.0.0.1:<port> only; returns once it listens
#   serve_web_app.sh <worktree> <port> --stop   stop only the server this script started there
#   serve_web_app.sh <worktree> --stop-all      stop every server the worktree recorded (run before removing it)
# Settings (env): APP_DIR (default rc-admin), ENV_FILE (default .localstack/harness.env),
#   FUNCTIONS_URL (default http://127.0.0.1:${PORT_FUNCTIONS:-8899}/.netlify/functions), LOCAL_PREFIX (default http://127.0.0.1).
# Lessons it encodes:
#   - --strictPort: vite without it moved to the next free port while the specs still named the old one,
#     so one lane's specs tested another lane's server. A taken port is an error, never another port;
#   - every port has its own pid file, and --stop checks the pid is still our vite (after a reboot the
#     number may belong to any process);
#   - each server has its own vite cache dir: a linked node_modules shares one, and each vite that starts
#     with another root deletes it under the servers that run;
#   - it refuses an env file whose SUPABASE_URL is not local.
set -e
APP_DIR=${APP_DIR:-rc-admin}
ENV_FILE=${ENV_FILE:-.localstack/harness.env}
LOCAL_PREFIX=${LOCAL_PREFIX:-http://127.0.0.1}
if [ "${2:-}" = "--stop-all" ]; then
  [ -d "$1/.localstack" ] || exit 0
  for f in "$1"/.localstack/webapp-*.pid; do
    [ -f "$f" ] || continue
    p=${f##*/webapp-}
    sh "$0" "$1" "${p%.pid}" --stop
  done
  exit 0
fi
WT=$(cd "${1:?worktree path}" && pwd -P)
PORT=${2:?port}
L=$WT/.localstack
mkdir -p "$L"
PID=$L/webapp-$PORT.pid
ARGS="--host 127.0.0.1 --port $PORT --strictPort"
ours() { [ -f "$PID" ] && case "$(ps -ww -p "$(cat "$PID")" -o command= 2>/dev/null || true)" in *"vite $ARGS"*) true ;; *) false ;; esac; }
stop() {
  if ours; then
    kill "$(cat "$PID")" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do ours || break; sleep 1; done
  fi
  rm -f "$PID"
}
stop
[ "${3:-}" = "--stop" ] && exit 0
. "$WT/$ENV_FILE"
case "${SUPABASE_URL:-}" in "$LOCAL_PREFIX"*) ;; *) echo "serve_web_app.sh: not a local stack: ${SUPABASE_URL:-unset}" >&2; exit 1 ;; esac
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "serve_web_app.sh: port $PORT is taken: stop that server or report it, never test another server" >&2
  exit 1
fi
cd "$WT/$APP_DIR"
if [ ! -e node_modules ]; then npm ci --no-audit --no-fund > "$L/webapp-npm.log" 2>&1; fi
export VITE_SUPABASE_URL="$SUPABASE_URL" VITE_SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY:-}"
export VITE_FUNCTIONS_BASE=${FUNCTIONS_URL:-http://127.0.0.1:${PORT_FUNCTIONS:-8899}/.netlify/functions}
export VITE_CACHE_DIR="$L/vite-cache-$PORT"
# shellcheck disable=SC2086
nohup ./node_modules/.bin/vite $ARGS > "$L/webapp-$PORT.log" 2>&1 &
echo $! > "$PID"
for _ in $(seq 60); do
  sleep 1
  ours || break
  if lsof -nP -a -p "$(cat "$PID")" -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "web app: http://127.0.0.1:$PORT from $WT (stop: serve_web_app.sh $WT $PORT --stop)"
    exit 0
  fi
done
stop
echo "serve_web_app.sh: vite did not start on $PORT (see $L/webapp-$PORT.log)" >&2
exit 1

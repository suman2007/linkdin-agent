#!/usr/bin/env bash
# LinkedIn agent - one-command launcher for Linux and macOS (same modes as run.bat).
#   ./run.sh            normal mode: asks before each like, loops every interval
#   ./run.sh dry        dry run: prints matches, never likes
#   ./run.sh once       single scan, then exit
#   ./run.sh watch      dry run with a visible browser + HTML scan report
#   ./run.sh login      only (re)login to LinkedIn
#   ./run.sh setup      choose jobs / companies / keywords / connections
#   ./run.sh jobs       find recent jobs (see jobs.yaml), write a report
#   ./run.sh applied <job link or id> ...   record jobs you applied to outside LinkedIn
set -u
cd "$(dirname "$0")"
export PYTHONUTF8=1

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python was not found. Install Python 3.11+ and retry."; exit 1
fi

# --- first run: private virtualenv (system pip is locked on most Linux distros), dependencies, browser
if [ ! -f .setup_done ]; then
  echo "Installing dependencies..."
  "$PY" -m venv .venv || { echo "Could not create .venv (on Debian/Ubuntu: sudo apt install python3-venv)"; exit 1; }
  .venv/bin/python -m pip install -q -r requirements.txt || { echo "pip install failed"; exit 1; }
  .venv/bin/python -m playwright install chromium || { echo "browser install failed"; exit 1; }
  if [ "$(uname)" = "Linux" ]; then
    echo "If Chromium fails to start, install its system libraries once:  sudo .venv/bin/python -m playwright install-deps chromium"
  fi
  echo done > .setup_done
fi
PY=.venv/bin/python

# a visible browser needs a display; on a headless server run:  xvfb-run -a ./run.sh <mode>
needs_display() {
  [ "$(uname)" = "Linux" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]
}

mode="${1:-}"
case "$mode" in
  login)   exec "$PY" -m agent login ;;
  setup)   "$PY" -m agent setup; exit $? ;;
  applied) shift; "$PY" -m agent applied "$@"; exit $? ;;
esac

# --- API key check (key lives in .env, which is gitignored)
if [ ! -f .env ]; then
  echo "Missing .env. Run: cp .env.example .env  then set NVIDIA_API_KEY=nvapi-... or ANTHROPIC_API_KEY=sk-ant-..."; exit 1
fi
if ! grep -Eq '^(NVIDIA_API_KEY=nvapi-|ANTHROPIC_API_KEY=sk-ant-)' .env; then
  echo ".env must contain NVIDIA_API_KEY=nvapi-... or ANTHROPIC_API_KEY=sk-ant-..."; exit 1
fi

case "$mode" in
  jobs)  ARGS=(jobs --open) ;;
  dry)   ARGS=(run --once --notify-only) ;;
  once)  ARGS=(run --once) ;;
  watch) ARGS=(run --once --notify-only --show --open) ;;
  "")    ARGS=(run) ;;
  *)     echo "Unknown mode '$mode'. Modes: dry once watch login setup jobs applied"; exit 1 ;;
esac

if needs_display && { [ "$mode" = "watch" ] || [ "$mode" = "" ] || [ "$mode" = "once" ]; }; then
  echo "Note: no display found. Company pages need a visible window; use  xvfb-run -a ./run.sh $mode  on a server."
fi

"$PY" -m agent "${ARGS[@]}"
rc=$?
if [ $rc -ne 0 ]; then
  echo; echo "Failed (maybe not logged in). Trying login..."
  "$PY" -m agent login || exit 1
  "$PY" -m agent "${ARGS[@]}"
fi

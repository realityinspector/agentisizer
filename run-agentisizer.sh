#!/usr/bin/env bash
# run-agentisizer.sh – the one command. Builds the environment if needed,
# then forwards everything to the CLI.
#
#   ./run-agentisizer.sh setup      get Sonic Pi installed and running
#   ./run-agentisizer.sh doctor     check what's working
#   ./run-agentisizer.sh demo       90-second tour
#   ./run-agentisizer.sh start      run the soundtrack
#   ./run-agentisizer.sh say "tests passed"
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"
STAMP="$VENV/.requirements.sha"

if command -v tput >/dev/null && [[ -t 1 ]]; then
  RED=$(tput setaf 1); YELLOW=$(tput setaf 3); BOLD=$(tput bold); RESET=$(tput sgr0)
else
  RED=""; YELLOW=""; BOLD=""; RESET=""
fi

# Some Homebrew Pythons ship an ensurepip that imports fine but fails when
# run, so the only trustworthy probe is building a throwaway venv.
pick_python() {
  local candidates=("${PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3)
  local probe c
  probe="$(mktemp -d)"
  trap 'rm -rf "$probe"' RETURN
  for c in "${candidates[@]}"; do
    [[ -z "$c" ]] && continue
    command -v "$c" >/dev/null 2>&1 || continue
    rm -rf "$probe/v"
    if "$c" -m venv "$probe/v" >/dev/null 2>&1; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

if [[ ! -x "$VENV_PY" ]]; then
  if ! PY="$(pick_python)"; then
    printf "${RED}✗${RESET} No usable Python 3 (needs a working venv + ensurepip).\n" >&2
    printf "  Install one with: ${BOLD}brew install python@3.12${RESET}\n" >&2
    exit 1
  fi
  printf "Creating virtual environment (%s) …\n" "$("$PY" -V 2>&1)"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
fi

if [[ "$(cat "$STAMP" 2>/dev/null)" != "$(shasum requirements.txt | awk '{print $1}')" ]]; then
  printf "Installing dependencies …\n"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r requirements.txt
  shasum requirements.txt | awk '{print $1}' > "$STAMP"
fi

# Tell the CLI how it was actually invoked, so every hint it prints is a
# command that exists. Without this it suggests `agentisizer …`, which is
# only on PATH if the package was pip-installed — and the whole point of
# this wrapper is that you never have to do that.
export AGENTISIZER_CMD="${AGENTISIZER_CMD:-./run-agentisizer.sh}"
exec "$VENV_PY" -m agentisizer.cli "$@"

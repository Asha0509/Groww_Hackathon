#!/usr/bin/env bash
# setup.sh — bootstrap Since. Idempotent: safe to re-run.
set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[32m'; OFF=$'\033[0m'
say() { echo "${BOLD}${GREEN}==>${OFF} $*"; }
die() { echo "${BOLD}${GREEN}==>${OFF} $*" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

say "Checking prerequisites"
command -v python3 >/dev/null || die "python3 not found"
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.11+ required (found $(python3 -V))"
say "python $(python3 -V 2>&1 | cut -d' ' -f2)"

say "Creating virtualenv"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip

say "Installing dependencies"
.venv/bin/pip install --quiet -r requirements.txt

say "Running tests"
.venv/bin/pytest -q

cat <<EOF

${BOLD}${GREEN}Setup complete.${OFF}

  .venv/bin/uvicorn app.main:app --reload --port 8000   # then open http://localhost:8000
  .venv/bin/pytest -q                                    # tests
  .venv/bin/python scripts/compare_naive.py               # naive-vs-Since comparison
  .venv/bin/python scripts/benchmark_fanout.py             # fan-out scaling measurement

EOF

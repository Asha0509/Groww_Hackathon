#!/usr/bin/env bash
# setup.sh — bootstrap the Since project. Idempotent: safe to re-run.
set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
say()  { echo "${BOLD}${GREEN}==>${OFF} $*"; }
warn() { echo "${BOLD}${YELLOW}==>${OFF} $*"; }
die()  { echo "${BOLD}${RED}==>${OFF} $*" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------- preflight
say "Checking prerequisites"
command -v python3 >/dev/null || die "python3 not found"
command -v node    >/dev/null || die "node not found (need >= 18)"
command -v npm     >/dev/null || die "npm not found"

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.11+ required (found $(python3 -V))"
NODE_MAJOR=$(node -p "process.versions.node.split('.')[0]")
[ "$NODE_MAJOR" -ge 18 ] || die "Node 18+ required (found $(node -v))"
say "python $(python3 -V 2>&1 | cut -d' ' -f2) · node $(node -v)"

# ---------------------------------------------------------------- structure
say "Scaffolding directories"
mkdir -p \
  app/{feed,ingest,corpactions,session,signals,digest,api} \
  app/naive \
  tests \
  data \
  scripts \
  web

for pkg in app app/feed app/ingest app/corpactions app/session app/signals app/digest app/api app/naive; do
  [ -f "$pkg/__init__.py" ] || touch "$pkg/__init__.py"
done
[ -f tests/__init__.py ] || touch tests/__init__.py

# ---------------------------------------------------------------- python env
say "Creating virtualenv"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

if [ ! -f requirements.txt ]; then
  cat > requirements.txt <<'EOF'
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
pydantic==2.10.4
pydantic-settings==2.7.0
python-dateutil==2.9.0.post0
pytest==8.3.4
pytest-asyncio==0.25.0
httpx==0.28.1
EOF
fi

say "Installing python dependencies"
pip install --quiet -r requirements.txt

# ---------------------------------------------------------------- frontend
if [ ! -f web/package.json ]; then
  say "Scaffolding Vite + React + TypeScript frontend"
  npm create vite@latest web -- --template react-ts >/dev/null 2>&1
fi
say "Installing frontend dependencies"
(cd web && npm install --silent)

# Point the dev frontend at the API
if [ ! -f web/.env.development ]; then
  echo "VITE_API_BASE=http://localhost:8000" > web/.env.development
fi

# ---------------------------------------------------------------- config
if [ ! -f .env ]; then
  say "Writing .env"
  cat > .env <<'EOF'
DATABASE_URL=sqlite:///./data/since.db
SEED=20260907
DIGEST_BUDGET=5
DEGRADED_MULTIPLIER=6
LOG_LEVEL=info
EOF
fi

if [ ! -f .gitignore ]; then
  cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/*.db
data/*.db-wal
data/*.db-shm
web/node_modules/
web/dist/
.env
.DS_Store
EOF
fi

# ---------------------------------------------------------------- makefile
if [ ! -f Makefile ]; then
  say "Writing Makefile"
  cat > Makefile <<'EOF'
.PHONY: dev api web test demo-tests build seed fmt clean

dev:            ## run api + frontend together
	@echo "API  -> http://localhost:8000"
	@echo "WEB  -> http://localhost:5173"
	@( .venv/bin/uvicorn app.main:app --reload --port 8000 & \
	   cd web && npm run dev & wait )

api:
	.venv/bin/uvicorn app.main:app --reload --port 8000

web:
	cd web && npm run dev

test:
	.venv/bin/pytest -q

demo-tests:     ## the red/green naive-vs-Since comparison table
	.venv/bin/python scripts/compare_naive.py

seed:
	.venv/bin/python scripts/seed.py

build:
	cd web && npm run build

clean:
	rm -rf data/*.db data/*.db-wal data/*.db-shm .pytest_cache
EOF
fi

# ---------------------------------------------------------------- docker
if [ ! -f Dockerfile ]; then
  cat > Dockerfile <<'EOF'
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY --from=web /web/dist ./app/static
RUN mkdir -p data
ENV DATABASE_URL=sqlite:///./data/since.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
EOF
fi

# ---------------------------------------------------------------- smoke app
if [ ! -f app/main.py ]; then
  say "Writing placeholder app (deploy this NOW, before building features)"
  cat > app/main.py <<'EOF'
from fastapi import FastAPI

app = FastAPI(title="Since")


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "since"}


@app.get("/")
def root():
    return {"service": "since", "status": "scaffold"}
EOF
fi

if [ ! -f scripts/compare_naive.py ]; then
  cat > scripts/compare_naive.py <<'EOF'
"""Prints the naive-vs-Since red/green table. Populated during the build."""
print("compare_naive: not implemented yet — see BUILD.md hour 6")
EOF
fi

# ---------------------------------------------------------------- git
if [ ! -d .git ]; then
  say "Initialising git"
  git init --quiet
  git add -A
  git commit --quiet -m "scaffold: Since — a watchlist that never lies about its baseline" || true
fi

# ---------------------------------------------------------------- verify
say "Verifying"
.venv/bin/python -c "import fastapi, sqlalchemy, pytest; print('  python deps ok')"
[ -d web/node_modules ] && echo "  frontend deps ok"

cat <<EOF

${BOLD}${GREEN}Setup complete.${OFF}

  ${BOLD}Next, in this exact order:${OFF}
    1. make api            # confirm http://localhost:8000/healthz returns ok
    2. Deploy to Render/Railway NOW, before writing any feature code.
       A deploy that fails at hour 9 is the classic way to lose.
    3. Open BUILD.md and start at Hour 0.

  ${BOLD}Commands:${OFF}
    make dev          api + frontend
    make test         pytest
    make demo-tests   naive-vs-Since comparison table

EOF

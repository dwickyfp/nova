#!/usr/bin/env bash
#
# dev.sh — run every Nova process on the local machine with one command.
#
# Starts the five host-side processes (nothing that belongs in Docker):
#   backend    FastAPI web + embedded MySQL proxy (port 8000, 4406)
#   scheduler  nova-scheduler (cron/interval tick -> Redis Streams)
#   worker     nova-worker (runs task graphs and migration jobs)
#   agent-worker  Studio Auto coordinator and specialist runs
#   frontend   Vite dev server (port 5173)
#
# Docker infrastructure (StarRocks, Ranger, MinIO, Redis) is NOT managed here.
# The script verifies it is reachable through docker-compose.dev.yml and fails
# fast with the exact startup command when it is not.
#
#   ./dev.sh                          start everything, follow logs
#   ./dev.sh --no-frontend            skip the Vite dev server
#   ./dev.sh --no-infra-check         skip the StarRocks/Redis reachability probe
#   ./dev.sh --kill                   free the ports first, then start
#   ./dev.sh --help
#
# Ports are overridable when the defaults are taken:
#   BACKEND_PORT=8000 PROXY_PORT=4406 FRONTEND_PORT=5173 ./dev.sh
#   STARROCKS_FE_MYSQL_PORT=29030 RANGER_ADMIN_PORT=6080 ./dev.sh
#
# Ctrl+C stops every child process. Logs are also written to .dev-logs/.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
LOG_DIR="$ROOT/.dev-logs"

RUN_FRONTEND=1
NO_INFRA_CHECK=0
KILL_PORTS=0

for arg in "$@"; do
  case "$arg" in
    --no-frontend) RUN_FRONTEND=0 ;;
    --no-infra-check) NO_INFRA_CHECK=1 ;;
    --kill) KILL_PORTS=1 ;;
    -h|--help)
      sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# ── pretty output ────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_CYAN=""
fi

log()  { printf '%s\n' "${C_DIM}[dev]${C_RESET} $*"; }
ok()   { printf '%s\n' "${C_GREEN}[dev]${C_RESET} $*"; }
warn() { printf '%s\n' "${C_YELLOW}[dev]${C_RESET} $*"; }
die()  { printf '%s\n' "${C_RED}[dev]${C_RESET} $*" >&2; exit 1; }

# ── required tooling ─────────────────────────────────────────────────
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }
need uv
need docker

if [[ "$RUN_FRONTEND" -eq 1 ]]; then
  need pnpm
fi

# ── env files ────────────────────────────────────────────────────────
# Backend reads .env relative to its cwd, so it must be started from backend/.
[[ -f "$BACKEND_DIR/.env" ]] || die "backend/.env not found — copy backend/.env.example and fill SECRET_KEY + FERNET_KEY"

# Host-side services cannot use Docker DNS. The development Compose override
# maps StarRocks to loopback on 29030; the primary stack still keeps 9030
# private. Explicit shell values win when a developer uses another endpoint.
STARROCKS_HOST="${STARROCKS_HOST:-127.0.0.1}"
STARROCKS_FE_MYSQL_PORT="${STARROCKS_FE_MYSQL_PORT:-29030}"
RANGER_ENABLED="${RANGER_ENABLED:-true}"
RANGER_ADMIN_URL="${RANGER_ADMIN_URL:-http://127.0.0.1:6080}"
RANGER_ADMIN_PORT="${RANGER_ADMIN_PORT:-6080}"
RANGER_USERNAME="${RANGER_USERNAME:-admin}"
RANGER_TLS_VERIFY="${RANGER_TLS_VERIFY:-false}"

# ── infrastructure reachability (Docker-managed, not started here) ───
tcp_open() {
  local host="$1" port="$2"
  # bash /dev/tcp probe — no nc dependency.
  (exec 3<>"/dev/tcp/$host/$port") >/dev/null 2>&1
}

port_open() {
  tcp_open 127.0.0.1 "$1"
}

# Read one variable from a container without printing its value. This lets the
# local backend reuse Ranger's development credential without copying it into
# another file or exposing it in logs.
container_env_value() {
  local container="$1" wanted="$2" line
  while IFS= read -r line; do
    case "$line" in
      "$wanted="*) printf '%s' "${line#*=}"; return 0 ;;
    esac
  done < <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null)
  return 1
}

check_infra() {
  local redis_url="${REDIS_URL:-redis://localhost:6379/0}"
  local redis_port redis_host
  redis_port="$(echo "$redis_url" | sed -E 's#^[a-z]+://##; s#^[^@]*@##; s#^[^:]*:([0-9]+).*#\1#')"
  redis_host="$(echo "$redis_url" | sed -E 's#^[a-z]+://##; s#^[^@]*@##; s#^([^:/]+).*#\1#')"

  local missing=0
  if tcp_open "$STARROCKS_HOST" "$STARROCKS_FE_MYSQL_PORT"; then
    ok "StarRocks FE reachable at ${STARROCKS_HOST}:${STARROCKS_FE_MYSQL_PORT}"
  else
    warn "StarRocks FE NOT reachable at ${STARROCKS_HOST}:${STARROCKS_FE_MYSQL_PORT}"; missing=1
  fi
  if tcp_open "$redis_host" "$redis_port"; then
    ok "Redis reachable at ${redis_host}:${redis_port}"
  else
    warn "Redis NOT reachable at ${redis_host}:${redis_port}"; missing=1
  fi
  if [[ "$RANGER_ENABLED" == "true" ]]; then
    if port_open "$RANGER_ADMIN_PORT"; then
      ok "Ranger Admin reachable at 127.0.0.1:${RANGER_ADMIN_PORT}"
    else
      warn "Ranger Admin NOT reachable at 127.0.0.1:${RANGER_ADMIN_PORT}"; missing=1
    fi
  fi

  if [[ "$missing" -eq 1 ]]; then
    die "infrastructure is down. Start the local-development stack, then re-run ./dev.sh:
    cd $ROOT/docker && docker compose -f docker-compose-engine.yml -f docker-compose.dev.yml up -d
  (The development override binds StarRocks SQL to loopback only.)"
  fi
}

if [[ "$NO_INFRA_CHECK" -eq 0 ]]; then
  log "checking Docker-managed infrastructure..."
  check_infra
else
  warn "skipping infrastructure check (--no-infra-check)"
fi

# ── process-tree helpers ─────────────────────────────────────────────
# `uvicorn --reload`, `uv run` and pnpm each fork grandchildren, so killing
# the direct child orphans the rest. macOS has no `setsid`, so collect the
# full descendant tree with `pgrep -P` and signal leaves-first.
descendants() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    descendants "$child"
    printf '%s\n' "$child"
  done
}

# Signal a whole tree: deepest children first, then the root.
signal_tree() {
  local sig="$1" pid="$2" child
  for child in $(descendants "$pid"); do
    kill "-$sig" "$child" 2>/dev/null || true
  done
  kill "-$sig" "$pid" 2>/dev/null || true
}

# ── pre-flight: refuse to start on a busy port ───────────────────────
# The embedded proxy (4406) shares the backend process; if either is taken,
# the stack comes up half-down, so fail before spawning anything.
BACKEND_PORT="${BACKEND_PORT:-8000}"
PROXY_PORT="${PROXY_PORT:-4406}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# PIDs listening on a TCP port (macOS lsof). Empty when the port is free.
port_pids() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u
}

# Terminate whatever holds a port: TERM the tree, then KILL any survivor.
# Only called for --kill; never touches a port this script does not own.
free_port() {
  local port="$1" label="$2" pid
  local pids
  pids="$(port_pids "$port")"
  [[ -z "$pids" ]] && return 0

  warn "port ${port} (${label}) held by pid(s): $(echo "$pids" | tr '\n' ' ')— terminating"
  for pid in $pids; do
    signal_tree TERM "$pid"
  done

  local waited=0
  while [[ "$waited" -lt 30 ]]; do
    [[ -z "$(port_pids "$port")" ]] && break
    sleep 0.1
    waited=$((waited + 1))
  done

  for pid in $(port_pids "$port"); do
    warn "port ${port}: pid ${pid} ignored TERM — killing"
    signal_tree KILL "$pid"
  done

  if [[ -n "$(port_pids "$port")" ]]; then
    die "could not free port ${port} (${label}). Check permissions for: $(port_pids "$port" | tr '\n' ' ')"
  fi
  ok "port ${port} freed (${label})"
}

# Refuse on a busy port unless --kill was given (then reap it first).
for entry in "$BACKEND_PORT:backend/web" "$PROXY_PORT:backend/MySQL-proxy"; do
  port="${entry%%:*}"
  label="${entry#*:}"
  if port_open "$port"; then
    if [[ "$KILL_PORTS" -eq 1 ]]; then
      free_port "$port" "$label"
    else
      die "port ${port} is already in use (${label}). Run ./dev.sh --kill to terminate it, or stop it yourself."
    fi
  fi
done
if [[ "$RUN_FRONTEND" -eq 1 ]] && port_open "$FRONTEND_PORT"; then
  if [[ "$KILL_PORTS" -eq 1 ]]; then
    free_port "$FRONTEND_PORT" "frontend"
  else
    die "port ${FRONTEND_PORT} is already in use (frontend). Run ./dev.sh --kill, or use --no-frontend."
  fi
fi

# ── child process management ─────────────────────────────────────────
mkdir -p "$LOG_DIR"
CHILD_PIDS=()
SHUTTING_DOWN=0

cleanup() {
  [[ "$SHUTTING_DOWN" -eq 1 ]] && return
  SHUTTING_DOWN=1
  printf '\n'
  log "stopping ${#CHILD_PIDS[@]} process(es)..."
  local pid
  for pid in "${CHILD_PIDS[@]}"; do
    signal_tree TERM "$pid"
  done

  local waited=0
  while [[ "$waited" -lt 50 ]]; do
    local alive=0
    for pid in "${CHILD_PIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null && alive=1
    done
    [[ "$alive" -eq 0 ]] && break
    sleep 0.1
    waited=$((waited + 1))
  done

  for pid in "${CHILD_PIDS[@]}"; do
    signal_tree KILL "$pid"
  done

  ok "all stopped. logs in $LOG_DIR"
  exit 0
}
trap cleanup INT TERM

# run <name> <color> <workdir> <command...>
run() {
  local name="$1" color="$2" workdir="$3"; shift 3
  local logfile="$LOG_DIR/$name.log"
  : > "$logfile"
  (
    cd "$workdir"
    exec "$@"
  ) > >(
    while IFS= read -r line; do
      printf '%s[%s]%s %s\n' "$color" "$name" "$C_RESET" "$line"
    done | tee -a "$logfile"
  ) 2>&1 &

  CHILD_PIDS+=("$!")
  ok "started $name (pid $!)"
}

# ── start everything ─────────────────────────────────────────────────
log "starting Nova dev processes..."

RANGER_PASSWORD_VALUE="${RANGER_PASSWORD:-}"
if [[ "$RANGER_ENABLED" == "true" && -z "$RANGER_PASSWORD_VALUE" ]]; then
  RANGER_PASSWORD_VALUE="$(container_env_value nova-ranger-admin RANGER_DB_PASSWORD || true)"
fi
if [[ "$RANGER_ENABLED" == "true" && -z "$RANGER_PASSWORD_VALUE" ]]; then
  die "Ranger is enabled but its development credential could not be read. Set RANGER_PASSWORD in the shell or start the Docker infrastructure first."
fi

RANGER_SERVICE_NAME_VALUE="${RANGER_SERVICE_NAME:-}"
if [[ "$RANGER_ENABLED" == "true" && -z "$RANGER_SERVICE_NAME_VALUE" ]]; then
  RANGER_SERVICE_NAME_VALUE="$(container_env_value nova-ranger-bootstrap RANGER_SERVICE_NAME || true)"
  RANGER_SERVICE_NAME_VALUE="${RANGER_SERVICE_NAME_VALUE:-nova_starrocks}"
fi

BACKEND_ENV=(
  env
  STARROCKS_HOST="$STARROCKS_HOST"
  STARROCKS_FE_MYSQL_PORT="$STARROCKS_FE_MYSQL_PORT"
  RANGER_ENABLED="$RANGER_ENABLED"
  RANGER_ADMIN_URL="$RANGER_ADMIN_URL"
  RANGER_SERVICE_NAME="$RANGER_SERVICE_NAME_VALUE"
  RANGER_USERNAME="$RANGER_USERNAME"
  RANGER_PASSWORD="$RANGER_PASSWORD_VALUE"
  RANGER_TLS_VERIFY="$RANGER_TLS_VERIFY"
)

if ! (
  cd "$BACKEND_DIR"
  "${BACKEND_ENV[@]}" uv run python - <<'PY'
from app.core.config import settings

user = settings.WORKER_IMPERSONATION_USER
password = settings.WORKER_IMPERSONATION_PASSWORD
role = settings.WORKER_IMPERSONATION_ROLE
valid = bool(user and password and user.lower() not in {"root", "nova_admin"})
raise SystemExit(0 if valid and (not settings.RANGER_ENABLED or role) else 1)
PY
); then
  die "task worker configuration is incomplete. Set WORKER_IMPERSONATION_USER and WORKER_IMPERSONATION_PASSWORD in backend/.env for a dedicated StarRocks account. With Ranger enabled, also set WORKER_IMPERSONATION_ROLE."
fi

# Backend: FastAPI with the embedded MySQL proxy (PROXY_PORT, default 4406).
run backend "$C_BLUE" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" PROXY_PORT="$PROXY_PORT" \
  uv run uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"

# Standalone workers. They share NOVA_SYSTEM + Redis; migration jobs are
# claimed by app.worker, never executed in the FastAPI process.
run scheduler "$C_CYAN" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" \
  uv run python -m app.scheduler

run worker "$C_CYAN" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" \
  uv run python -m app.worker

run agent-worker "$C_CYAN" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" \
  uv run python -m app.agent_worker

if [[ "$RUN_FRONTEND" -eq 1 ]]; then
  # `pnpm dev` in package.json already passes `--port 5173`; use this to
  # control the port without stacking a second conflicting flag.
  run frontend "$C_YELLOW" "$FRONTEND_DIR" \
    pnpm exec vite --host 0.0.0.0 --port "$FRONTEND_PORT"
fi

# ── wait ─────────────────────────────────────────────────────────────
printf '\n'
ok "Nova is coming up:"
printf '    %-10s %s\n' "web"     "http://localhost:${BACKEND_PORT}"
printf '    %-10s %s\n' "api"     "http://localhost:${BACKEND_PORT}/docs"
printf '    %-10s %s\n' "mysql"   "localhost:${PROXY_PORT} (through the backend)"
[[ "$RUN_FRONTEND" -eq 1 ]] && printf '    %-10s %s\n' "ui" "http://localhost:${FRONTEND_PORT}"
printf '\n'
log "Ctrl+C to stop everything."

# Exit if any service dies, so a crashed backend does not leave a half-up stack.
# (Polled rather than `wait -n`: macOS ships bash 3.2, which lacks it.)
while [[ "$SHUTTING_DOWN" -eq 0 ]]; do
  for pid in "${CHILD_PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      warn "a process exited; shutting the rest down."
      cleanup
    fi
  done
  # `sleep &` + `wait` rather than a bare `sleep`, so Ctrl+C interrupts it
  # immediately and the INT trap runs (bash 3.2 defers traps otherwise).
  sleep 1 &
  wait $!
done

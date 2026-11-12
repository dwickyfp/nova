#!/usr/bin/env bash
#
# dev.sh — run every Nova process on the local machine with one command.
#
# Runs web (8000), query (8001), MySQL proxy (4406), scheduler and the
# supervised workload processes separately. Backend and workers reload on
# Python edits; Vite hot-reloads the UI. Docker infrastructure stays separate.
#   worker     task, ingestion, ML, notebook, automation, autopilot, maintenance, outbox
#   frontend   Vite dev server (port 5173)
#
# Docker infrastructure (StarRocks, Ranger, MinIO, Redis) is NOT managed here.
# The script verifies it is reachable through docker-compose.dev.yml and fails
# fast with the exact startup command when it is not.
#
#   ./dev.sh                          start everything, follow logs
#   ./dev.sh --no-frontend            skip the Vite dev server
#   ./dev.sh --no-infra-check         skip the StarRocks/Redis reachability probe
#   ./dev.sh --split                  accepted for compatibility; split is the default
#   ./dev.sh --kill                   stop this checkout's listeners, then start
#   ./dev.sh --help
#
# Ports are overridable when the defaults are taken:
#   BACKEND_PORT=8000 QUERY_PORT=8001 PROXY_PORT=4406 FRONTEND_PORT=5173 ./dev.sh
#   STARROCKS_FE_MYSQL_PORT=29030 RANGER_ADMIN_PORT=6080 ./dev.sh
#
# Ctrl+C stops every child process. Logs are also written to .dev-logs/.

set -euo pipefail

umask 077

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
    --split) ;;
    -h|--help)
      sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
need lsof
need pgrep
need ps

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
  local redis_host redis_port endpoint
  endpoint="$(cd "$BACKEND_DIR" && uv run python - <<'PYTHON'
from urllib.parse import urlsplit
from app.core.config import settings
endpoint = urlsplit(settings.REDIS_URL)
print(f"{endpoint.hostname or 'localhost'} {endpoint.port or 6379}")
PYTHON
)" || die "could not read Redis endpoint from backend settings"
  read -r redis_host redis_port <<< "$endpoint"

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
  if port_open "$RANGER_ADMIN_PORT"; then
    ok "Ranger Admin reachable at 127.0.0.1:${RANGER_ADMIN_PORT}"
  else
    warn "Ranger Admin NOT reachable at 127.0.0.1:${RANGER_ADMIN_PORT}"; missing=1
  fi

  if [[ "$missing" -eq 1 ]]; then
    die "infrastructure is down. Start the local-development stack, then re-run ./dev.sh:
    cd $ROOT/docker && docker compose -f docker-compose-engine.yml -f docker-compose.dev.yml up -d
  (The development override binds StarRocks SQL to loopback only.)"
  fi
}

if [[ "$NO_INFRA_CHECK" -eq 0 ]]; then
  log "checking existing Docker projects and infrastructure..."
  docker compose ls
  docker ps --format '{{.Names}}\t{{.Status}}'
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
# Refuse collisions across API, proxy, worker telemetry and frontend ports
# before spawning any application process.
BACKEND_PORT="${BACKEND_PORT:-8000}"
PROXY_PORT="${PROXY_PORT:-4406}"
QUERY_PORT="${QUERY_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# PIDs listening on a TCP port (macOS lsof). Empty when the port is free.
port_pids() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u
}

process_identity() {
  ps -p "$1" -o lstart= 2>/dev/null
}

same_process() {
  local identity
  identity="$(process_identity "$1")" || return 1
  [[ -n "$identity" && "$identity" == "$2" ]]
}

# --kill is restricted to the original listeners from this checkout.
free_port() {
  local port="$1" label="$2" pid
  local pids
  local owned=()
  pids="$(port_pids "$port" || true)"
  [[ -z "$pids" ]] && return 0

  for pid in $pids; do
    local process_cwd
    process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
    case "$process_cwd" in
      "$BACKEND_DIR"|"$FRONTEND_DIR") ;;
      *) die "port ${port} is held outside this checkout; stop its owner explicitly" ;;
    esac
    owned+=("$pid|$(process_identity "$pid")")
  done
  warn "port ${port} (${label}) held by pid(s): $(echo "$pids" | tr '\n' ' ')— terminating"
  for pid in $pids; do
    signal_tree TERM "$pid"
  done

  local waited=0
  while [[ "$waited" -lt 500 ]]; do
    [[ -z "$(port_pids "$port")" ]] && break
    sleep 0.1
    waited=$((waited + 1))
  done

  local tracked
  for tracked in "${owned[@]}"; do
    pid="${tracked%%|*}"
    if same_process "$pid" "${tracked#*|}"; then
      warn "port ${port}: pid ${pid} ignored TERM — killing"
      signal_tree KILL "$pid"
    fi
  done

  if [[ -n "$(port_pids "$port")" ]]; then
    die "could not free port ${port} (${label}). Check permissions for: $(port_pids "$port" | tr '\n' ' ')"
  fi
  ok "port ${port} freed (${label})"
}

# Refuse on a busy port unless --kill was given (then reap it first).
PORT_ENTRIES=("$BACKEND_PORT:backend/web" "$PROXY_PORT:backend/MySQL-proxy")
for entry in "SCHEDULER:9101" "WORKER:9102" "PROXY:9104" "INGESTION_WORKER:9105" "ML_WORKER:9106" "NOTEBOOK_WORKER:9107" "AUTOMATION_WORKER:9108" "AUTOPILOT_WORKER:9109" "MAINTENANCE_WORKER:9110" "OUTBOX_WORKER:9111"; do
  metrics_name="NOVA_METRICS_${entry%%:*}_PORT"
  PORT_ENTRIES+=("${!metrics_name:-${entry#*:}}:metrics/${entry%%:*}")
done
[[ "$RUN_FRONTEND" -eq 1 ]] && PORT_ENTRIES+=("$FRONTEND_PORT:frontend")
PORT_ENTRIES+=("$QUERY_PORT:backend/query")
SEEN_PORTS=" "
for entry in "${PORT_ENTRIES[@]}"; do
  port="${entry%%:*}"
  label="${entry#*:}"
  [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && [[ "$port" -le 65535 ]] || die "invalid port for ${label}"
  [[ "$SEEN_PORTS" != *" $port "* ]] || die "port ${port} is assigned to more than one process"
  SEEN_PORTS+="$port "
  if port_open "$port"; then
    if [[ "$KILL_PORTS" -eq 1 ]]; then
      free_port "$port" "$label"
    else
      die "port ${port} is already in use (${label}). Run ./dev.sh --kill to terminate it, or stop it yourself."
    fi
  fi
done
# ── child process management ─────────────────────────────────────────
mkdir -p "$LOG_DIR"
CHILD_PIDS=()
SHUTTING_DOWN=0

cleanup() {
  local exit_status="${1:-0}"
  trap - EXIT INT TERM
  [[ "$SHUTTING_DOWN" -eq 1 ]] && return
  SHUTTING_DOWN=1
  printf '\n'
  log "stopping ${#CHILD_PIDS[@]} process(es)..."
  local pid child tracked
  local tracked_pids=()
  for pid in "${CHILD_PIDS[@]}"; do
    for child in $(descendants "$pid") "$pid"; do
      tracked_pids+=("$child|$(process_identity "$child" || true)")
    done
  done
  for pid in "${CHILD_PIDS[@]}"; do
    signal_tree TERM "$pid"
  done

  local waited=0
  while [[ "$waited" -lt 500 ]]; do
    local alive=0
    for tracked in "${tracked_pids[@]}"; do
      same_process "${tracked%%|*}" "${tracked#*|}" && alive=1
    done
    [[ "$alive" -eq 0 ]] && break
    sleep 0.1
    waited=$((waited + 1))
  done

  for tracked in "${tracked_pids[@]}"; do
    pid="${tracked%%|*}"
    if same_process "$pid" "${tracked#*|}"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${CHILD_PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done

  ok "all stopped. logs in $LOG_DIR"
  exit "$exit_status"
}
trap 'cleanup $?' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

# run <name> <color> <workdir> <command...>
run() {
  local name="$1" color="$2" workdir="$3"; shift 3
  local logfile="$LOG_DIR/$name.log"
  : > "$logfile"
  chmod 600 "$logfile"
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
if [[ -z "$RANGER_PASSWORD_VALUE" ]]; then
  RANGER_PASSWORD_VALUE="$(container_env_value nova-ranger-admin RANGER_ADMIN_PASSWORD || true)"
fi
if [[ -z "$RANGER_PASSWORD_VALUE" ]]; then
  die "Ranger's development credential could not be read. Set RANGER_PASSWORD in the shell or start the Docker infrastructure first."
fi

RANGER_SERVICE_NAME_VALUE="${RANGER_SERVICE_NAME:-}"
if [[ -z "$RANGER_SERVICE_NAME_VALUE" ]]; then
  RANGER_SERVICE_NAME_VALUE="$(container_env_value nova-ranger-bootstrap RANGER_SERVICE_NAME || true)"
  RANGER_SERVICE_NAME_VALUE="${RANGER_SERVICE_NAME_VALUE:-nova_starrocks}"
fi

# BI (docs/arch-17-superset.md): Superset runs in Docker and calls this backend
# through docker-compose.dev.yml; the backend reaches Superset on loopback and
# shares its service token, read from the running container like Ranger's.
SUPERSET_PORT="${SUPERSET_PORT:-8088}"
SUPERSET_SERVICE_TOKEN_VALUE="${SUPERSET_SERVICE_TOKEN:-}"
if [[ -z "$SUPERSET_SERVICE_TOKEN_VALUE" ]]; then
  SUPERSET_SERVICE_TOKEN_VALUE="$(container_env_value nova-superset NOVA_SUPERSET_SERVICE_TOKEN || true)"
fi
if [[ -z "$SUPERSET_SERVICE_TOKEN_VALUE" ]]; then
  warn "Superset is not running: BI and Studio Dataviz stay unavailable until it starts."
fi

BACKEND_ENV=(
  env
  SUPERSET_INTERNAL_URL="${SUPERSET_INTERNAL_URL:-http://127.0.0.1:${SUPERSET_PORT}}"
  SUPERSET_SERVICE_TOKEN="$SUPERSET_SERVICE_TOKEN_VALUE"
  STARROCKS_HOST="$STARROCKS_HOST"
  STARROCKS_FE_MYSQL_PORT="$STARROCKS_FE_MYSQL_PORT"
  RANGER_ADMIN_URL="$RANGER_ADMIN_URL"
  RANGER_SERVICE_NAME="$RANGER_SERVICE_NAME_VALUE"
  RANGER_USERNAME="$RANGER_USERNAME"
  RANGER_PASSWORD="$RANGER_PASSWORD_VALUE"
  RANGER_TLS_VERIFY="$RANGER_TLS_VERIFY"
  NOVA_METRICS_HOST=127.0.0.1
)

if ! (
  cd "$BACKEND_DIR"
  "${BACKEND_ENV[@]}" uv run python - <<'PY'
from app.core.config import settings
from app.core.metadata_process import PROCESS_WORKLOADS, load_runtime_settings, pool_config

metadata_settings = load_runtime_settings()
for workload in PROCESS_WORKLOADS:
    pool_config(metadata_settings, workload)

user = settings.WORKER_IMPERSONATION_USER
password = settings.WORKER_IMPERSONATION_PASSWORD
role = settings.WORKER_IMPERSONATION_ROLE
valid = bool(user and password and user.lower() not in {"root", "nova_admin"})
raise SystemExit(0 if valid and role else 1)
PY
); then
  die "runtime configuration is incomplete. Configure the published PG schema/epoch and workload credentials, plus a dedicated StarRocks worker impersonation account/role. See HOW_TO_RUN.md; dev.sh never migrates or publishes an epoch."
fi

# Split topology: the web process serves the application API, the query
# process runs SQL and ML, and the MySQL proxy is its own process. Vite routes
# by path the way the gateway does in a deployment.
run backend "$C_BLUE" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" NOVA_PROCESS_ROLE=web PROXY_ENABLED=false ML_TRAINING_EXECUTOR=worker \
  uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port "$BACKEND_PORT"

run query "$C_BLUE" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" NOVA_PROCESS_ROLE=query PROXY_ENABLED=false ML_TRAINING_EXECUTOR=worker \
  uv run uvicorn app.main:app --reload --reload-dir app --host 127.0.0.1 --port "$QUERY_PORT"

run proxy "$C_BLUE" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" PROXY_PORT="$PROXY_PORT" \
  uv run python -m app.proxy

# The worker launcher supervises one child process per workload. Watchfiles
# restarts the launcher and its children together when Python code changes.
WATCH=(uv run watchfiles --filter python --sigint-timeout 45 --sigkill-timeout 5)

run scheduler "$C_CYAN" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" \
  "${WATCH[@]}" "python -m app.scheduler" app

run worker "$C_CYAN" "$BACKEND_DIR" \
  "${BACKEND_ENV[@]}" \
  "${WATCH[@]}" "python -m app.worker" app

if [[ "$RUN_FRONTEND" -eq 1 ]]; then
  # `pnpm dev` in package.json already passes `--port 5173`; use this to
  # control the port without stacking a second conflicting flag.
  FRONTEND_ENV=(env NOVA_WEB_API_URL="http://localhost:${BACKEND_PORT}")
  FRONTEND_ENV+=(NOVA_QUERY_API_URL="http://localhost:${QUERY_PORT}")
  run frontend "$C_YELLOW" "$FRONTEND_DIR" \
    "${FRONTEND_ENV[@]}" pnpm exec vite --host 127.0.0.1 --strictPort --port "$FRONTEND_PORT"
fi

# ── wait ─────────────────────────────────────────────────────────────
printf '\n'
ok "Nova is coming up:"
printf '    %-10s %s\n' "web"     "http://localhost:${BACKEND_PORT}"
printf '    %-10s %s\n' "api"     "http://localhost:${BACKEND_PORT}/docs"
printf '    %-10s %s\n' "query" "http://localhost:${QUERY_PORT}"
printf '    %-10s %s\n' "mysql" "localhost:${PROXY_PORT} (standalone proxy)"
[[ "$RUN_FRONTEND" -eq 1 ]] && printf '    %-10s %s\n' "ui" "http://localhost:${FRONTEND_PORT}"
printf '\n'
log "waiting for every runtime to report ready..."
READY_ARGS=(--web "$BACKEND_PORT" --query "$QUERY_PORT")
[[ "$RUN_FRONTEND" -eq 1 ]] && READY_ARGS+=(--frontend "$FRONTEND_PORT")
STARTUP_SECONDS="${DEV_STARTUP_SECONDS:-240}"
[[ "$STARTUP_SECONDS" =~ ^[1-9][0-9]{0,3}$ ]] || die "DEV_STARTUP_SECONDS must be a positive integer"
started_at=$SECONDS
while ! (cd "$BACKEND_DIR" && "${BACKEND_ENV[@]}" uv run python scripts/dev_readiness.py "${READY_ARGS[@]}"); do
  for pid in "${CHILD_PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || die "a runtime exited during startup; see .dev-logs"
  done
  [[ $((SECONDS - started_at)) -lt "$STARTUP_SECONDS" ]] || die "runtime readiness deadline exceeded; see .dev-logs"
  sleep 1 &
  wait $!
done
ok "all Nova runtimes are ready. Ctrl+C to stop everything."

# Exit if any service dies, so a crashed backend does not leave a half-up stack.
# (Polled rather than `wait -n`: macOS ships bash 3.2, which lacks it.)
while [[ "$SHUTTING_DOWN" -eq 0 ]]; do
  for pid in "${CHILD_PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      warn "a process exited; shutting the rest down."
      cleanup 1
    fi
  done
  # `sleep &` + `wait` rather than a bare `sleep`, so Ctrl+C interrupts it
  # immediately and the INT trap runs (bash 3.2 defers traps otherwise).
  sleep 1 &
  wait $!
done

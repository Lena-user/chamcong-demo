#!/usr/bin/env bash
# Run one load-test scenario, sample container CPU/RAM, then build the report.
# Also samples Postgres (wait events, TPS, top queries) and, for write scenarios,
# reconciles the DB with what the clients were told (reconcile.py).
# Usage: loadtest/run.sh <steady|burst|ramp|login_storm|custom|write_storm|write_ramp> [host]
# Settings come from loadtest/config.env (or $LOADTEST_CONFIG); variables already
# set in the environment win, e.g. STEADY_USERS=50 ./loadtest/run.sh steady
# Default host is TARGET_HOST (inside the compose network). Pass a
# https://xxx.trycloudflare.com URL to go through the tunnel instead.
set -euo pipefail

SCENARIO="${1:-}"
case "$SCENARIO" in
  steady|burst|ramp|login_storm|custom|write_storm|write_ramp) ;;
  *) echo "Usage: $0 <steady|burst|ramp|login_storm|custom|write_storm|write_ramp> [host]" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Load KEY=VALUE lines without executing them; the environment wins over the file.
CONFIG_FILE="${LOADTEST_CONFIG:-$ROOT/loadtest/config.env}"
[ -f "$CONFIG_FILE" ] || { echo "Config file not found: $CONFIG_FILE" >&2; exit 2; }
CONFIG_KEYS=()
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  if [ -z "${!key+x}" ]; then
    export "$key=${line#*=}"
  fi
  CONFIG_KEYS+=("$key")
done <"$CONFIG_FILE"

HOST="${2:-${TARGET_HOST:-http://api:8000}}"
LOCUST_SERVICE="${LOCUST_SERVICE:-locust}"
COMPOSE_PROFILE="${COMPOSE_PROFILE:-loadtest}"

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_NAME="${STAMP}-${SCENARIO}"
OUT="$ROOT/loadtest/results/$RUN_NAME"
REL="results/$RUN_NAME" # same dir as seen from inside the locust container
mkdir -p "$OUT"

COMPOSE=(docker compose --profile "$COMPOSE_PROFILE")

# Running containers of this compose project, excluding the locust runner itself.
mapfile -t IDS < <("${COMPOSE[@]}" ps -q 2>/dev/null || true)
if [ "${#IDS[@]}" -eq 0 ]; then
  echo "No running containers for this compose project. Start it first: docker compose up -d" >&2
  exit 1
fi

# Compose project name (label of any running container) and the psql command inside the db container.
PROJECT="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "${IDS[0]}")"
# shellcheck disable=SC2016 # the variables are meant to expand inside the db container
PSQL=("${COMPOSE[@]}" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh)

# Print one "-- @name: <section>" block of db_probe.sql.
probe_sql() { awk -v name="$1" '/^-- @name: /{on = ($3 == name); next} on' "$ROOT/loadtest/db_probe.sql"; }

# Same limit JSON as in meta.json: 0 (not set in compose) becomes null = unlimited.
limit_json() { # <nano cpus> <memory bytes>
  local cpu_json=null mem_json=null
  [ "$1" != 0 ] && cpu_json="$(awk -v n="$1" 'BEGIN{printf "%.3f", n/1e9}')"
  [ "$2" != 0 ] && mem_json="$2"
  printf '{"cpu_limit":%s,"mem_limit_bytes":%s}' "$cpu_json" "$mem_json"
}

json_str() { # JSON-escape a value (backslash and double quote; no control chars expected)
  local s="${1//\\/\\\\}"
  printf '%s' "${s//\"/\\\"}"
}

# meta.json: scenario, host, time, limits (from docker inspect), git commit, compose
# project, host CPU count and every config value used.
# A limit of 0 (not set in compose) is written as null = unlimited.
write_meta() {
  local limits="" sep="" line svc cpus mem project=""
  while IFS= read -r line; do
    read -r svc cpus mem project <<<"$line"
    [ -z "$svc" ] && continue
    limits+="${sep}\"$(json_str "$svc")\":$(limit_json "$cpus" "$mem")"
    sep=","
  done < <(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{index .Config.Labels "com.docker.compose.project"}}' "${IDS[@]}")
  local commit cfg="" csep="" k rate_limit
  # RATE_LIMIT_ENABLED as the api container sees it (null = not set there).
  rate_limit="$("${COMPOSE[@]}" exec -T api printenv RATE_LIMIT_ENABLED 2>/dev/null || true)"
  rate_limit="${rate_limit%$'\r'}"
  if [ -n "$rate_limit" ]; then rate_limit="\"$(json_str "$rate_limit")\""; else rate_limit=null; fi
  commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  for k in "${CONFIG_KEYS[@]}"; do
    cfg+="${csep}\"${k}\":\"$(json_str "${!k}")\""
    csep=","
  done
  printf '{"scenario":"%s","host":"%s","started_at":"%s","git_commit":"%s","project":"%s","host_cpus":%s,"rate_limit_enabled":%s,"limits":{%s},"config":{%s}}\n' \
    "$SCENARIO" "$(json_str "$HOST")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$(json_str "$project")" \
    "$(nproc)" "$rate_limit" "$limits" "$cfg" >"$OUT/meta.json"
}
write_meta

# Sample docker stats every ~2s; each line is {"ts":<epoch>,<docker stats json>...}.
# The Locust runner container (one-off, started later) is looked up on every round.
sample_stats() {
  local t0 ts runners cpus mem
  while true; do
    t0="$(date +%s)"
    ts="$(date +%s.%N)"
    mapfile -t runners < <(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
      --filter "label=com.docker.compose.service=$LOCUST_SERVICE" 2>/dev/null || true)
    if [ "${#runners[@]}" -gt 0 ] && [ ! -s "$OUT/runner_limits.json" ]; then
      read -r cpus mem < <(docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}' "${runners[0]}" 2>/dev/null || echo "0 0")
      limit_json "${cpus:-0}" "${mem:-0}" >"$OUT/runner_limits.json"
    fi
    docker stats --no-stream --format '{{json .}}' "${IDS[@]}" "${runners[@]}" 2>/dev/null \
      | sed "s/^{/{\"ts\":${ts},/" >>"$OUT/docker_stats.jsonl" || true
    local spent=$(( $(date +%s) - t0 ))
    [ "$spent" -lt 2 ] && sleep $(( 2 - spent ))
  done
}

# Sample Postgres every ~2s into pg_samples.jsonl (see db_probe.sql, section "sample").
sample_pg() {
  local q t0 spent
  q="$(probe_sql sample)"
  while true; do
    t0="$(date +%s)"
    printf '%s\n' "$q" | "${PSQL[@]}" -qAt -f - 2>/dev/null | grep '^{' >>"$OUT/pg_samples.jsonl" || true
    spent=$(( $(date +%s) - t0 ))
    [ "$spent" -lt 2 ] && sleep $(( 2 - spent ))
  done
}

SAMPLER_PIDS=()
stop_samplers() {
  local pid
  for pid in "${SAMPLER_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  SAMPLER_PIDS=()
}
trap stop_samplers EXIT INT TERM

# Start clean: per-query stats (extension created if missing; errors are ignored) and
# the DB clock, so the reconcile query only looks at rows written by this run.
{ echo 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'; probe_sql reset; } \
  | "${PSQL[@]}" -q -f - >/dev/null 2>&1 || echo "pg_stat_statements not available, skipping the top-queries table" >&2
DB_SINCE="$(echo 'SELECT now();' | "${PSQL[@]}" -qAt -f - | head -n 1)"

: >"$OUT/docker_stats.jsonl"
: >"$OUT/pg_samples.jsonl"
sample_stats &
SAMPLER_PIDS+=($!)
sample_pg &
SAMPLER_PIDS+=($!)

# Every config value (plus scenario) goes into the container with -e.
ENV_ARGS=(-e "SCENARIO=$SCENARIO" -e "RESULT_DIR=$REL" -e "RECONCILE_HOST=$HOST")
for k in "${CONFIG_KEYS[@]}"; do
  ENV_ARGS+=(-e "$k=${!k}")
done

echo "Scenario: $SCENARIO  Host: $HOST  Output: $OUT"
# Load shapes (see shapes.py) define users and duration, so no -u/-r/-t here.
# --exit-code-on-error 0: failed requests are reported, not fatal to this script.
set +e
# One Locust process tops out around one CPU; fork more so the load generator is not the bottleneck.
PROC_ARGS=()
if [ "${LOCUST_PROCESSES:-1}" -gt 1 ]; then PROC_ARGS=(--processes "$LOCUST_PROCESSES"); fi
"${COMPOSE[@]}" run --rm -T "${ENV_ARGS[@]}" "$LOCUST_SERVICE" \
  -f locustfile.py --headless --host "$HOST" "${PROC_ARGS[@]}" \
  --csv "$REL/locust" --csv-full-history --html "$REL/locust.html" \
  --exit-code-on-error 0
LOCUST_RC=$?
set -e

stop_samplers
trap - EXIT INT TERM

echo "Locust exit code: $LOCUST_RC. Collecting DB data..."
probe_sql top_queries | "${PSQL[@]}" --csv -f - >"$OUT/pg_top_queries.csv" 2>/dev/null \
  || echo "No pg_top_queries.csv (pg_stat_statements not available)" >&2

export_keys() { # <csv file>: keys in the DB since the start of the run (app_reconcile.sql)
  "${PSQL[@]}" --csv -v ON_ERROR_STOP=1 -v since="$DB_SINCE" -f - <"$ROOT/loadtest/app_reconcile.sql" >"$OUT/$1"
}
PY=("${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python "${ENV_ARGS[@]}" "$LOCUST_SERVICE")

RECONCILE=0
if [ -s "$OUT/writes.jsonl" ]; then
  RECONCILE=1
  export_keys db_keys.csv
  if [ "${RECONCILE_RETRY:-1}" = 1 ]; then
    "${PY[@]}" reconcile.py --retry "$REL"
    if [ -s "$OUT/writes_retry.jsonl" ]; then export_keys db_keys_after_retry.csv; fi
  fi
fi

echo "Building report..."
"${PY[@]}" report.py "$REL"
RECONCILE_RC=0
if [ "$RECONCILE" = 1 ]; then
  "${PY[@]}" reconcile.py "$REL" || RECONCILE_RC=$?
fi
# Optional app-specific check of what was written (POST_CHECK_CMD in config.env; {since} = start of
# the run as a DB timestamp). Its output is appended to report.md; a non-zero exit fails the run.
POST_CHECK_RC=0
if [ -n "${POST_CHECK_CMD:-}" ]; then
  echo "Post-check: $POST_CHECK_CMD"
  (cd "$ROOT" && bash -c "${POST_CHECK_CMD//\{since\}/$DB_SINCE}") >"$OUT/post_check.md" 2>&1 || POST_CHECK_RC=$?
  { echo; cat "$OUT/post_check.md"; } >>"$OUT/report.md"
fi
echo "Report: $OUT/report.md"
if [ "$RECONCILE_RC" -ne 0 ]; then
  echo "RECONCILE FAILED: a confirmed write is missing, duplicated or owned by someone else (see $OUT/reconcile.md)" >&2
  [ "$LOCUST_RC" -eq 0 ] && exit 3
fi
if [ "$POST_CHECK_RC" -ne 0 ]; then
  echo "POST-CHECK FAILED: see $OUT/post_check.md" >&2
  [ "$LOCUST_RC" -eq 0 ] && exit 4
fi
exit "$LOCUST_RC"

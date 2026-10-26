#!/usr/bin/env bash
# Run one load-test scenario, sample container CPU/RAM, then build the report.
# Usage: loadtest/run.sh <steady|burst|ramp|login_storm|custom> [host]
# Settings come from loadtest/config.env (or $LOADTEST_CONFIG); variables already
# set in the environment win, e.g. STEADY_USERS=50 ./loadtest/run.sh steady
# Default host is TARGET_HOST (inside the compose network). Pass a
# https://xxx.trycloudflare.com URL to go through the tunnel instead.
set -euo pipefail

SCENARIO="${1:-}"
case "$SCENARIO" in
  steady|burst|ramp|login_storm|custom) ;;
  *) echo "Usage: $0 <steady|burst|ramp|login_storm|custom> [host]" >&2; exit 2 ;;
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

json_str() { # JSON-escape a value (backslash and double quote; no control chars expected)
  local s="${1//\\/\\\\}"
  printf '%s' "${s//\"/\\\"}"
}

# meta.json: scenario, host, time, limits (from docker inspect), git commit, compose
# project, host CPU count and every config value used.
# A limit of 0 (not set in compose) is written as null = unlimited.
write_meta() {
  local limits="" sep="" line svc cpus mem project="" cpu_json mem_json
  while IFS= read -r line; do
    read -r svc cpus mem project <<<"$line"
    [ -z "$svc" ] && continue
    cpu_json=null
    mem_json=null
    [ "$cpus" != 0 ] && cpu_json="$(awk -v n="$cpus" 'BEGIN{printf "%.3f", n/1e9}')"
    [ "$mem" != 0 ] && mem_json="$mem"
    limits+="${sep}\"$(json_str "$svc")\":{\"cpu_limit\":${cpu_json},\"mem_limit_bytes\":${mem_json}}"
    sep=","
  done < <(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{index .Config.Labels "com.docker.compose.project"}}' "${IDS[@]}")
  local commit cfg="" csep="" k
  commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  for k in "${CONFIG_KEYS[@]}"; do
    cfg+="${csep}\"${k}\":\"$(json_str "${!k}")\""
    csep=","
  done
  printf '{"scenario":"%s","host":"%s","started_at":"%s","git_commit":"%s","project":"%s","host_cpus":%s,"limits":{%s},"config":{%s}}\n' \
    "$SCENARIO" "$(json_str "$HOST")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$(json_str "$project")" \
    "$(nproc)" "$limits" "$cfg" >"$OUT/meta.json"
}
write_meta

# Sample docker stats every ~2s; each line is {"ts":<epoch>,<docker stats json>...}.
sample_stats() {
  while true; do
    local t0 ts
    t0="$(date +%s)"
    ts="$(date +%s.%N)"
    docker stats --no-stream --format '{{json .}}' "${IDS[@]}" 2>/dev/null \
      | sed "s/^{/{\"ts\":${ts},/" >>"$OUT/docker_stats.jsonl" || true
    local spent=$(( $(date +%s) - t0 ))
    [ "$spent" -lt 2 ] && sleep $(( 2 - spent ))
  done
}

SAMPLER_PID=""
stop_sampler() {
  if [ -n "$SAMPLER_PID" ]; then
    kill "$SAMPLER_PID" 2>/dev/null || true
    wait "$SAMPLER_PID" 2>/dev/null || true
    SAMPLER_PID=""
  fi
}
trap stop_sampler EXIT INT TERM

: >"$OUT/docker_stats.jsonl"
sample_stats &
SAMPLER_PID=$!

# Every config value (plus scenario) goes into the container with -e.
ENV_ARGS=(-e "SCENARIO=$SCENARIO")
for k in "${CONFIG_KEYS[@]}"; do
  ENV_ARGS+=(-e "$k=${!k}")
done

echo "Scenario: $SCENARIO  Host: $HOST  Output: $OUT"
# Load shapes (see shapes.py) define users and duration, so no -u/-r/-t here.
# --exit-code-on-error 0: failed requests are reported, not fatal to this script.
set +e
"${COMPOSE[@]}" run --rm -T "${ENV_ARGS[@]}" "$LOCUST_SERVICE" \
  -f locustfile.py --headless --host "$HOST" \
  --csv "$REL/locust" --csv-full-history --html "$REL/locust.html" \
  --exit-code-on-error 0
LOCUST_RC=$?
set -e

stop_sampler
trap - EXIT INT TERM

echo "Locust exit code: $LOCUST_RC. Building report..."
"${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python "${ENV_ARGS[@]}" "$LOCUST_SERVICE" report.py "$REL"
echo "Report: $OUT/report.md"
exit "$LOCUST_RC"

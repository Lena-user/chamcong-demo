#!/usr/bin/env bash
# Run one load-test scenario, sample container CPU/RAM, then build the report.
# Usage: loadtest/run.sh <steady|burst|ramp|login_storm> [host]
# Default host is http://api:8000 (inside the compose network). Pass a
# https://xxx.trycloudflare.com URL to go through the tunnel instead.
set -euo pipefail

SCENARIO="${1:-}"
HOST="${2:-http://api:8000}"
case "$SCENARIO" in
  steady|burst|ramp|login_storm) ;;
  *) echo "Usage: $0 <steady|burst|ramp|login_storm> [host]" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_NAME="${STAMP}-${SCENARIO}"
OUT="$ROOT/loadtest/results/$RUN_NAME"
REL="results/$RUN_NAME" # same dir as seen from inside the locust container
mkdir -p "$OUT"

COMPOSE=(docker compose --profile loadtest)

# Running containers of this compose project, excluding the locust runner itself.
mapfile -t IDS < <("${COMPOSE[@]}" ps -q 2>/dev/null || true)
if [ "${#IDS[@]}" -eq 0 ]; then
  echo "No running containers for this compose project. Start it first: docker compose up -d" >&2
  exit 1
fi

# meta.json: scenario, host, time, limits (from docker inspect) and git commit.
write_meta() {
  local limits="" sep="" line svc cpus mem
  while IFS= read -r line; do
    read -r svc cpus mem <<<"$line"
    [ -z "$svc" ] && continue
    limits+="${sep}\"${svc}\":{\"cpu_limit\":$(awk -v n="$cpus" 'BEGIN{printf "%.3f", n/1e9}'),\"mem_limit_bytes\":${mem}}"
    sep=","
  done < <(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}' "${IDS[@]}")
  local commit
  commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  printf '{"scenario":"%s","host":"%s","started_at":"%s","git_commit":"%s","limits":{%s}}\n' \
    "$SCENARIO" "$HOST" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$commit" "$limits" >"$OUT/meta.json"
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

echo "Scenario: $SCENARIO  Host: $HOST  Output: $OUT"
# Load shapes (see locustfile.py) define users and duration, so no -u/-r/-t here.
# --exit-code-on-error 0: failed requests are reported, not fatal to this script.
set +e
"${COMPOSE[@]}" run --rm -T -e "SCENARIO=$SCENARIO" locust \
  -f locustfile.py --headless --host "$HOST" \
  --csv "$REL/locust" --csv-full-history --html "$REL/locust.html" \
  --exit-code-on-error 0
LOCUST_RC=$?
set -e

stop_sampler
trap - EXIT INT TERM

echo "Locust exit code: $LOCUST_RC. Building report..."
"${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python locust report.py "$REL"
echo "Report: $OUT/report.md"
exit "$LOCUST_RC"

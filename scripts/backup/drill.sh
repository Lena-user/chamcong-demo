#!/usr/bin/env bash
# Disaster-recovery drill on a separate compose project (chamcong-drill) and BACKUP_DIR (backups-drill).
# Never touches the main stack.
set -euo pipefail

export CC_PROJECT=chamcong-drill
here=$(dirname "${BASH_SOURCE[0]}")
ROOT_DIR=$(cd "$here/../.." && pwd)
export BACKUP_DIR=$ROOT_DIR/backups-drill
# shellcheck source=lib.sh
. "$here/lib.sh"
[ "$PROJECT" != chamcong ] || die "refusing to run on the main project"

usage() {
  cat <<'USAGE'
Usage: drill.sh [--yes] [--keep]
Runs a full backup/restore rehearsal in project chamcong-drill with ./backups-drill:
migrate+seed, base backup, marker rows A/B, simulated incident (TRUNCATE attendance_events),
PITR to the moment between A and B, then a dump/drop/restore round. Prints PASS/FAIL per step.
  --yes   do not ask for confirmation
  --keep  keep the drill containers, volumes and directory afterwards
USAGE
}

KEEP=0
for a in "$@"; do
  case $a in
    -h | --help) usage; exit 0 ;;
    --yes | -y) YES=1 ;;
    --keep) KEEP=1 ;;
    *) usage >&2; exit 2 ;;
  esac
done

RESULTS=()
CURRENT=
NFAIL=0
pass() { RESULTS+=("PASS|$1|${2:-}"); log "PASS: $1"; }
fail() { RESULTS+=("FAIL|$1|${2:-}"); NFAIL=$((NFAIL + 1)); log "FAIL: $1 ${2:-}"; }

remove_drill_dir() {
  [ -d "$BACKUP_DIR" ] || return 0
  # files may be owned by postgres/root: remove them from a container
  docker run --rm -v "$BACKUP_DIR:/x" postgres:16 sh -c 'rm -rf /x/* /x/.[!.]*' || true
  rmdir "$BACKUP_DIR" 2>/dev/null || true
}

cleanup() {
  local code=$?
  trap - EXIT
  if [ "$code" -ne 0 ] && [ -n "$CURRENT" ]; then fail "$CURRENT" "script error (exit $code)"; fi
  echo
  echo "=========== DRILL RESULT ==========="
  printf '%-6s %-52s %s\n' RESULT STEP DETAIL
  local r
  for r in "${RESULTS[@]}"; do
    IFS='|' read -r st name detail <<<"$r"
    printf '%-6s %-52s %s\n' "$st" "$name" "$detail"
  done
  if [ "$NFAIL" -eq 0 ] && [ "$code" -eq 0 ]; then echo "OVERALL: PASS"; else echo "OVERALL: FAIL"; fi
  if [ "$KEEP" = 0 ]; then
    log "cleaning up project $PROJECT"
    dc down -v --remove-orphans >/dev/null 2>&1 || true
    remove_drill_dir
  fi
  if [ "$NFAIL" -ne 0 ] && [ "$code" -eq 0 ]; then exit 1; fi
  exit "$code"
}
trap cleanup EXIT

confirm "Drill creates compose project '$PROJECT' and $BACKUP_DIR, and deletes both at the end."

CURRENT="0. clean start"
dc down -v --remove-orphans >/dev/null 2>&1 || true
remove_drill_dir
mkdir -p "$BACKUP_DIR/wal"
chmod 777 "$BACKUP_DIR" "$BACKUP_DIR/wal"
CURRENT=

CURRENT="1. start db, migrate and seed"
dc up -d --wait db
dc run --rm --no-deps -T api sh -c 'alembic upgrade head && python -m scripts.seed'
base_rows=$(table_counts | tr '\n' ' ')
if [ "$(sql <<<'select count(*) from employees')" -gt 0 ]; then
  pass "$CURRENT" "tables: $base_rows"
else
  fail "$CURRENT" "no seed data"
fi
CURRENT=

CURRENT="2. backup service + base backup"
dc up -d --no-deps backup
# the loop makes its own first base backup and dump; wait until both exist
for _ in $(seq 1 90); do
  if compgen -G "$BACKUP_DIR/dump/chamcong-*.dump" >/dev/null && compgen -G "$BACKUP_DIR/base/2*/base.tar.gz" >/dev/null; then break; fi
  sleep 2
done
sleep 2
"$here/backup-now.sh" --base >/dev/null
BASE=$(find "$BACKUP_DIR/base" -mindepth 1 -maxdepth 1 -not -name '.*' | sort | tail -n 1)
if [ -f "$BASE/base.tar.gz" ] && [ -f "$BASE/START_WAL" ]; then
  pass "$CURRENT" "$(basename "$BASE"), start WAL $(cat "$BASE/START_WAL")"
else
  fail "$CURRENT" "base backup missing"
fi
CURRENT=

CURRENT="3. insert markers A, record T, insert markers B"
emp=$(sql <<<'select id from employees order by id limit 1')
mark() { # $1 = label, $2 = rows
  sql <<SQL >/dev/null
INSERT INTO attendance_events (employee_id, event_time, source, idempotency_key)
SELECT $emp, now() + (g || ' seconds')::interval, 'drill-$1', gen_random_uuid() FROM generate_series(1, $2) g
SQL
}
mark A 3
T=$(sql <<<'select clock_timestamp()::text')
sleep 4
mark B 2
cur_wal=$(sql <<<'select pg_walfile_name(pg_current_wal_lsn())')
sql <<<'select pg_switch_wal()' >/dev/null
archived=0
for _ in $(seq 1 60); do
  last=$(sql <<<'select coalesce(last_archived_wal, $$$$) from pg_stat_archiver')
  if [[ $last > $cur_wal || $last == "$cur_wal" ]]; then archived=1; break; fi
  sleep 1
done
SNAP=$(table_counts)
if [ "$archived" = 1 ]; then
  pass "$CURRENT" "T=$T, WAL $cur_wal archived"
else
  fail "$CURRENT" "WAL $cur_wal not archived in 60s"
fi
CURRENT=

CURRENT="4. simulate incident (TRUNCATE attendance_events CASCADE)"
sql <<<'TRUNCATE attendance_events CASCADE' >/dev/null
left=$(sql <<<'select count(*) from attendance_events')
if [ "$left" = 0 ]; then pass "$CURRENT" "attendance_events now has 0 rows"; else fail "$CURRENT" "still $left rows"; fi
CURRENT=

CURRENT="5. PITR to T"
"$here/restore-pitr.sh" "$BASE" --target-time "$T" --yes
a=$(sql <<<"select count(*) from attendance_events where source='drill-A'")
b=$(sql <<<"select count(*) from attendance_events where source='drill-B'")
if [ "$a" = 3 ] && [ "$b" = 0 ]; then pass "5a. marker A present, marker B absent" "A=$a B=$b"; else fail "5a. marker A present, marker B absent" "A=$a B=$b"; fi
# expected = snapshot taken before the incident, minus the 2 B rows
want=$(awk -F= '$1=="attendance_events"{$2=$2-2} {print $1"="$2}' <<<"$SNAP")
got=$(table_counts)
if [ "$want" = "$got" ]; then
  pass "5b. all table row counts match pre-incident state"
else
  fail "5b. all table row counts match pre-incident state" "want [$(tr '\n' ' ' <<<"$want")] got [$(tr '\n' ' ' <<<"$got")]"
fi
CURRENT=

CURRENT="6. dump, drop table, restore dump"
"$here/backup-now.sh" >/dev/null
DUMP=$(find "$BACKUP_DIR/dump" -name 'chamcong-*.dump' | sort | tail -n 1)
SNAP2=$(table_counts)
sql <<<'DROP TABLE attendance_daily CASCADE' >/dev/null
"$here/restore-dump.sh" "$DUMP" --yes
got2=$(table_counts)
if [ "$SNAP2" = "$got2" ]; then
  pass "$CURRENT" "$(basename "$DUMP"); counts match"
else
  fail "$CURRENT" "want [$(tr '\n' ' ' <<<"$SNAP2")] got [$(tr '\n' ' ' <<<"$got2")]"
fi
CURRENT=

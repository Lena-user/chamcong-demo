#!/usr/bin/env bash
# Point-in-time recovery from a base backup + archived WAL (destructive for current data).
# shellcheck disable=SC2016 # single quotes are intentional: expanded inside the container
set -euo pipefail
here=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=lib.sh
. "$here/lib.sh"

usage() {
  cat <<'USAGE'
Usage: restore-pitr.sh <base_dir> [--target-time "YYYY-MM-DD HH:MM:SS+07"] [--yes]
  <base_dir>      a directory under <BACKUP_DIR>/base/ (contains base.tar.gz)
  --target-time   recover up to this moment; without it, replay all archived WAL
Stops the stack, tars the current data into <BACKUP_DIR>/pre-restore/, replaces the pgdata volume
with the base backup, replays WAL, then restarts what was running.
USAGE
}

base=
target=
while [ $# -gt 0 ]; do
  case $1 in
    -h | --help) usage; exit 0 ;;
    --yes | -y) YES=1 ;;
    --target-time) shift; target=${1:-}; [ -n "$target" ] || die "--target-time needs a value" ;;
    -*) usage >&2; exit 2 ;;
    *) base=$1 ;;
  esac
  shift
done
[ -n "$base" ] || { usage >&2; exit 2; }
base=$(cd "$base" 2>/dev/null && pwd) || die "base dir not found"
[ -f "$base/base.tar.gz" ] || die "$base/base.tar.gz missing"
if [ -n "$target" ] && ! [[ $target =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9:.]+([+-][0-9]{2}(:?[0-9]{2})?|\ UTC)$ ]]; then
  die "bad --target-time format: $target"
fi

confirm "This REPLACES all data of project '$PROJECT' with base $base${target:+ rolled forward to $target}."

was=$(running_services)
mkdir -p "$BACKUP_DIR/pre-restore"
chmod 777 "$BACKUP_DIR/pre-restore" 2>/dev/null || true
ts=$(date +%Y%m%d-%H%M%S)

log "stopping stack"
dc stop api worker backup db >/dev/null 2>&1 || true

log "replacing pgdata (old data tarred to pre-restore/pgdata-$ts.tar.gz)"
dc run --rm --no-deps -T --user root --entrypoint bash -e TS="$ts" -e TARGET="$target" \
  -v "$base:/restore_base:ro" -v "$BACKUP_DIR/pre-restore:/pre_restore" db -c '
set -euo pipefail
D=/var/lib/postgresql/data
if [ -n "$(ls -A "$D")" ]; then
  tar -czf "/pre_restore/pgdata-$TS.tar.gz" -C "$D" .
  chmod a+rw "/pre_restore/pgdata-$TS.tar.gz"
fi
find "$D" -mindepth 1 -delete
tar -xzf /restore_base/base.tar.gz -C "$D"
if [ -f /restore_base/pg_wal.tar.gz ]; then tar -xzf /restore_base/pg_wal.tar.gz -C "$D/pg_wal"; fi
{
  echo "restore_command = '"'"'test -f /wal_archive/%f.gz && gunzip -c /wal_archive/%f.gz > \"%p\"'"'"'"
  if [ -n "$TARGET" ]; then echo "recovery_target_time = '"'"'$TARGET'"'"'"; fi
  echo "recovery_target_action = '"'"'promote'"'"'"
} >>"$D/postgresql.auto.conf"
touch "$D/recovery.signal"
chown -R postgres:postgres "$D"
chmod 700 "$D"
'

log "starting db, waiting for recovery"
dc start db >/dev/null
ok=0
for _ in $(seq 1 300); do
  r=$(dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select pg_is_in_recovery()"' 2>/dev/null || true)
  if [ "$r" = f ]; then ok=1; break; fi
  if ! has_word db "$(running_services)"; then
    dc logs --tail 30 db >&2 || true
    die "db stopped during recovery"
  fi
  sleep 2
done
[ "$ok" = 1 ] || die "recovery did not finish in time"

log "recovery finished, cleaning recovery settings"
sql <<'SQL' >/dev/null
ALTER SYSTEM RESET restore_command;
ALTER SYSTEM RESET recovery_target_time;
ALTER SYSTEM RESET recovery_target_action;
SQL

for s in backup api worker; do
  if has_word "$s" "$was"; then dc start "$s" >/dev/null; fi
done
log "PITR done"

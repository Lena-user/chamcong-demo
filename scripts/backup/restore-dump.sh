#!/usr/bin/env bash
# Restore the database from a pg_dump -Fc file (destructive for the current data).
# shellcheck disable=SC2016 # single quotes are intentional: expanded inside the container
set -euo pipefail
here=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=lib.sh
. "$here/lib.sh"

usage() {
  cat <<'USAGE'
Usage: restore-dump.sh <file.dump> [--yes]
Takes a safety dump of the current DB into <BACKUP_DIR>/pre-restore/, stops api and worker,
runs pg_restore --clean --if-exists (single transaction), restarts what was running,
and prints table row counts before and after.
USAGE
}

file=
for a in "$@"; do
  case $a in
    -h | --help) usage; exit 0 ;;
    --yes | -y) YES=1 ;;
    -*) usage >&2; exit 2 ;;
    *) file=$a ;;
  esac
done
[ -n "$file" ] || { usage >&2; exit 2; }
[ -f "$file" ] || die "file not found: $file"

confirm "This REPLACES the data of project '$PROJECT' with $file."

dc exec -T db pg_restore --list <"$file" >/dev/null || die "not a valid dump: $file"

mkdir -p "$BACKUP_DIR/pre-restore"
chmod 777 "$BACKUP_DIR/pre-restore" 2>/dev/null || true
safety=$BACKUP_DIR/pre-restore/dump-$(date +%Y%m%d-%H%M%S).dump
log "safety dump of current DB -> $safety"
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$safety"
dc exec -T db pg_restore --list <"$safety" >/dev/null || die "safety dump is broken, aborting"

was=$(running_services)
log "row counts before:"
table_counts | sed 's/^/  /'

log "stopping api and worker"
dc stop api worker >/dev/null 2>&1 || true

log "restoring"
dc exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction' <"$file"

for s in api worker; do
  if has_word "$s" "$was"; then dc start "$s" >/dev/null; fi
done

log "row counts after:"
table_counts | sed 's/^/  /'
log "done (safety dump kept at $safety)"

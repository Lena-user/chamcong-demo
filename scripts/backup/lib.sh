# Shared helpers for the host-side backup scripts. Sourced, not executed.
# shellcheck shell=bash
# shellcheck disable=SC2016 # single quotes are intentional: expanded inside the container

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PROJECT=${CC_PROJECT:-chamcong}

# BACKUP_DIR: environment, then .env, then ./backups; always made absolute.
if [ -z "${BACKUP_DIR:-}" ] && [ -f "$ROOT/.env" ]; then
  BACKUP_DIR=$(grep -E '^BACKUP_DIR=' "$ROOT/.env" | tail -n 1 | cut -d= -f2- || true)
fi
BACKUP_DIR=${BACKUP_DIR:-./backups}
case $BACKUP_DIR in
  /*) ;;
  *) BACKUP_DIR=$ROOT/${BACKUP_DIR#./} ;;
esac
export BACKUP_DIR

YES=0

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# docker compose bound to the right project and directory
dc() { docker compose -p "$PROJECT" --project-directory "$ROOT" "$@"; }

# Run SQL (read from stdin) in the db container, unaligned tuples-only output.
sql() {
  dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At'
}

# Row count of every table in schema public, as "table=count" lines.
table_counts() {
  sql <<'SQL'
SELECT relname || '=' ||
  (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', relname), false, true, '')))[1]::text
FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY relname
SQL
}

# Ask for confirmation unless --yes was given.
confirm() {
  [ "$YES" = 1 ] && return 0
  printf '%s\nType "yes" to continue: ' "$1"
  local reply
  read -r reply
  [ "$reply" = yes ] || die "aborted"
}

# Space separated list of compose services currently running.
running_services() { dc ps --status running --services | tr '\n' ' '; }

# Succeeds when $1 appears in the word list $2.
has_word() { case " $2 " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

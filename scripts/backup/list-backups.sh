#!/usr/bin/env bash
# List dumps, base backups and the span of archived WAL.
set -euo pipefail
here=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=lib.sh
. "$here/lib.sh"

usage() {
  cat <<'USAGE'
Usage: list-backups.sh
Lists dumps, base backups, WAL range and disk usage under BACKUP_DIR (default ./backups).
USAGE
}
case ${1:-} in
  -h | --help) usage; exit 0 ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

echo "Backup dir: $BACKUP_DIR"
echo
echo "== Dumps (pg_dump -Fc) =="
if compgen -G "$BACKUP_DIR/dump/*.dump" >/dev/null; then
  find "$BACKUP_DIR/dump" -maxdepth 1 -name '*.dump' -not -name '.*' | sort | while read -r f; do
    printf '%s  %8s\n' "$f" "$(du -h "$f" | cut -f1)"
  done
else
  echo "(none)"
fi
echo
echo "== Base backups (PITR starting points) =="
if [ -d "$BACKUP_DIR/base" ] && [ -n "$(find "$BACKUP_DIR/base" -mindepth 1 -maxdepth 1 -not -name '.*')" ]; then
  find "$BACKUP_DIR/base" -mindepth 1 -maxdepth 1 -not -name '.*' | sort | while read -r d; do
    printf '%s  %8s  start WAL %s\n' "$d" "$(du -sh "$d" | cut -f1)" "$(cat "$d/START_WAL" 2>/dev/null || echo '?')"
  done
else
  echo "(none)"
fi
echo
echo "== WAL archive =="
if compgen -G "$BACKUP_DIR/wal/*.gz" >/dev/null; then
  mapfile -t wals < <(find "$BACKUP_DIR/wal" -maxdepth 1 -name '*.gz' -not -name '*.history.gz' -not -name '*.backup.gz' | sort)
  if [ "${#wals[@]}" -gt 0 ]; then
    first=${wals[0]}
    last=${wals[${#wals[@]} - 1]}
    echo "segments: ${#wals[@]}, size $(du -sh "$BACKUP_DIR/wal" | cut -f1)"
    echo "oldest:   $(basename "$first")  archived $(date -r "$first" '+%F %T %z')"
    echo "newest:   $(basename "$last")  archived $(date -r "$last" '+%F %T %z')"
  fi
else
  echo "(none)"
fi
echo
echo "Total: $(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)"

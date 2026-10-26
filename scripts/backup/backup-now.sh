#!/usr/bin/env bash
# Take a backup right now from the host (uses the "backup" service image).
set -euo pipefail
here=$(dirname "${BASH_SOURCE[0]}")
# shellcheck source=lib.sh
. "$here/lib.sh"

usage() {
  cat <<'USAGE'
Usage: backup-now.sh [--base]
  (no option)  pg_dump -Fc into <BACKUP_DIR>/dump/chamcong-YYYYmmdd-HHMMSS.dump
  --base       pg_basebackup into <BACKUP_DIR>/base/<timestamp>/ (PITR starting point)
Environment: BACKUP_DIR (default ./backups), CC_PROJECT (compose project, default chamcong)
USAGE
}

mode=dump
for a in "$@"; do
  case $a in
    -h | --help) usage; exit 0 ;;
    --base) mode=base ;;
    *) usage >&2; exit 2 ;;
  esac
done

dc run --rm --no-deps -T backup "$mode"

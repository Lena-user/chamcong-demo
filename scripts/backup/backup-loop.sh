#!/usr/bin/env bash
# Runs inside the "backup" container (postgres:16 image, /backups = BACKUP_DIR).
#   loop  (default) periodic dumps + base backups + pruning
#   dump  one pg_dump -Fc now
#   base  one pg_basebackup now
#   prune apply retention
set -euo pipefail
umask 000 # files must stay deletable by the host user

usage() {
  cat <<'USAGE'
Usage: backup-loop.sh [loop|dump|base|prune]
Runs inside the backup container. Environment: PGHOST/PGUSER/PGPASSWORD/PGDATABASE,
BACKUP_INTERVAL_MINUTES, BASEBACKUP_INTERVAL_HOURS, BACKUP_KEEP_DUMPS, BACKUP_KEEP_BASE.
USAGE
}

B=/backups
KEEP_DUMPS=${BACKUP_KEEP_DUMPS:-48}
KEEP_BASE=${BACKUP_KEEP_BASE:-3}

log() { printf '[backup %s] %s\n' "$(date '+%F %T')" "$*"; }

prepare_dirs() {
  mkdir -p "$B/dump" "$B/base" "$B/wal" "$B/pre-restore"
  # postgres (uid 999) writes WAL here from the db container
  chmod 777 "$B" "$B/dump" "$B/base" "$B/wal" "$B/pre-restore" 2>/dev/null || true
}

do_dump() {
  local ts tmp out
  ts=$(date +%Y%m%d-%H%M%S)
  tmp=$B/dump/.tmp-chamcong-$ts.dump
  out=$B/dump/chamcong-$ts.dump
  pg_dump -Fc -f "$tmp"
  mv "$tmp" "$out"
  if ! pg_restore --list "$out" >/dev/null; then
    rm -f "$out"
    log "ERROR: dump $out is not readable, removed"
    return 1
  fi
  log "dump ok: $out ($(du -h "$out" | cut -f1))"
}

do_base() {
  local ts tmp start
  ts=$(date +%Y%m%d-%H%M%S)
  tmp=$B/base/.tmp-$ts
  rm -rf "$tmp"
  pg_basebackup -D "$tmp" -Ft -z -X stream -c fast
  tar -tzf "$tmp/base.tar.gz" >/dev/null
  [ -f "$tmp/pg_wal.tar.gz" ] || { rm -rf "$tmp"; log "ERROR: base backup incomplete"; return 1; }
  # first WAL segment needed; older archived WAL can be pruned
  start=$(tar -xzOf "$tmp/base.tar.gz" backup_label | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]\{24\}\)).*/\1/p')
  [ -n "$start" ] && echo "$start" >"$tmp/START_WAL"
  mv "$tmp" "$B/base/$ts"
  chmod -R a+rwX "$B/base/$ts"
  log "base backup ok: $B/base/$ts (start WAL $start)"
}

# keep the newest $2 entries (by name) of directory $1
prune_dir() {
  local i=0 f
  while IFS= read -r f; do
    i=$((i + 1))
    if [ "$i" -gt "$2" ]; then rm -rf -- "$f"; fi
  done < <(find "$1" -mindepth 1 -maxdepth 1 -not -name '.*' | sort -r)
}

do_prune() {
  local oldest wal
  prune_dir "$B/dump" "$KEEP_DUMPS"
  prune_dir "$B/base" "$KEEP_BASE"
  oldest=$(find "$B/base" -mindepth 1 -maxdepth 1 -not -name '.*' | sort | head -n 1)
  if [ -n "$oldest" ] && [ -f "$oldest/START_WAL" ]; then
    wal=$(cat "$oldest/START_WAL")
    pg_archivecleanup -x .gz "$B/wal" "$wal"
    log "pruned; WAL kept from $wal"
  fi
}

# epoch of the newest non-hidden entry in $1 (0 if none)
newest_epoch() {
  local t
  t=$(find "$1" -mindepth 1 -maxdepth 1 -not -name '.*' -printf '%T@\n' | sort -n | tail -n 1)
  t=${t%.*}
  echo "${t:-0}"
}

do_loop() {
  local di=$(( ${BACKUP_INTERVAL_MINUTES:-30} * 60 )) bi=$(( ${BASEBACKUP_INTERVAL_HOURS:-24} * 3600 )) now
  prepare_dirs
  log "loop start: dump every ${di}s, base every ${bi}s, keep $KEEP_DUMPS dumps / $KEEP_BASE bases"
  if [ -z "$(newest_epoch "$B/base")" ] || [ "$(newest_epoch "$B/base")" = 0 ]; then
    do_base || log "initial base backup failed"
  fi
  while true; do
    now=$(date +%s)
    if [ $((now - $(newest_epoch "$B/dump"))) -ge "$di" ]; then do_dump || log "dump failed"; fi
    if [ $((now - $(newest_epoch "$B/base"))) -ge "$bi" ]; then do_base || log "base backup failed"; fi
    do_prune || log "prune failed"
    sleep 30
  done
}

case ${1:-loop} in
  -h | --help) usage ;;
  loop) do_loop ;;
  dump) prepare_dirs; do_dump ;;
  base) prepare_dirs; do_base ;;
  prune) do_prune ;;
  *) usage >&2; exit 2 ;;
esac

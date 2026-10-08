#!/usr/bin/env bash
# Backs up Jellyfin's config + database (NOT the media) to /opt/jellyfin/backups and keeps the newest 7.
# The SQLite database is only copied consistently while Jellyfin is stopped, so it is stopped for a few seconds.
# Run weekly from cron (see docs/07-backups-and-maintenance.md).
#
# Optional off-server copy: set REMOTE (rsync target), e.g. REMOTE=user@home-pc:/backups/jellyfin/
set -euo pipefail

BASE="${BASE:-/opt/jellyfin}"
KEEP="${KEEP:-7}"
REMOTE="${REMOTE:-}"
STAMP="$(date +%F_%H%M)"
OUT="$BASE/backups/jellyfin-config-$STAMP.tar.gz"

mkdir -p "$BASE/backups"
cd "$BASE"
trap 'docker compose start jellyfin >/dev/null' EXIT
docker compose stop jellyfin >/dev/null

tar --exclude='config/log' --exclude='config/transcodes' --exclude='config/cache' \
    -czf "$OUT" config compose.yaml Caddyfile .env
chmod 600 "$OUT"

ls -1t "$BASE"/backups/jellyfin-config-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "Backup written: $OUT ($(du -h "$OUT" | cut -f1))"

if [[ -n "$REMOTE" ]]; then
  rsync -a --delete "$BASE/backups/" "$REMOTE" && echo "Copied to $REMOTE"
fi

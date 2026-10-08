#!/usr/bin/env bash
# Warns when a filesystem is nearly full. Writes to the system log (journalctl -t disk-alert)
# and, if NTFY_URL is set (e.g. https://ntfy.sh/your-secret-topic), sends a push notification.
# Cron example (hourly):  0 * * * * root /opt/jellyfin/scripts/disk-alert.sh
set -euo pipefail
THRESHOLD="${THRESHOLD:-85}"
NTFY_URL="${NTFY_URL:-}"

for MP in / /srv/media; do
  mountpoint -q "$MP" || continue
  USED="$(df --output=pcent "$MP" | tail -n 1 | tr -dc '0-9')"
  if (( USED >= THRESHOLD )); then
    MSG="$(hostname): $MP is ${USED}% full"
    logger -t disk-alert "$MSG"
    [[ -n "$NTFY_URL" ]] && curl -fsS -m 10 -d "$MSG" "$NTFY_URL" >/dev/null || true
  fi
done

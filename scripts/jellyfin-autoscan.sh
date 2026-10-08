#!/usr/bin/env bash
# Asks Jellyfin to scan its libraries shortly after the media folder changes.
#
# Why: Jellyfin's own real-time watcher only starts for library folders that already contain files when
# Jellyfin starts or scans. A library that was empty at that moment (a new TV Shows or Music library, for
# example) is not watched, so uploads into it stay invisible until the next scheduled scan (every 12 hours).
# This service does not depend on that behaviour.
#
# Runs as a systemd service (installed by scripts/07-enable-autoscan.sh).
set -u

WATCH="${WATCH:-/srv/media}"
QUIET="${QUIET:-30}"                                  # seconds with no change before scanning
ENVF="${ENVF:-/etc/selfhost-jellyfin/autoscan.env}"   # contains JELLYFIN_API_KEY=...
CONTAINER="${CONTAINER:-jellyfin}"

# shellcheck disable=SC1090
. "$ENVF"
: "${JELLYFIN_API_KEY:?missing in $ENVF}"

scan() {
  # The API call runs INSIDE the Jellyfin container, so Jellyfin's port is never published on the host.
  if docker exec -e K="$JELLYFIN_API_KEY" "$CONTAINER" sh -c \
      'curl -fsS -m 30 -o /dev/null -X POST -H "Authorization: MediaBrowser Token=\"$K\"" http://localhost:8096/Library/Refresh'; then
    logger -t jellyfin-autoscan "library refresh requested"
  else
    logger -t jellyfin-autoscan "library refresh FAILED (is the jellyfin container running?)"
  fi
}

logger -t jellyfin-autoscan "watching $WATCH (quiet period ${QUIET}s)"
inotifywait -m -r -q -e close_write,moved_to,create,delete,moved_from --format '%w%f' \
    --exclude '(\.uploading$|/lost\+found)' "$WATCH" |
while read -r _; do
  while read -r -t "$QUIET" _; do :; done             # keep waiting while more changes arrive (big uploads)
  scan
done

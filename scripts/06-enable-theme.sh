#!/usr/bin/env bash
# OPTIONAL: install the theme + home-page banner (see docs/12-theme.md).
#   sudo bash scripts/06-enable-theme.sh
# Downloads the community theme (Abyss, MIT, pinned to an exact commit), copies our own banner files,
# adds the banner helper to COMPOSE_FILE and restarts the stack. Then paste the Custom CSS lines it prints into Jellyfin.
set -euo pipefail
BASE="${BASE:-/opt/jellyfin}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SHA="${ABYSS_SHA:-2f809171ed7f3a34b5e78297608b5f2b63dd3f0f}"      # Abyss v1.2.3 + fixes; review before changing

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ -f "$BASE/.env" ]] || { echo "$BASE/.env not found. Deploy the main stack first."; exit 1; }
set -a; . "$BASE/.env"; set +a
: "${PUID:?}" "${PGID:?}"

mkdir -p "$BASE/theme" "$BASE/web"
curl -fsSL -o "$BASE/theme/abyss.css" "https://raw.githubusercontent.com/AumGupta/abyss-jellyfin/$SHA/abyss.css"
cp "$HERE/deploy/theme/custom.css" "$HERE/deploy/theme/hero.css" "$HERE/deploy/theme/hero.js" "$BASE/theme/"
cp "$HERE/deploy/theme/compose.theme.yaml" "$HERE/deploy/Caddyfile" "$HERE/deploy/compose.yaml" "$BASE/"
chown -R "$PUID:$PGID" "$BASE/web"

# Make plain "docker compose up -d" include every optional file (COMPOSE_FILE is read from .env).
CUR="$(grep -E '^COMPOSE_FILE=' "$BASE/.env" | cut -d= -f2- || true)"
CUR="${CUR:-compose.yaml}"
case ":$CUR:" in *":compose.theme.yaml:"*) ;; *) CUR="$CUR:compose.theme.yaml" ;; esac
grep -vE '^COMPOSE_FILE=' "$BASE/.env" > "$BASE/.env.new" || true
echo "COMPOSE_FILE=$CUR" >> "$BASE/.env.new" && mv "$BASE/.env.new" "$BASE/.env"

cd "$BASE"
docker compose up -d
echo
echo "Now in Jellyfin: Dashboard -> General -> Custom CSS, paste:"
echo '  @import url("/theme/abyss.css");'
echo '  @import url("/theme/custom.css");'
echo '  @import url("/theme/hero.css");'
echo "Then hard-reload the browser (Ctrl+Shift+R)."

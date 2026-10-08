#!/usr/bin/env bash
# OPTIONAL: install the theme + home-page banner (see docs/12-theme.md).
#   sudo bash scripts/06-enable-theme.sh
# Downloads the community theme (ElegantFin, GPL-2.0, pinned to an exact commit), copies our own banner files,
# adds the banner helper to COMPOSE_FILE and restarts the stack. Then paste the Custom CSS lines it prints into Jellyfin.
set -euo pipefail
BASE="${BASE:-/opt/jellyfin}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SHA="${ELEGANTFIN_SHA:-9d43fa9b898c74055237133b7a7b8b8c5543f0ce}"      # review before changing
FILE="${ELEGANTFIN_FILE:-ElegantFin-theme-v26.09.05.css}"

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ -f "$BASE/.env" ]] || { echo "$BASE/.env not found. Deploy the main stack first."; exit 1; }
set -a; . "$BASE/.env"; set +a
: "${PUID:?}" "${PGID:?}"

mkdir -p "$BASE/theme" "$BASE/web"
curl -fsSL -o "$BASE/theme/elegantfin.css" "https://raw.githubusercontent.com/lscambo13/ElegantFin/$SHA/Theme/$FILE"
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
echo '  @import url("/theme/elegantfin.css");'
echo '  @import url("/theme/custom.css");'
echo '  @import url("/theme/hero.css");'
echo "Then hard-reload the browser (Ctrl+Shift+R)."

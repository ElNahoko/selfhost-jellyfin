#!/usr/bin/env bash
# OPTIONAL: set up the browser upload page (File Browser) on top of the running stack.
# Run on the server after the main stack works:
#
#   sudo bash scripts/05-setup-uploads.sh files.example.com
#
# It creates ONE admin account for uploads with a random password that is printed ONCE.
# Set UPLOAD_PASSWORD to choose your own (at least 12 characters). The page is reachable only over HTTPS,
# on its own hostname, and only this account can write to /srv/media.
set -euo pipefail

FILES_DOMAIN="${1:-}"
BASE="${BASE:-/opt/jellyfin}"
UPLOAD_USER="${UPLOAD_USER:-uploader}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ "$FILES_DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]] || { echo "Usage: $0 files.your-domain.tld"; exit 1; }
[[ -f "$BASE/.env" ]] || { echo "$BASE/.env not found. Deploy the main stack first."; exit 1; }

set -a; . "$BASE/.env"; set +a
: "${PUID:?}" "${PGID:?}"
FILEBROWSER_VERSION="${FILEBROWSER_VERSION:-v2.63.23}"
IMAGE="filebrowser/filebrowser:$FILEBROWSER_VERSION"

mkdir -p "$BASE/filebrowser/database" "$BASE/filebrowser/branding" "$BASE/uploads" "$BASE/sites"
cp -r "$HERE/deploy/uploads/branding/." "$BASE/filebrowser/branding/"
cp "$HERE/deploy/uploads/compose.uploads.yaml" "$BASE/uploads/compose.uploads.yaml"
sed "s/{\$FILES_DOMAIN}/$FILES_DOMAIN/" "$HERE/deploy/uploads/files.caddy.example" > "$BASE/sites/files.caddy"
grep -q '^FILEBROWSER_VERSION=' "$BASE/.env" || echo "FILEBROWSER_VERSION=$FILEBROWSER_VERSION" >> "$BASE/.env"
chown -R "$PUID:$PGID" "$BASE/filebrowser"

FB=(docker run --rm --user "$PUID:$PGID" -v "$BASE/filebrowser/database:/database" -v "$BASE/filebrowser/branding:/branding:ro"
    --entrypoint /bin/filebrowser "$IMAGE")

if [[ ! -f "$BASE/filebrowser/database/filebrowser.db" ]]; then
  "${FB[@]}" config init --database /database/filebrowser.db --root /srv --address 0.0.0.0 --port 8080 \
    --branding.name "Media Uploads" --branding.files /branding --branding.color "#6a4fd8" \
    --branding.disableExternal --locale en --signup=false >/dev/null
  PASS="${UPLOAD_PASSWORD:-$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)}"
  [[ ${#PASS} -ge 12 ]] || { echo "Password must be at least 12 characters."; exit 1; }
  "${FB[@]}" users add "$UPLOAD_USER" "$PASS" --perm.admin --database /database/filebrowser.db >/dev/null
  if [[ -z "${UPLOAD_PASSWORD:-}" ]]; then
    echo "================================================="
    echo " Upload page login   user: $UPLOAD_USER"
    echo "                     password: $PASS     (shown once, store it safely)"
    echo "================================================="
  else
    echo "Upload account '$UPLOAD_USER' created with the password you provided."
  fi
else
  echo "Existing File Browser database found: keeping accounts and settings."
fi

cd "$BASE"
docker compose -f compose.yaml -f uploads/compose.uploads.yaml up -d
docker compose -f compose.yaml -f uploads/compose.uploads.yaml exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || docker compose restart caddy
echo "Open https://$FILES_DOMAIN"

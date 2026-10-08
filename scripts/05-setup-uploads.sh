#!/usr/bin/env bash
# OPTIONAL: set up the browser upload page on top of the running stack.
# Run on the server after the main stack works, with a hostname that already points at the server:
#
#   sudo bash scripts/05-setup-uploads.sh files.example.com
#
# Creates ONE upload account ("uploader") with a random password that is printed ONCE
# (or set UPLOAD_PASSWORD yourself, at least 16 characters). Reachable only over HTTPS on its own
# hostname; only this account can write to /srv/media.
set -euo pipefail

FILES_DOMAIN="${1:-}"
BASE="${BASE:-/opt/jellyfin}"
UPLOAD_USER="${UPLOAD_USER:-uploader}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ "$FILES_DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]] || { echo "Usage: $0 files.your-domain.tld"; exit 1; }
[[ -f "$BASE/.env" ]] || { echo "$BASE/.env not found. Deploy the main stack first."; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required."; exit 1; }

set -a; . "$BASE/.env"; set +a
: "${PUID:?}" "${PGID:?}"
DUFS_VERSION="${DUFS_VERSION:-v0.46.0}"
grep -q '^DUFS_VERSION=' "$BASE/.env" || echo "DUFS_VERSION=$DUFS_VERSION" >> "$BASE/.env"

mkdir -p "$BASE/uploads/assets" "$BASE/sites"
cp "$HERE/deploy/uploads/assets/index.html" "$BASE/uploads/assets/index.html"
cp "$HERE/deploy/uploads/compose.uploads.yaml" "$BASE/compose.uploads.yaml"
sed "s/FILES_DOMAIN_PLACEHOLDER/$FILES_DOMAIN/" "$HERE/deploy/uploads/files.caddy.example" > "$BASE/sites/files.caddy"

if [[ ! -f "$BASE/uploads.env" ]]; then
  PASS="${UPLOAD_PASSWORD:-$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 26)}"
  [[ ${#PASS} -ge 16 ]] || { echo "Password must be at least 16 characters."; exit 1; }
  [[ "$PASS" =~ ^[A-Za-z0-9._~+-]+$ ]] || { echo "Use only letters, digits and . _ ~ + - in the password."; exit 1; }
  HASH="$(openssl passwd -6 "$PASS")"
  umask 077
  printf "DUFS_AUTH='%s:%s@/:rw'\n" "$UPLOAD_USER" "$HASH" > "$BASE/uploads.env"
  if [[ -z "${UPLOAD_PASSWORD:-}" ]]; then
    echo "================================================="
    echo " Upload page login   user: $UPLOAD_USER"
    echo "                     password: $PASS      (shown once, store it safely)"
    echo "================================================="
  else
    echo "Upload account '$UPLOAD_USER' created with the password you provided."
  fi
else
  echo "Keeping the existing upload account ($BASE/uploads.env)."
fi

cd "$BASE"
docker compose -f compose.yaml -f compose.uploads.yaml up -d
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || docker compose restart caddy
echo "Open https://$FILES_DOMAIN"

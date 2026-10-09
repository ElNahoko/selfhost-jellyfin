#!/usr/bin/env bash
# OPTIONAL: set up the upload + catalogue site (LUMIO) on top of the running stack.
# Run on the server after the main stack works, with a hostname that already points at the server:
#
#   sudo JELLYFIN_API_KEY=<admin key> bash scripts/05-setup-uploads.sh files.example.com
#
# What you get on https://files.example.com
#   - one branded sign-in page (no browser pop-up)
#   - admin ("uploader"): uploads, deletes, folder sizes, server stats, profiles, requests
#   - guest profiles (made in the page): catalogue + requests only
# The admin password is generated and printed ONCE (or set UPLOAD_PASSWORD yourself, 12+ characters).
# JELLYFIN_API_KEY: any Jellyfin admin API key (Dashboard > API Keys). It is only used to create a separate
# key named "upload-meta" for the helper service. If the autoscan service is installed, its key is found automatically.
set -euo pipefail

FILES_DOMAIN="${1:-}"
BASE="${BASE:-/opt/jellyfin}"
UPLOAD_USER="${UPLOAD_USER:-uploader}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ "$FILES_DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]] || { echo "Usage: $0 files.your-domain.tld"; exit 1; }
[[ -f "$BASE/.env" ]] || { echo "$BASE/.env not found. Deploy the main stack first."; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required."; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required."; exit 1; }

set -a; . "$BASE/.env"; set +a
: "${PUID:?}" "${PGID:?}"
DUFS_VERSION="${DUFS_VERSION:-v0.46.0}"
grep -q '^DUFS_VERSION=' "$BASE/.env" || echo "DUFS_VERSION=$DUFS_VERSION" >> "$BASE/.env"

# ---- files ----
mkdir -p "$BASE/uploads/assets" "$BASE/uploads/meta" "$BASE/uploads/data" "$BASE/sites"
cp "$HERE/deploy/uploads/assets/index.html" "$BASE/uploads/assets/index.html"
cp "$HERE"/deploy/uploads/meta/*.py "$HERE/deploy/uploads/meta/login.html" "$BASE/uploads/meta/"
chmod 755 "$BASE/uploads/meta"; chmod 644 "$BASE"/uploads/meta/*
chown "$PUID:$PGID" "$BASE/uploads/data"; chmod 700 "$BASE/uploads/data"
cp "$HERE/deploy/uploads/compose.uploads.yaml" "$BASE/compose.uploads.yaml"
sed "s/FILES_DOMAIN_PLACEHOLDER/$FILES_DOMAIN/" "$HERE/deploy/uploads/files.caddy.example" > "$BASE/sites/files.caddy"

# ---- the file server has no login of its own now: Caddy only lets signed-in admins reach it ----
OLD_HASH=""
if [[ -f "$BASE/uploads.env" ]]; then
  OLD_HASH="$(sed -n "s/^DUFS_AUTH='[^:]*:\(\$6\$[^@]*\)@.*/\1/p" "$BASE/uploads.env" | head -1)"
fi
umask 077
echo "# dufs has no login of its own: Caddy forwards only signed-in admins (see sites/files.caddy)" > "$BASE/uploads.env"

# ---- admin account + Jellyfin key for the helper service ----
if [[ ! -f "$BASE/uploads-meta.env" ]] || ! grep -q '^ADMIN_HASH=' "$BASE/uploads-meta.env"; then
  if [[ -n "$OLD_HASH" ]]; then
    HASH="$OLD_HASH"; echo "Keeping your existing upload password."
  else
    PASS="${UPLOAD_PASSWORD:-$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 22)}"
    [[ ${#PASS} -ge 12 ]] || { echo "Password must be at least 12 characters."; exit 1; }
    [[ "$PASS" =~ ^[A-Za-z0-9._~+-]+$ ]] || { echo "Use only letters, digits and . _ ~ + - in the password."; exit 1; }
    HASH="$(openssl passwd -6 "$PASS")"
    if [[ -z "${UPLOAD_PASSWORD:-}" ]]; then
      echo "================================================="
      echo " Sign-in   name: $UPLOAD_USER"
      echo "           password: $PASS      (shown once, store it safely)"
      echo "           You can change it later in Settings."
      echo "================================================="
    fi
  fi
  KEY="${JELLYFIN_API_KEY:-}"
  [[ -n "$KEY" ]] || KEY="$(sed -n 's/^JELLYFIN_API_KEY=//p' /etc/selfhost-jellyfin/autoscan.env 2>/dev/null | head -1 || true)"
  [[ -n "$KEY" ]] || { echo "Need a Jellyfin API key: Dashboard > API Keys > +, then run again with JELLYFIN_API_KEY=<key>."; exit 1; }
  cd "$BASE"
  jf() { docker compose exec -T jellyfin curl -fsS -m 20 -H "Authorization: MediaBrowser Token=\"$KEY\"" "$@"; }
  if ! jf -X POST "http://localhost:8096/Auth/Keys?app=upload-meta" >/dev/null 2>&1; then echo "Could not create a separate key; using the one you gave."; META_KEY="$KEY"
  else
    META_KEY="$(jf http://localhost:8096/Auth/Keys | python3 -c 'import sys,json; k=[i["AccessToken"] for i in json.load(sys.stdin)["Items"] if i["AppName"]=="upload-meta"]; print(k[-1] if k else "")')"
    [[ -n "$META_KEY" ]] || META_KEY="$KEY"
  fi
  printf "JELLYFIN_API_KEY=%s\nADMIN_USER=%s\nADMIN_HASH='%s'\n" "$META_KEY" "$UPLOAD_USER" "$HASH" > "$BASE/uploads-meta.env"
  chmod 600 "$BASE/uploads-meta.env"
else
  echo "Keeping the existing admin account ($BASE/uploads-meta.env)."
fi

# Make plain "docker compose up -d" include every optional file (COMPOSE_FILE is read from .env).
CUR="$(grep -E '^COMPOSE_FILE=' "$BASE/.env" | cut -d= -f2- || true)"
CUR="${CUR:-compose.yaml}"
case ":$CUR:" in *":compose.uploads.yaml:"*) ;; *) CUR="$CUR:compose.uploads.yaml" ;; esac
grep -vE '^COMPOSE_FILE=' "$BASE/.env" > "$BASE/.env.new" || true
echo "COMPOSE_FILE=$CUR" >> "$BASE/.env.new" && mv "$BASE/.env.new" "$BASE/.env"

cd "$BASE"
docker compose up -d --force-recreate uploads uploads-meta
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || docker compose restart caddy
echo "Open https://$FILES_DOMAIN and sign in. Create guest profiles under Profiles."

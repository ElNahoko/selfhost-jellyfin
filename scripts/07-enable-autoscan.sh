#!/usr/bin/env bash
# Installs the "scan after media changes" service (see scripts/jellyfin-autoscan.sh for why).
#
# 1. In Jellyfin: Dashboard -> API Keys -> add a key named "autoscan" and copy it.
# 2. On the server:   sudo JELLYFIN_API_KEY=<the key> bash scripts/07-enable-autoscan.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
: "${JELLYFIN_API_KEY:?Set JELLYFIN_API_KEY (Dashboard -> API Keys)}"
HERE="$(cd "$(dirname "$0")" && pwd)"

DEBIAN_FRONTEND=noninteractive apt-get install -y -qq inotify-tools >/dev/null

# plenty of watch slots for big libraries (one per folder)
echo 'fs.inotify.max_user_watches=524288' > /etc/sysctl.d/99-selfhost-inotify.conf
sysctl -q --system >/dev/null

install -d -m 700 /etc/selfhost-jellyfin
umask 077
printf 'JELLYFIN_API_KEY=%s\n' "$JELLYFIN_API_KEY" > /etc/selfhost-jellyfin/autoscan.env
umask 022
install -m 755 "$HERE/jellyfin-autoscan.sh" /usr/local/sbin/jellyfin-autoscan.sh

cat > /etc/systemd/system/jellyfin-autoscan.service <<'EOF'
[Unit]
Description=Ask Jellyfin to scan after media changes
After=docker.service
Requires=docker.service

[Service]
ExecStart=/usr/local/sbin/jellyfin-autoscan.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now jellyfin-autoscan.service
sleep 2
systemctl --no-pager --lines=3 status jellyfin-autoscan.service | head -6
echo "Done. New or changed media is scanned about 30 seconds after the last change. Log: journalctl -t jellyfin-autoscan"

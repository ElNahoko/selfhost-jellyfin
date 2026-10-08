#!/usr/bin/env bash
# Installs the "scan after media changes" service (see scripts/jellyfin-autoscan.py for why and how).
#
# 1. In Jellyfin: Dashboard -> API Keys -> add a key named "autoscan" and copy it.
# 2. On the server:   sudo JELLYFIN_API_KEY=<the key> bash scripts/07-enable-autoscan.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
: "${JELLYFIN_API_KEY:?Set JELLYFIN_API_KEY (Dashboard -> API Keys)}"
HERE="$(cd "$(dirname "$0")" && pwd)"

DEBIAN_FRONTEND=noninteractive apt-get install -y -qq inotify-tools python3 >/dev/null

# plenty of watch slots for big libraries (one per folder)
echo 'fs.inotify.max_user_watches=524288' > /etc/sysctl.d/99-selfhost-inotify.conf
sysctl -q --system >/dev/null

install -d -m 700 /etc/selfhost-jellyfin
umask 077
printf 'JELLYFIN_API_KEY=%s\n' "$JELLYFIN_API_KEY" > /etc/selfhost-jellyfin/autoscan.env
umask 022
install -m 755 "$HERE/jellyfin-autoscan.py" /usr/local/sbin/jellyfin-autoscan
rm -f /usr/local/sbin/jellyfin-autoscan.sh

cat > /etc/systemd/system/jellyfin-autoscan.service <<'EOF'
[Unit]
Description=Ask Jellyfin to scan after media changes
After=docker.service
Requires=docker.service

[Service]
ExecStart=/usr/bin/python3 /usr/local/sbin/jellyfin-autoscan
SyslogIdentifier=jellyfin-autoscan
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable jellyfin-autoscan.service
systemctl restart jellyfin-autoscan.service     # restart so an upgrade really runs the new code
sleep 2
systemctl --no-pager --lines=3 status jellyfin-autoscan.service | head -6
echo "Done. Only the affected library is scanned, about a minute after the last change. Log: journalctl -t jellyfin-autoscan"

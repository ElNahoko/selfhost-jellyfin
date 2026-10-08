#!/usr/bin/env bash
# Phase 1 (run as root on a fresh Ubuntu 24.04 server).
# Creates an admin user with your SSH public key, enables the firewall, updates, auto-updates, fail2ban.
# It does NOT disable password login: do that with 02-lock-ssh.sh only AFTER you have tested key login.
#
#   sudo ADMIN_USER=media PUBKEY_FILE=/root/mykey.pub bash 01-bootstrap.sh
set -euo pipefail

ADMIN_USER="${ADMIN_USER:-media}"
PUBKEY_FILE="${PUBKEY_FILE:-}"

[[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
[[ -n "$PUBKEY_FILE" && -f "$PUBKEY_FILE" ]] || { echo "Set PUBKEY_FILE to a public key file (.pub)."; exit 1; }
grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-)' "$PUBKEY_FILE" || { echo "$PUBKEY_FILE does not look like a public key."; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y full-upgrade
apt-get -y install ufw unattended-upgrades fail2ban curl ca-certificates gnupg

# Admin user (no password; key only). Passwordless sudo is the usual trade-off for key-only accounts.
id "$ADMIN_USER" &>/dev/null || adduser --disabled-password --gecos "" "$ADMIN_USER"
usermod -aG sudo "$ADMIN_USER"
echo "$ADMIN_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$ADMIN_USER"
chmod 440 "/etc/sudoers.d/90-$ADMIN_USER"
visudo -cf "/etc/sudoers.d/90-$ADMIN_USER" >/dev/null

install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
AK="/home/$ADMIN_USER/.ssh/authorized_keys"
touch "$AK"
while IFS= read -r line; do
  [[ -n "$line" ]] && ! grep -qxF "$line" "$AK" && echo "$line" >> "$AK"
done < "$PUBKEY_FILE"
chown "$ADMIN_USER:$ADMIN_USER" "$AK"
chmod 600 "$AK"

# Firewall: only SSH + web. (Docker-published ports bypass UFW, so publish only what you mean to.)
ufw default deny incoming
ufw default allow outgoing
ufw limit 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

# Automatic security updates
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

timedatectl set-ntp true
systemctl enable --now fail2ban

echo
echo "Done. NEXT: open a NEW terminal and check that this works before going further:"
echo "    ssh $ADMIN_USER@<server-ip>"
echo "Then run 02-lock-ssh.sh. Keep this root session open until you have confirmed it."

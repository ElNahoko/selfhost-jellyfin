#!/usr/bin/env bash
# Phase 2: disable root + password SSH login. Run ONLY after you logged in as the admin user with your key.
#
#   sudo ADMIN_USER=media bash 02-lock-ssh.sh
set -euo pipefail

ADMIN_USER="${ADMIN_USER:-media}"
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
AK="/home/$ADMIN_USER/.ssh/authorized_keys"
[[ -s "$AK" ]] || { echo "No keys in $AK: refusing to lock SSH."; exit 1; }

# "00-" so it is read before cloud-image drop-ins (first value wins in sshd).
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
X11Forwarding no
AllowUsers $ADMIN_USER
EOF

sshd -t
systemctl reload ssh

echo "Effective settings:"
sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowusers) '
echo
echo "Keep this session open. In ANOTHER terminal confirm:  ssh $ADMIN_USER@<server-ip>"
echo "If it fails, fix it from this session (or the provider's recovery console) by removing /etc/ssh/sshd_config.d/00-hardening.conf and running: systemctl reload ssh"

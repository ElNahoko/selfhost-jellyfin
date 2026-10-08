#!/usr/bin/env bash
# Installs Docker Engine + Compose plugin from Docker's official apt repository (Ubuntu), with log rotation.
#   sudo bash 03-install-docker.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Global log rotation (don't overwrite an existing config)
if [[ -e /etc/docker/daemon.json ]]; then
  echo "/etc/docker/daemon.json already exists: add log rotation manually (max-size 10m, max-file 3)."
else
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
  systemctl restart docker
fi

systemctl enable --now docker
docker --version
docker compose version
docker run --rm hello-world | head -n 3
echo
echo "Note: users are NOT added to the 'docker' group (that equals root). Use 'sudo docker ...'."

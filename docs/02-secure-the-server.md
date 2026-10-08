# 2. Secure the server

Goal: key-only SSH, a non-root admin user, a firewall that allows only SSH and web, and automatic security updates, **without locking yourself out**.

## 0. Before you start
- Find the provider's **recovery/VNC console** and confirm you can open it. It is your safety net.
- Generate an SSH key on your own computer (if you do not have one):
  ```bash
  ssh-keygen -t ed25519 -C "jellyfin-server"
  ```
  The **public** key is the `.pub` file. Never share the private key.

## 1. Check the machine
```bash
lsb_release -d          # Ubuntu 24.04 LTS
nproc; free -h
lsblk -f                # disks, filesystems, mount points
ip -4 addr              # public IP
```
Write down which disk is the system disk and which is the data disk. **Do not format anything yet.**

## 2. Bootstrap (as root)
Copy your public key to the server (for example `scp mykey.pub root@SERVER:/root/`) and the `scripts/` folder, then:

```bash
sudo ADMIN_USER=media PUBKEY_FILE=/root/mykey.pub bash scripts/01-bootstrap.sh
```

This creates the admin user, installs your key, runs a full upgrade, enables UFW (22 rate-limited, 80, 443/tcp+udp), unattended security upgrades, fail2ban and time sync.

## 3. Prove key login works
**Open a second terminal** and run:
```bash
ssh media@SERVER_IP
sudo -n true && echo "sudo works"
```
Only continue if this works.

## 4. Lock SSH
```bash
sudo ADMIN_USER=media bash scripts/02-lock-ssh.sh
```
Then open a third terminal and confirm you can still log in. Root and password logins are now refused.

If something breaks, use the provider console and delete `/etc/ssh/sshd_config.d/00-hardening.conf`, then `systemctl reload ssh`.

## 5. Notes
- Docker-published ports **bypass UFW**. This stack publishes only 80/443 (Caddy). Never add `ports: "8096:8096"` for Jellyfin.
- Optional extra: keep SSH off the public internet using a private mesh VPN and only allow SSH from it. Do this only after the basic setup works.
- Check updates are active: `systemctl status unattended-upgrades` and `cat /var/log/unattended-upgrades/unattended-upgrades.log`.
- Reboots: security updates sometimes need one. `ls /var/run/reboot-required` tells you.

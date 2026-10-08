# Jellyfin on a VPS: a family media server guide

A practical, provider-neutral recipe for running your own **Jellyfin** server on a rented Linux VPS with
HTTPS, a locked-down firewall, and a setup that works on **LG webOS TVs, phones and browsers**.

Designed for a small budget: a low-power VPS with a large (about 2 TB) disk, serving 3–4 family members
mostly with **Direct Play** (no heavy transcoding).

> **Legal:** only host media you have the right to share. Check that your hosting provider's terms allow
> personal media streaming and that your plan's CPU, disk I/O and traffic limits fit your use.
> This repo is not affiliated with Jellyfin, Docker, Caddy or any hosting provider. No warranty.

## What you get

```
Internet ──443──▶ Caddy (HTTPS, auto certificates) ──▶ Jellyfin (container, port 8096 never public)
                                                           │
                                  /opt/jellyfin/{config,cache}   /srv/media (read-only mount)
```

- Ubuntu 24.04 LTS, Docker Engine + Compose plugin (official repo)
- Jellyfin pinned to a stable version, restart policy, health check, memory limit, log rotation
- Caddy reverse proxy with automatic Let's Encrypt certificates
- SSH keys only, no root login, UFW firewall, fail2ban, unattended security updates
- Scripts for hardening, Docker install, safe disk mounting, backups, disk alerts, verification and a bounded stream test

## Local install wizard

Prefer clicking to reading? Open [`wizard/index.html`](wizard/index.html) in any browser (double-click it, no server or internet needed).
Enter your IP, hostname and SSH **public** key once; it builds every command for you with copy buttons, tracks your progress
step by step (saved in your browser only), and can read the output of `verify.sh`. It never asks for passwords and sends nothing anywhere.

## Steps

| # | Guide | What |
|---|---|---|
| 1 | [Choosing a VPS](docs/01-choosing-a-vps.md) | What to check before you pay, and capacity math |
| 2 | [Secure the server](docs/02-secure-the-server.md) | Admin user, SSH keys, firewall, updates |
| 3 | [Storage](docs/03-storage.md) | Identify, mount and lay out the big disk without losing data |
| 4 | [Docker and Jellyfin](docs/04-docker-and-jellyfin.md) | Install, configure, first-run wizard |
| 5 | [HTTPS and DNS](docs/05-https-and-dns.md) | Hostname, Caddy, proxy settings |
| 6 | [Configure Jellyfin](docs/06-configure-jellyfin.md) | Libraries, users, Direct Play, client compatibility |
| 7 | [Backups and maintenance](docs/07-backups-and-maintenance.md) | Backup, updates, monitoring, recovery |
| 8 | [Add media and test](docs/08-first-media-and-testing.md) | Legal sample media, playback checks, 4-stream test |
| 9 | [Connect devices](docs/09-clients.md) | LG TV, phones, browsers |
| 10 | [Troubleshooting](docs/10-troubleshooting.md) | Common problems |

Quick path once you have a fresh Ubuntu 24.04 server and a hostname pointing to it:

```bash
git clone https://github.com/ElNahoko/jellyfin-vps-guide.git && cd jellyfin-vps-guide
sudo ADMIN_USER=media PUBKEY_FILE=~/mykey.pub bash scripts/01-bootstrap.sh   # then test SSH login as that user
sudo ADMIN_USER=media bash scripts/02-lock-ssh.sh
sudo bash scripts/03-install-docker.sh
lsblk -f   # identify your data disk, then: sudo bash scripts/04-mount-media.sh /dev/<disk>
# copy deploy/ to /opt/jellyfin, edit .env, then follow docs/04 and docs/05
```

Read the docs before running scripts. Each one explains what it changes.

## Progress checklist

- [ ] VPS ordered, Ubuntu 24.04 installed, recovery console tested
- [ ] Admin user + SSH key login verified, then password/root login disabled
- [ ] Firewall on (22, 80, 443 only)
- [ ] Data disk mounted at `/srv/media` (nothing erased by accident)
- [ ] Docker + Compose installed
- [ ] Jellyfin first-run wizard done through an SSH tunnel
- [ ] DNS name points to the server, HTTPS certificate issued
- [ ] Libraries created, family accounts made, transcoding restricted
- [ ] Legal sample media plays through the public HTTPS URL, survives a restart
- [ ] Backup job + disk alert + uptime monitor in place
- [ ] LG TV, phone and browser tested

## License

MIT, see [LICENSE](LICENSE).

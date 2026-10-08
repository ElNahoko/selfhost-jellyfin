# 4. Docker and Jellyfin

## Install Docker (official repository)
```bash
sudo bash scripts/03-install-docker.sh
```
Installs Docker Engine and the Compose plugin, enables log rotation (10 MB × 3 files per container), and runs `hello-world` as a smoke test. If `hello-world` fails, your VPS type may not support Docker: stop and check the virtualization type (`systemd-detect-virt` should say `kvm` or similar, not `openvz`/`lxc`).

## Deploy files
```bash
sudo mkdir -p /opt/jellyfin/{config,cache,backups,scripts}
sudo cp deploy/compose.yaml deploy/compose.setup.yaml deploy/Caddyfile /opt/jellyfin/
sudo cp deploy/.env.example /opt/jellyfin/.env
sudo cp scripts/*.sh /opt/jellyfin/scripts/
id media            # note uid/gid
sudo chown -R media:media /opt/jellyfin
sudo nano /opt/jellyfin/.env   # set versions, domain, PUID/PGID, TZ
```
Pin real versions in `.env`: check [Jellyfin releases](https://github.com/jellyfin/jellyfin/releases) and the Caddy image tags. Avoid `latest`.

## First run: do the setup wizard privately
Until an admin account exists, anyone who can reach the server could claim it. So do the wizard **before** exposing Jellyfin:

```bash
cd /opt/jellyfin
sudo docker compose -f compose.yaml -f compose.setup.yaml up -d jellyfin
```
This publishes Jellyfin only on the server's loopback. From **your own computer**:
```bash
ssh -L 8096:localhost:8096 media@SERVER_IP
```
Open `http://localhost:8096`, then in the wizard:
1. Create the **administrator** account with a long unique password (password manager). Use it only to administer.
2. Skip adding libraries for now (you will add them in [Configure Jellyfin](06-configure-jellyfin.md)), or add them now with the paths below.
3. Metadata language: your choice. **Allow remote connections**: yes (it is only reachable through Caddy).

Library paths inside the container are under `/media`: `/media/movies`, `/media/shows`, `/media/music`, `/media/audiobooks`.

## Go live
Stop the setup instance and start the real stack (after DNS is ready, see [HTTPS and DNS](05-https-and-dns.md)):
```bash
sudo docker compose down
sudo docker compose up -d
sudo docker compose ps          # jellyfin should become "healthy"
sudo docker compose logs -f caddy   # watch the certificate being issued
```

## Everyday commands
```bash
cd /opt/jellyfin
sudo docker compose ps
sudo docker compose logs --tail 100 jellyfin
sudo docker compose restart jellyfin
sudo docker stats --no-stream
```

## Update procedure
1. Run a backup: `sudo /opt/jellyfin/scripts/backup.sh`
2. Read the release notes for the new version.
3. Edit `JELLYFIN_VERSION` in `.env`.
4. `sudo docker compose pull && sudo docker compose up -d`
5. Check `docker compose ps` and play something.

Rollback: restore the backup tarball and set the previous version in `.env`. Newer versions may migrate the database, so the backup matters.

## Resource limits
`compose.yaml` caps Jellyfin at 1280 MB of RAM. On a 2 GB server leave room for the OS and Caddy. Raise it if you have more RAM. Library scans on a large collection are the heaviest regular job; schedule them for the night (Dashboard → Scheduled Tasks).

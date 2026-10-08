# 7. Backups and maintenance

## What to protect
| Item | Loss impact | Strategy |
|---|---|---|
| `/opt/jellyfin/config` (database, users, watched state) | Rebuildable but annoying | Weekly backup, **copied off the server** |
| `.env`, `compose.yaml`, `Caddyfile` | Easy to recreate | Included in the backup tarball and in this repo's templates |
| Media in `/srv/media` | Large | Keep originals at home/elsewhere. The VPS disk is **not** a backup |
| TLS certificates (`caddy_data`) | Re-issued automatically | No backup needed |

## Backup job
`scripts/backup.sh` stops Jellyfin for a few seconds, archives config and compose files, restarts it, and keeps the newest 7 archives.

```bash
sudo /opt/jellyfin/scripts/backup.sh
```
Weekly at 04:00 on Sunday, plus an off-server copy (adjust `REMOTE`):
```bash
echo '0 4 * * 0 root REMOTE=user@home-pc:/backups/jellyfin/ /opt/jellyfin/scripts/backup.sh >> /var/log/jellyfin-backup.log 2>&1' | sudo tee /etc/cron.d/jellyfin-backup
```
Alternatively **pull** backups from your home computer so the server holds no credentials for it:
```bash
rsync -avP media@SERVER_IP:/opt/jellyfin/backups/ ./jellyfin-backups/
```

## Restore
```bash
cd /opt/jellyfin
sudo docker compose down
sudo mv config config.broken
sudo tar -xzf backups/jellyfin-config-YYYY-MM-DD_HHMM.tar.gz -C /opt/jellyfin config
sudo chown -R media:media config
sudo docker compose up -d
```
New server? Do [steps 2–5](02-secure-the-server.md), copy the tarball over, restore as above, upload media again, repoint DNS.

## Disaster consequences
- **VPS failure / provider problem:** Jellyfin config is restorable from the backup in minutes; media must be re-uploaded (so keep originals). Plan the time that takes at your upload speed.
- **Media disk loss:** assume it can happen. Keep a second copy of anything irreplaceable.
- **Accidental deletion by a family user:** users cannot delete media if you disable "Allow media deletion".

## Monitoring (free)
- **Uptime:** a free external HTTP monitor on `https://your-host/health` that emails or pushes you on failure.
- **Disk:** `scripts/disk-alert.sh` from cron:
  ```bash
  echo '0 * * * * root NTFY_URL=https://ntfy.sh/CHANGE-TO-A-LONG-RANDOM-TOPIC THRESHOLD=85 /opt/jellyfin/scripts/disk-alert.sh' | sudo tee /etc/cron.d/disk-alert
  ```
  (Anyone who knows the topic name can read it: use a long random one and put no secrets in the message.)
- **Containers:** `restart: unless-stopped` + health check. Look at `docker compose ps` weekly.

## Routine
| When | Task |
|---|---|
| Weekly | Backup runs; glance at `docker compose ps`, `df -h` |
| Monthly | `sudo apt update && apt list --upgradable`, reboot if `/var/run/reboot-required` exists; check Jellyfin releases |
| On new Jellyfin version | Backup, bump `JELLYFIN_VERSION`, `pull`, `up -d`, test playback |
| Before the subscription renews | Decide to keep, change plan or cancel. Download what you need first. Cancel in the provider's panel, not by ignoring it |
| Yearly | Rotate the admin password; remove unused users and API keys |

## Cancelling
Copy off your config backup and any media you have no other copy of, **then** cancel in the billing portal. Cancellation usually destroys all data at once.

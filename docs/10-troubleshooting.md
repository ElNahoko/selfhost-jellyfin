# 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Caddy can't get a certificate | DNS doesn't point at the server, or port 80 blocked | `dig +short host`, `sudo ufw status`, `docker compose logs caddy` |
| Browser shows 502 | Jellyfin not healthy yet or crashed | `docker compose ps`, `docker compose logs jellyfin` |
| Jellyfin keeps restarting, "permission denied" in logs | `PUID/PGID` don't own `config/` or `cache/` | `sudo chown -R media:media /opt/jellyfin` and match `.env` |
| Uploaded a file but it is not in Jellyfin | Jellyfin's own real-time watcher does not start for a library folder that was **empty** when Jellyfin started or last scanned (typical for a new TV Shows or Music library). Nothing scans until the 12-hourly task | Install the auto-scan service: `scripts/07-enable-autoscan.sh` (new files are then scanned, only the affected library, about a minute after the last change). Quick manual fix: Dashboard → Libraries → Scan All Libraries. Check with `journalctl -t jellyfin-autoscan` |
| No subtitle (CC) button, although the release came with `.srt` files | Jellyfin only reads external subtitles that sit **next to the video** and start with its file name. Many releases put them in `Subs/<video name>/2_English.srt` | The auto-scan service (`scripts/07-enable-autoscan.sh`) copies them next to the video as `<video name>.eng.srt`, `.fra.srt`, ... (originals stay) before each scan. Several files for one language become `.eng.srt`, `.eng.2.srt`. Jellyfin has no built-in subtitle translation |
| I deleted a movie but Jellyfin still lists it | Jellyfin does not remove entries when a library folder is completely empty (it assumes the disk is missing) | The auto-scan service now removes entries whose files are gone (log: `removed from library (file is gone)`). Delete files on the upload page, wait about a minute, reload. By hand: open the title in Jellyfin → ⋮ → Delete (admin) |
| Library empty | Wrong path (use `/media/...`), or files unreadable | Check `ls -l /srv/media`, ownership/permissions; rescan |
| Media vanished after reboot | Data disk not mounted | `findmnt /srv/media`, `lsblk -f`, check `/etc/fstab` UUID; services started before the mount, so `docker compose restart` |
| The whole web interface is slow while someone watches a film | One transcode (re-encoding HEVC/10-bit for a browser that cannot decode it, or burning in image subtitles) uses the only CPU core and starves Jellyfin itself. Check Dashboard: *Transcoding* | Use a client that direct-plays HEVC (TV app, phone app, Edge/Safari with HEVC), pick text subtitles instead of image ones (PGS). Optional: `compose.perf.yaml` runs ffmpeg at the lowest CPU priority (`nice 19`) so the interface stays responsive |
| Everything transcodes / buffers | Codec/container/subtitle mismatch | See [Configure Jellyfin](06-configure-jellyfin.md); look at the playback reason in the player info |
| High CPU during playback | Transcoding is active | Disable video transcoding for the user, fix the file or client |
| Slow library scans | Spinning disk + many small files | Schedule nightly; disable chapter image extraction; keep config on SSD |
| Locked out of SSH | Key not installed, wrong user | Use the provider's recovery console; remove `/etc/ssh/sshd_config.d/00-hardening.conf` and `systemctl reload ssh` |
| `docker: permission denied` | Admin isn't in the docker group (by design) | Use `sudo docker ...` |
| Can't reach the server from some countries | ISP/route issue, not necessarily your server | Test from mobile data vs. Wi-Fi; `mtr` from the affected side; try another DNS resolver |
| Traffic limit hit | Heavy 4K or many viewers | Lower per-user bitrate limits; check the provider's panel traffic graph |

Useful diagnostics:
```bash
sudo docker compose ps && sudo docker stats --no-stream
sudo journalctl -u docker --since "1 hour ago" | tail -50
sudo ss -tlnp               # what is listening (8096 must not appear on 0.0.0.0)
sudo ufw status verbose
df -h; free -h; uptime
```

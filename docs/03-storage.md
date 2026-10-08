# 3. Storage

## Layout

| Path | Lives on | Contents |
|---|---|---|
| `/opt/jellyfin/config` | system SSD/NVMe | Jellyfin database, users, metadata. **Back this up.** |
| `/opt/jellyfin/cache` | system SSD/NVMe | Image cache. Can be rebuilt. |
| `/opt/jellyfin/backups` | system disk (copy off-server!) | Config backups |
| `/srv/media/{movies,shows,music,audiobooks}` | big data disk | Your media |

Keep the database off slow HDD storage. Keep metadata/artwork on the system disk (default behaviour).

## Identify the data disk (read-only commands)
```bash
lsblk -f
sudo blkid
df -h
```
- A disk with an empty `FSTYPE` and no mountpoint is **unformatted**. Often `/dev/vdb` or `/dev/sdb`.
- A disk with a filesystem and data must **not** be formatted.

## Mount it
```bash
# existing filesystem: just mounts it
sudo bash scripts/04-mount-media.sh /dev/vdb

# EMPTY disk only: creates ext4, asks you to type the device name to confirm
sudo bash scripts/04-mount-media.sh /dev/vdb --format
```
The script writes a UUID-based `/etc/fstab` entry with `nofail`, so a missing disk does not stop the server booting.

Then give your admin user ownership (the Jellyfin container runs as the same UID):
```bash
sudo chown -R media:media /srv/media
```

## Performance sanity check (short and gentle)
```bash
# sequential read of a big file, bypassing cache (use after you have uploaded something)
sudo dd if=/srv/media/movies/yourfile.mkv of=/dev/null bs=4M count=250 iflag=direct status=progress
```
One 1080p stream needs ~1–2 MB/s. Even a slow HDD volume handles four streams; random-IO heavy tasks (library scans of thousands of files) are what feel slow.

## Uploading media
- `rsync -avP --partial /local/movies/ media@SERVER:/srv/media/movies/` (resumable)
- SFTP clients work with the admin account (key login).
- Keep naming clean: `Movies/Title (Year)/Title (Year).mkv`, `Shows/Show Name (Year)/Season 01/...`. See Jellyfin's naming docs.

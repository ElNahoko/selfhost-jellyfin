#!/usr/bin/env bash
# Mounts a data disk at /srv/media safely. It NEVER formats anything unless you pass --format
# AND type the device name back to confirm.
#
#   lsblk -f                                   # identify the disk first
#   sudo bash 04-mount-media.sh /dev/vdb        # mount an existing filesystem
#   sudo bash 04-mount-media.sh /dev/vdb --format   # EMPTY disks only: creates ext4 (destroys data!)
set -euo pipefail

DEV="${1:-}"; FORMAT="${2:-}"
MNT=/srv/media
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ -b "$DEV" ]] || { echo "Usage: $0 /dev/<device> [--format]   (see: lsblk -f)"; exit 1; }
findmnt -rn -S "$DEV" >/dev/null && { echo "$DEV is already mounted:"; findmnt -S "$DEV"; exit 1; }

echo "Current state of $DEV:"; lsblk -f "$DEV"

FSTYPE="$(blkid -o value -s TYPE "$DEV" || true)"
if [[ -z "$FSTYPE" ]]; then
  [[ "$FORMAT" == "--format" ]] || { echo "No filesystem found. Re-run with --format if the disk is really empty."; exit 1; }
  read -r -p "This will ERASE $DEV. Type the device path to confirm: " ANSWER
  [[ "$ANSWER" == "$DEV" ]] || { echo "Aborted."; exit 1; }
  mkfs.ext4 -m 0 -L media "$DEV"      # -m 0: no reserved blocks on a pure data disk
else
  echo "Existing filesystem: $FSTYPE (left untouched)."
  [[ "$FORMAT" == "--format" ]] && { echo "Refusing --format: the disk already has a filesystem."; exit 1; }
fi

UUID="$(blkid -o value -s UUID "$DEV")"
mkdir -p "$MNT"
grep -q "$UUID" /etc/fstab || echo "UUID=$UUID $MNT ext4 defaults,noatime,nofail 0 2" >> /etc/fstab
mount "$MNT"
mkdir -p "$MNT"/{movies,shows,music,audiobooks,books,manga}
df -h "$MNT"
echo "Mounted. Set ownership for your admin user, e.g.: sudo chown -R media:media $MNT"

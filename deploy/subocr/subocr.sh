#!/bin/sh
# Nightly (root cron): turn picture subtitles into .srt files, then ask Jellyfin to pick them up.
# 0 2 * * * /opt/jellyfin/subocr/subocr.sh >> /var/log/subocr.log 2>&1
docker ps --format '{{.Names}}' | grep -qx subocr && exit 0          # a pass is already running
docker run --rm --name subocr --user 1000:1000 --cpus 0.8 -v /srv/media:/media lumio-subocr
docker exec uploads-meta python -c "import sys; sys.path.insert(0, '/app'); import meta; meta.jf('/Library/Refresh', data={}, method='POST')" && echo "jellyfin rescan requested"

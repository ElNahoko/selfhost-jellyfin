#!/bin/sh
# Converts picture subtitles into .srt files, then asks Jellyfin to pick them up.
#   subocr.sh        nightly (root cron 02:00): every video
#   subocr.sh queue  what the admin panel asked for (started by watch.sh)
MODE="${1:-all}"
docker ps --format '{{.Names}}' | grep -qx subocr && exit 0          # a pass is already running (it reads the panel's queue too)
docker run --rm --name subocr --user 1000:1000 --cpus 0.8 -e MODE="$MODE"   -v /srv/media:/media -v /opt/jellyfin/uploads/data:/status lumio-subocr
[ $? -eq 0 ] && docker exec uploads-meta python -c "import sys; sys.path.insert(0, '/app'); import meta; meta.jf('/Library/Refresh', data={}, method='POST')" && echo "jellyfin rescan requested"
exit 0

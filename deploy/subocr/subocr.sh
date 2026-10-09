#!/bin/sh
# Converts picture subtitles into .srt files, then asks Jellyfin to pick them up.
# Hard memory cap: OCR of a feature film can need ~900 MB; on a 2 GB server an uncapped run froze everything (Oct 2026).
#   subocr.sh        every video (root cron, nightly 02:00)
#   subocr.sh queue  what the admin panel asked for (started by watch.sh)
MODE="${1:-all}"
docker ps --format '{{.Names}}' | grep -qx subocr && exit 0          # a pass is already running (it reads the panel's queue too)
docker run --rm --name subocr --user 1000:1000 --cpus 0.8 --memory 600m --memory-swap 600m -e MODE="$MODE"   -v /srv/media:/media -v /opt/jellyfin/uploads/data:/status lumio-subocr
[ $? -eq 0 ] && docker exec uploads-meta python -c "import sys; sys.path.insert(0, '/app'); import meta; meta.jf('/Library/Refresh', data={}, method='POST')" && echo "jellyfin rescan requested"
exit 0

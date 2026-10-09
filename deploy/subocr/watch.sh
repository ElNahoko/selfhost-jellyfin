#!/bin/sh
# Root cron, every minute: start a conversion when the admin panel asked for one (it leaves subocr-queue.json in the data dir).
[ -f /opt/jellyfin/uploads/data/subocr-queue.json ] || exit 0
exec /opt/jellyfin/subocr/subocr.sh queue

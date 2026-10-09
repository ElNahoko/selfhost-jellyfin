#!/bin/sh
# One pass over the library: every video with picture subtitles gets "Name.en.srt" / "Name.fr.srt" (or .en.sdh.srt) (skipped when already there).
# Runs at the lowest CPU priority, so playback always wins. Started nightly by cron (see docs/12-subtitles.md).
set -u
LANGS="${LANGS:-en fr}"
args=""; for l in $LANGS; do args="$args -l $l"; done
find /media/movies /media/shows -type f -name "*.mkv" | sort | while IFS= read -r f; do
  b="${f%.*}"; need=0
  for l in $LANGS; do ls "$b.$l".*srt "$b.$l.srt" >/dev/null 2>&1 || need=1; done
  [ "$need" = 1 ] || continue
  echo "$(date +%H:%M) $f"
  nice -n 19 pgsrip rip $args --one-per-language --engine tesseract --workers 1 "$f" 2>&1 | tail -2
done
echo "$(date +%H:%M) pass done"

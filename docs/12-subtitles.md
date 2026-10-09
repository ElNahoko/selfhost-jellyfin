# Subtitles without re-encoding

Blu-ray rips carry **picture subtitles** (PGS). TVs, phones and the browser cannot draw them, so when one is turned on Jellyfin burns it into the picture, which means re-encoding the whole video. On this 1-vCPU server that is 100 % CPU and stuttering playback.

**Fix:** `deploy/subocr` reads those subtitles once with OCR (tesseract, through [pgsrip](https://github.com/ratoaq2/pgsrip)) and writes text subtitles next to each video (`Name.en.srt`, `Name.en.sdh.srt`, `Name.fr.srt`). Players draw text subtitles themselves, so the video plays directly. About 30 seconds per episode.

- Image `lumio-subocr` is built on the server from `deploy/subocr/Dockerfile`.
- `/opt/jellyfin/subocr/subocr.sh` runs one pass (lowest CPU priority, capped at 0.8 CPU, skips videos that already have their .srt) and then asks Jellyfin to rescan. Root cron, nightly at 02:00; log in `/var/log/subocr.log`.
- Languages: `LANGS="en fr"` in `run.sh`.
- On the TV, pick the subtitle track marked **SRT** (external), not **PGSSUB**.

Jellyfin encoding is also set to `veryfast`, with throttling and segment deletion on, so a transcode that does happen costs less.

## From the admin panel

Settings → **Subtitles** shows every video with picture subtitles grouped by film or show: how many are converted, what is left, what failed (hover for the error), and the file being converted right now. **Convert all** or **Convert** on one show queues the work: the panel writes `data/subocr-queue.json`, and `/opt/jellyfin/subocr/watch.sh` (root cron, every minute) starts a pass for it. A pass that is already running picks the request up between two files. Status: `data/subocr.json`.

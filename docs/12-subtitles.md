# Subtitles without re-encoding

Blu-ray rips carry **picture subtitles** (PGS). TVs, phones and the browser cannot draw them, so when one is turned on Jellyfin burns it into the picture, which means re-encoding the whole video. On this 1-vCPU server that is 100 % CPU and stuttering playback.

**Fix:** `deploy/subocr` reads those subtitles once with OCR (tesseract, through [pgsrip](https://github.com/ratoaq2/pgsrip)) and writes text subtitles next to each video (`Name.en.srt`, `Name.en.sdh.srt`, `Name.fr.srt`). Players draw text subtitles themselves, so the video plays directly. About 30 seconds per episode.

- Image `lumio-subocr` is built on the server from `deploy/subocr/Dockerfile`.
- `/opt/jellyfin/subocr/subocr.sh` runs one pass (lowest CPU priority, capped at 0.8 CPU, skips videos that already have their .srt) and then asks Jellyfin to rescan. Root cron, nightly at 02:00; log in `/var/log/subocr.log`.
- Languages: `LANGS="en fr"` in `run.sh`.
- On the TV, pick the subtitle track marked **SRT** (external), not **PGSSUB**.

Jellyfin encoding is also set to `veryfast`, with throttling and segment deletion on, so a transcode that does happen costs less.

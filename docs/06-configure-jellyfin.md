# 6. Configure Jellyfin

## Libraries
Dashboard → Libraries → Add Media Library.

| Name | Content type | Folder (inside container) |
|---|---|---|
| Movies | Movies | `/media/movies` |
| TV Shows | Shows | `/media/shows` |
| Music | Music | `/media/music` |
| Audiobooks | see note | `/media/audiobooks` |
| Books | Books | `/media/books` |
| Manga | Books | `/media/manga` |

**Audiobooks note:** Jellyfin has no first-class audiobook library. Common approaches: a **Music** library (one folder per author, one subfolder per book) or the **Books** type. Client support differs, so test one item on every device you care about before loading a large collection.

Library settings worth setting:
- Metadata downloaders: keep the defaults (TheMovieDb / TheTVDB / MusicBrainz) and set the preferred language.
- **Do not** enable "Save artwork into media folders" (the media mount is read-only by design).
- Real-time monitoring: fine on local disks. For large libraries schedule scans nightly instead.
- Trickplay / chapter image extraction are CPU and disk heavy. On a small server, disable "Extract chapter images" and be selective with trickplay generation.

## Books and manga
Jellyfin 12 reads books itself. Use the **Books** library type for both a *Books* and a *Manga* library (separate folders keep them tidy).
- **EPUB** opens in the web reader. **PDF, CBZ and CBR** are listed with covers, but whether they open in the web reader or only download depends on the client version: test one file on each device you use. Dedicated Jellyfin reader apps exist for phones and tablets if you read a lot.
- Layout: `Books/Author/Title.epub`, `Manga/Series/Series - Volume 01.cbz`.
- Free, legal starters: public-domain classics from [Project Gutenberg](https://www.gutenberg.org) (EPUB), and old public-domain art books on the Internet Archive.

## Users
- **Administrator**: separate account; do not use it for watching.
- **One account per person** (up to four): Dashboard → Users → Add. Set the initial passwords **privately**, then let each person change theirs. Do not post them in chat or repos.
- For each normal user, under *Access*: no administrator rights, only the libraries they need, disable "Allow remote control of other users".
- Under *Playback* (per user):
  - **Allow video playback that requires transcoding**: **off** (protects the CPU, see below).
  - **Allow audio playback that requires transcoding**: on (cheap).
  - **Internet streaming bitrate limit**: e.g. 12–15 Mbit/s so one user cannot saturate the link.
  - **Maximum active sessions**: 2 per user.

## Transcoding policy
Dashboard → Playback → Transcoding: no hardware acceleration on a plain VPS. Set a **low** "Transcode throttle" and limit the **number of simultaneous transcodes** (for a 1–2 vCPU server, 1). With video transcoding disabled per user, clients that cannot Direct Play simply get an error: that is intentional. Fix the *file* or the *client*, not the server.

## Make Direct Play work

### What the clients can play

| Client | Reliable | Problems |
|---|---|---|
| **LG webOS TV (Jellyfin app)** | H.264, HEVC (H.265), AAC, AC3, EAC3, MP4/MKV, SRT/text subtitles | DTS / TrueHD audio often unsupported; PGS/image subtitles force burn-in (video transcode); some 10-bit/HDR combinations vary by model |
| **Chrome / Edge on Windows** | H.264, AAC, MP3, MP4, WebM | AC3/EAC3/DTS usually not decoded in the browser (audio transcode); HEVC only with hardware support; MKV works but with caveats |
| **Android app / iPhone app** | H.264, HEVC (device dependent), AAC | Safari/iOS dislikes some MKV audio/subtitle combos; PGS subtitles burn in |

### Library preparation rules (cheapest solution)
1. Prefer **H.264 or HEVC video + AAC/AC3/EAC3 audio** in MP4 or MKV.
2. Prefer **text subtitles (SRT)** over image subtitles (PGS/VobSub).
3. Avoid DTS/TrueHD as the only audio track. Add or convert to EAC3/AAC.
4. Keep 1080p as the default. Keep 4K for local viewing unless the viewer has a fast line.

Convert only the audio, leaving the video untouched (fast, on **your own computer**, not the server):
```bash
ffmpeg -i input.mkv -map 0 -c copy -c:a eac3 -b:a 640k output.mkv
```
Extract text subtitles instead of burning images:
```bash
ffmpeg -i input.mkv -map 0:s:0 subs.srt      # works for text subtitle tracks only
```

### Client-side settings (instead of server transcoding)
- In each client, set the playback quality to **Auto/Maximum** and pick **Direct Play** compatible audio/subtitle tracks.
- On TVs, choose SRT subtitles or turn subtitles off to avoid burn-in.
- If a title always transcodes, re-encode that file once offline.

## Check how a stream is delivered
During playback: Dashboard → **Activity** / **Dashboard home** shows *Direct Play*, *Direct Stream* (remux, cheap) or *Transcoding* (expensive). For one title, the player's *Playback Info* overlay shows the reason (container, codec, subtitle, bitrate).

## Plugins worth installing (official catalogue only)
Dashboard → Plugins → Catalogue, then restart Jellyfin once. Plugins run code inside your server, so stick to the official catalogue and skip anything that needs a third-party repository unless you have read its source.

| Plugin | What it adds | Needs an account? |
|---|---|---|
| **Fanart** | More posters, backdrops and title logos | No |
| **TMDb Box Sets** | Movie franchises become collections automatically | No (uses the TMDb plugin) |
| **Subtitle Extract** | Extracts embedded subtitles ahead of time, so switching subtitles is instant on a small CPU | No |
| **Chapter Segments Provider** | Turns named chapters (Intro, Credits) into skip buttons | No |
| **Playback Reporting** | Who watched what, per user | No |
| **Open Subtitles** | Downloads subtitles for files that have none | Yes, your own account |
| **Trakt / Simkl** | Syncs your history to a third-party site, nothing new inside Jellyfin | Yes |

Home-screen plugins that replace the whole home page (Home Screen Sections and similar) come from third-party repositories and replace the layout, so they clash with a custom theme. This guide's theme adds the genre and recommendation rows itself instead.

# 8. Add media and test

The setup is not done until a real file plays through the public HTTPS URL.

## Legal sample media
Use only material with a clear licence. Verify the licence on the official page before downloading.

| Type | Where | Licence note |
|---|---|---|
| Audiobook | LibriVox (public-domain audiobooks) | Public domain recordings. Check the book's page |
| Music | Internet Archive "Netlabels"/Creative Commons audio, Free Music Archive, Jamendo | Per-track licence. Pick CC0, CC BY or public domain |
| Video | Blender Foundation open movies (e.g. *Big Buck Bunny*) | Creative Commons Attribution. Official download page lists sizes and formats |

Download **directly onto the server**:
```bash
curl -I "https://example.org/path/file.mp3"    # check size/type first
mkdir -p /srv/media/music/Sample\ Artist/Sample\ Album
curl -L --fail -o "/srv/media/music/Sample Artist/Sample Album/01 Sample Track.mp3" "https://example.org/path/file.mp3"
```
Keep a note of the source URL and licence (for yourself, and for attribution if required).

For a tidy result: filename `01 Track Title.mp3`, folder `Artist/Album`, and tags (ID3) filled in. Missing tags are the usual cause of "wrong metadata".

## Scan and check
Dashboard → Libraries → **Scan All Libraries** (or the library's menu). Confirm the item appears with its title, artist/album and artwork.

## Playback verification checklist
Run through the **public** URL (`https://your-host`), logged in as a normal user, not the admin.
- [ ] Item plays and you hear audio
- [ ] Seeking forward/back works
- [ ] Dashboard shows *Direct Play* (not *Transcoding*)
- [ ] `sudo docker compose logs --tail 50 jellyfin` shows no errors
- [ ] `sudo docker compose restart jellyfin` → item still there, plays again
- [ ] `sudo reboot` → everything returns on its own (`docker compose ps` healthy, HTTPS OK)

In Chrome you can also open DevTools → Network and confirm the media request returns `206 Partial Content` while seeking.

## Server checks
```bash
sudo bash /opt/jellyfin/scripts/verify.sh your-host
free -h; uptime
sudo docker stats --no-stream
```

## Four-stream test (bounded)
Use a freely licensed 1080p video file. From a computer **outside** the server (ideally on a different network, such as a relative's):
1. Dashboard → API Keys → create a temporary key.
2. Get the item id from the item's URL in the web UI.
3. Run:
   ```bash
   BASE_URL=https://your-host ITEM_ID=<id> API_KEY=<key> STREAMS=4 DURATION=30 bash scripts/stream-test.sh
   ```
4. Delete the API key.

Interpretation: if each stream sustains above the file's bitrate (typically 8–15 Mbit/s) the path can carry that many Direct Play viewers. Meanwhile watch `docker stats` and `iostat`/`uptime` on the server: CPU should stay low and there should be no sustained I/O wait.

Keep tests short. Many providers have fair-use clauses against sustained heavy load.

What a test **cannot** prove: that every family member's own internet line is fast enough and that their TV decodes your files. Verify those on the real devices ([Connect devices](09-clients.md)).

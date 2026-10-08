# 11. Upload page (optional)

A small web page where you drag and drop movies, shows, music and audiobooks straight into the library folders, from any computer.

![The upload page](img/uploads.jpg)

## What it is
- [Dufs](https://github.com/sigoden/dufs), a small maintained file server, plus our own front-end page (`deploy/uploads/assets/index.html`).
- **Resumable**: files are sent in 16 MB pieces to `name.uploading` and renamed when complete. If the connection drops it retries and continues; if you close the tab, pick the same file again and it resumes.
- Folders can be dragged in; sub-folders are created for you.
- Runs in its own container with **one** account that can write to `/srv/media`. Jellyfin itself keeps a read-only view.

## Set it up
1. Give it its own hostname pointing at the server, for example `files.example.com` (same IP as Jellyfin).
2. On the server:
   ```bash
   cd ~/selfhost-jellyfin
   sudo bash scripts/05-setup-uploads.sh files.example.com
   ```
   The script prints the login **once**. Store it in a password manager.
3. Open `https://files.example.com`, sign in, choose a library and drop files.

## Good to know
- "Done" on the page means the file is **stored**. Jellyfin adds it to the library about a minute later **if the auto-scan service is installed** (`sudo JELLYFIN_API_KEY=<key> bash scripts/07-enable-autoscan.sh`, key from Dashboard → API Keys). Without it, Jellyfin's own watcher only covers library folders that already had files when it started, so uploads into a previously empty library can wait up to 12 hours: Dashboard → Libraries → Scan All Libraries fixes that by hand.
- Use clean names (`Movie Title (2020)/Movie Title (2020).mkv`), see [Add media](08-first-media-and-testing.md).
- The login uses HTTP Basic authentication over HTTPS. Use the long random password the script generates and do not share it. To rotate it, delete `/opt/jellyfin/uploads.env` and run the script again.
- Prefer the command line? `rsync -avP --partial ./movies/ media@SERVER:/srv/media/movies/` works too.
- Why not File Browser? It was a popular choice but the project is archived with no further security fixes, so this guide does not use it.

If you edit `deploy/uploads/assets/index.html`, run `sudo docker compose restart uploads`: the file server keeps the page in memory.

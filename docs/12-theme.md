# 12. Theme (optional)

Make Jellyfin look like a modern streaming app: a near-black skin with a red accent, a floating pill header, a short featured banner and a few clean rows.

## What this uses
- **Abyss**, a community CSS theme (MIT, by AumGupta, github.com/AumGupta/abyss-jellyfin), tested by its author on Jellyfin 12.1. It is **styling only** (no scripts). This repo does not copy it: the installer downloads it, pinned to an exact commit.
- `deploy/theme/custom.css`: our tuning on top of it (red accent, near-black, visible artwork on movie pages).
- The files are served from **your own server** by Caddy at `/theme/`, so your viewers' devices do not depend on a public CDN for the theme.

## Easy install
```bash
cd ~/selfhost-jellyfin
sudo bash scripts/06-enable-theme.sh
```
It downloads the pinned theme, copies our files, enables the banner helper and restarts the stack. Then paste the three `@import` lines it prints into **Dashboard → General → Custom CSS** and hard-reload (Ctrl+Shift+R).

## The featured banner (our own code)
`deploy/theme/hero.js` + `hero.css` add a full-width **featured banner** at the top of the home page: backdrop, poster, title logo, year, runtime, rating, genres, description, **Play** and **More info** buttons, arrows, dots and auto-rotation.
- It reads items through the **logged-in user's own session**, so every person only sees what their account may see.
- It makes **no requests to other sites** and is about 200 lines you can read.
- To load it, Jellyfin's own `index.html` needs one extra `<script>` line. `deploy/theme/compose.theme.yaml` does this with a one-shot helper container that copies `index.html` out of the **same Jellyfin image** and adds the line every time the stack starts, so it can never go stale after an upgrade.
- Undo: remove `compose.theme.yaml` from `COMPOSE_FILE` in `.env`, run `docker compose up -d`, and delete the Custom CSS.

## Home page layout (Netflix-style)
- **Billboard** at the top: a full-width picture of a random title with its logo, rating, a three-line description, a white **Play** button and a grey **More Info** button, the age rating on the right, and slow auto-rotation. It reads items through the logged-in user's own session.
- **Rows** of landscape thumbnails below it: *Continue Watching* (red progress bar), *Next Up* (without the titles already in Continue Watching), *Recently Added*, *Top 10* (large outlined numbers on posters), one *Because You Watched …* and the biggest genres. Hover a thumbnail on a computer and it grows into a small panel with Play, details, rating and genres; left/right arrows scroll a row. Rows that would repeat an earlier row are skipped.
- **Silent previews:** on the billboard (after ~2.5 s) and when you rest the pointer on a thumbnail (~0.65 s) about 20 s of the real file plays muted, straight from the server (no transcoding, no playback session). Only for titles the browser can decode as they are (H.264 8-bit in MP4/MKV/WebM; HEVC titles keep the still picture), never on touch screens or with *reduce motion* set.
- Jellyfin's own video rows and the library tiles are hidden (our rows replace them; the libraries are in the top bar). Order of Jellyfin's remaining sections is still a per-user setting (User menu → **Home**).
- **Header:** transparent over the picture with the server name in red on the left, solid once you scroll.
- **Movie / show page:** a tall picture that fades into the page, the title logo over its lower edge, then Play and the details, with no poster column.
- Browse rows are cached for ten minutes per browser tab; Continue Watching and Next Up are always fresh.
- All of this runs in the **web** client (computer, phone and tablet browsers; on a phone use *Add to Home screen*). The native Android/iOS apps and the TV app have their own screens, so they show Jellyfin's standard home.

## Manual install
(or just run the script above; this is what it does)
On the server (after the main stack works):
```bash
cd /opt/jellyfin
sudo mkdir -p theme
# pinned commit: check https://github.com/AumGupta/abyss-jellyfin for a newer one and review it before upgrading
SHA=2f809171ed7f3a34b5e78297608b5f2b63dd3f0f
sudo curl -fsSL -o theme/abyss.css "https://raw.githubusercontent.com/AumGupta/abyss-jellyfin/$SHA/abyss.css"
sudo cp ~/selfhost-jellyfin/deploy/theme/custom.css theme/custom.css
sudo cp ~/selfhost-jellyfin/deploy/Caddyfile ~/selfhost-jellyfin/deploy/compose.yaml /opt/jellyfin/
sudo docker compose up -d
```
Then in Jellyfin: **Dashboard → General → Custom CSS** (or *Branding*), paste:
```css
@import url("/theme/abyss.css");
@import url("/theme/custom.css");
@import url("/theme/hero.css");
```
Save and hard-reload the browser (Ctrl+Shift+R).

## Good to know
- The theme loads its font (Google Sans) from Google Fonts and its icon font from a public CDN. If you do not want that, self-host those files or edit the `@import`/`src` lines in your copy of `abyss.css`.
- Abyss styles the **web client**, not the admin dashboard pages, and it needs Jellyfin's **Dark** theme (default). Native phone/TV apps keep their own look.
- Colours and rounding are variables in `custom.css`: `--abyss-accent`, `--abyss-accent-channel` (both the same colour, once as `R, G, B` and once as `R G B`) and `--abyss-radius`.
- Other maintained themes for Jellyfin 12 are listed at github.com/awesome-jellyfin/awesome-jellyfin (THEMES.md); many older ones are marked as not yet verified on version 12.
- Switching back: restore your old Custom CSS (keep a copy of it before you change it).
- Library tiles (photos, see credits below): set a 16:9 picture per library (Dashboard → Libraries → library → Images). `deploy/theme/tiles/make-tiles.ps1` (Windows PowerShell) builds artwork tiles: a slanted collage of your own films' backdrops (put `movie_*.jpg` backdrops in an `art` folder next to the script) for Movies/TV Shows, and drawn illustrations for Music and Audiobooks (ready-made JPGs are included). Upload the results as each library's Primary image, then clear the browser cache once.
- A featured banner is not built into Jellyfin. The one here is our own small script (above); third-party banner add-ons usually load unpinned scripts from public CDNs, so review anything like that before using it on a server your family relies on.
- Give the server a nice name (Dashboard → General → Server name) or the top-left shows the container ID.
- Undo: empty the Custom CSS box.

## Photo credits for the example library tiles
`deploy/theme/tiles/make-photo-tiles.ps1` builds tiles from openly licensed photos on Wikimedia Commons. The photos are **not** bundled in this repo; download them yourself and keep the credits:

| Tile | Photo (Wikimedia Commons) | Author | Licence |
|---|---|---|---|
| Movies | `Kino Atlas Interier J.jpg` | Mojmir Churavy | CC0 |
| Movies | `061217-N-0336C-052 Sailors watch the Christmas film "Elf".jpg` | U.S. Navy | Public domain |
| TV Shows | `Woman sitting on a couch watching television while covered with a blanket.jpg` | Shixart1985 | CC BY 2.0 |
| TV Shows | `Person uses remote control to watch TV at home.jpg` | Shixart1985 | CC BY 2.0 |
| Music | `Close-up of a dj reaching for a vinyl on the turntable, guitar in a blurry background.jpg` | Shixart1985 | CC BY 2.0 |
| Music | `Dj holding a vinyl in her hands next to a turntable, guitar in a blurry background.jpg` | Shixart1985 | CC BY 2.0 |
| Audiobooks | `Man in a checkered shirt enjoys reading a book while wearing headphones.jpg` | Shixart1985 | CC BY 2.0 |

Be gentle with Wikimedia's servers: download a handful of files, with pauses between requests.

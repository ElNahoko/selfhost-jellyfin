# 12. Theme (optional)

Make Jellyfin look nicer: a dark navy/violet skin, rounded cards and colourful library tiles.

## What this uses
- **ElegantFin**, a community CSS theme (GPL-2.0, by lscambo13). It is **styling only** (no scripts). Because of its licence this repo does **not** include it: you download it, pinned to an exact commit.
- `deploy/theme/custom.css`: our small tuning (colours and background) on top of it.
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

## Home page layout (Netflix-style order)
- **Order** is a per-user Jellyfin setting (User menu → **Home**). Recommended: *Continue Watching → Next Up → My Media (library tiles) → Latest media*. It is stored on the server, so it applies on every device that honours Jellyfin's home settings. Apply it to all accounts at once through the API (`/DisplayPreferences/usersettings?userId=…&client=emby`, keys `homesection0…`, values such as `resume`, `nextup`, `smalllibrarytiles`, `latestmedia`).
- **My Media** becomes an even, full-width row of picture tiles (2 per row on phones).
- **Suggested for you / Because you watched … / Top rated** rows come from Jellyfin's own recommendation endpoints and appear once there are at least two items to show. With a small library they overlap a lot; they get better as it grows. Below them come **one row per genre** that has at least three titles (best rated first), so the home page reads like Netflix categories.
- The banner and extra rows run in the **web** client (computer, phone and tablet browsers; on a phone use *Add to Home screen* for an app-like icon). The native Android/iOS apps and the TV app have their own screens, so they show Jellyfin's standard home with the section order above and the colours where the app supports server styling.

## Manual install
On the server (after the main stack works):
```bash
cd /opt/jellyfin
sudo mkdir -p theme
# pinned commit: check https://github.com/lscambo13/ElegantFin for a newer one and review it before upgrading
SHA=9d43fa9b898c74055237133b7a7b8b8c5543f0ce
sudo curl -fsSL -o theme/elegantfin.css "https://raw.githubusercontent.com/lscambo13/ElegantFin/$SHA/Theme/ElegantFin-theme-v26.09.05.css"
sudo cp ~/selfhost-jellyfin/deploy/theme/custom.css theme/custom.css
sudo cp ~/selfhost-jellyfin/deploy/Caddyfile ~/selfhost-jellyfin/deploy/compose.yaml /opt/jellyfin/
sudo docker compose up -d
```
Then in Jellyfin: **Dashboard → General → Custom CSS** (or *Branding*), paste:
```css
@import url("/theme/elegantfin.css");
@import url("/theme/custom.css");
```
Save and hard-reload the browser (Ctrl+Shift+R).

## Good to know
- The theme loads two fonts from Google Fonts (Inter and an icon font). If you do not want that, self-host those fonts or remove the `@import` lines for fonts from your copy of the theme file.
- **Jellyfin 12 ships a new interface.** ElegantFin documents full support for the *Legacy* interface; in each client open **User menu → Display → Display mode** and choose **Desktop (Legacy)** if something looks off.
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

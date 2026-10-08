# 12. Theme (optional)

Make Jellyfin look nicer: a dark navy/violet skin, rounded cards and colourful library tiles.

## What this uses
- **ElegantFin**, a community CSS theme (GPL-2.0, by lscambo13). It is **styling only** (no scripts). Because of its licence this repo does **not** include it: you download it, pinned to an exact commit.
- `deploy/theme/custom.css`: our small tuning (colours and background) on top of it.
- The files are served from **your own server** by Caddy at `/theme/`, so your viewers' devices do not depend on a public CDN for the theme.

## Install
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
- Library tiles: set a 16:9 picture per library (Dashboard → Libraries → library → Images), for example a coloured gradient with the library name.
- A big "featured" banner on the home page is **not** part of Jellyfin. Add-ons for it work by editing Jellyfin's web files and loading scripts from public CDNs: weigh that before using one on a server your family relies on.
- Undo: empty the Custom CSS box.

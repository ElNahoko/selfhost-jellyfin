# 9. Connect devices

Server address everywhere: `https://your-host` (no port number, no `http://`).

## LG webOS TV
1. Open the **LG Content Store** → search **Jellyfin** → install.
2. Open the app → **Add server** → enter `https://your-host`.
3. Sign in with the user's own account.
4. If it cannot connect: check the TV's date/time and DNS (Settings → Network), try another network, confirm the certificate is valid with `curl -I https://your-host`.
5. Playback tips: use SRT subtitles or none, avoid DTS/TrueHD-only files, start with 1080p H.264/HEVC. Check Dashboard → Activity to see if the TV is Direct Playing.

webOS apps depend on the TV's model and firmware. If the Jellyfin app is unavailable for your model/region, use casting from a phone or a small streaming stick instead of changing the server.

## Phones and tablets
- **Android:** install the official Jellyfin app, add the server URL, sign in.
- **iPhone/iPad:** install the Jellyfin app from the App Store (or a compatible third-party Jellyfin client), add the server URL, sign in.
- In app settings leave the max streaming bitrate on Auto, disable "transcode" fallbacks if offered.

## Browsers
Open `https://your-host` and sign in. Chrome/Edge/Firefox work; for best results prefer files with H.264 + AAC. For files the browser cannot play (AC3, DTS), install the app on a device that can.

## Inviting a family member
1. Dashboard → Users → **Add**, a plain name, a temporary password you tell them privately.
2. Libraries: only what they should see. No admin rights.
3. Playback: transcoding limits as in [Configure Jellyfin](06-configure-jellyfin.md).
4. Ask them to change the password on first login.
5. Send them the URL and the steps for their device.

## One-line verification per device
- **TV:** open any title and check Dashboard → Activity says *Direct Play*.
- **Phone on mobile data:** play 1 minute of a 1080p title.
- **Remote family network:** have them run any online speed test: they need roughly 15 Mbit/s of **download** per stream.

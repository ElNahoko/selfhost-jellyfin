# 1. Choosing a VPS

Advertised specs and checkout reality often differ. Verify each point **on the official order page and the terms**, not on forum posts.

## Must-have checklist

| Check | Why it matters |
|---|---|
| Full virtualization (**KVM** or similar), root access | Docker needs a real kernel. Container-based VPS (OpenVZ/LXC) often cannot run Docker properly. |
| Ubuntu 24.04 template (or custom ISO) | Matches the scripts here. Check the template list *after* login if the sales page only says "Ubuntu". |
| Usable data disk ≥ your library size | Check whether the "2 TB" is one disk with the OS or a separate volume, and whether it is shared or dedicated. |
| Separate small SSD/NVMe for OS, Docker, Jellyfin database | Jellyfin's database and metadata are small-random-IO heavy. HDDs are poor at that. |
| Monthly traffic allowance | See the math below. When exceeded, many providers throttle hard. |
| Port speed and region | Pick a datacenter close to most viewers. Latency matters less than throughput for video, but a distant region can reduce per-stream speed. |
| Price **with VAT**, and the **renewal** price | Intro discounts often end after the first term. Compare the recurring monthly price. |
| Terms: personal media streaming, CPU/IO fair use | Many providers do not name Jellyfin/Plex; "persistent heavy resource use" clauses can still apply. Ask support in writing if unclear. |
| Refund window | Often only 7 days. Test the server inside that window. |
| IPv4 included | Many TVs and ISPs still need IPv4. |
| Recovery/VNC console | Lets you fix a broken SSH config. |

## Capacity math (Direct Play)

Direct Play means the server only reads the file and sends it. CPU use is low.

- 1080p H.264 files typically run **6–15 Mbit/s** (peaks higher).
- 4 simultaneous streams at 12 Mbit/s ≈ **48 Mbit/s** of sustained upload. Any 1 Gbit port handles this.
- 1 hour at 10 Mbit/s ≈ **4.5 GB** of traffic. 4 streams × 3 h/day × 30 days at 10 Mbit/s ≈ **1.6 TB/month**.
- 4K remux files can reach 40–80 Mbit/s each. Remote family on average home connections will not play those smoothly.

A 1–2 vCPU server **cannot transcode** 4 streams. Design for compatibility instead (see [Configure Jellyfin](06-configure-jellyfin.md)).

## Storage reality check

- A large HDD volume is fine for sequential video reads. It is slow for the database, so keep `/opt/jellyfin/config` on the SSD/NVMe system disk.
- If the data disk sits behind a write cache, a failure can lose recent writes. Treat the VPS as **non-durable**: keep originals elsewhere, back up Jellyfin's config off-server.
- The provider's disk is **not a backup**.

## Before paying

1. Write down: plan, extra storage, term, total with VAT, renewal total, refund window.
2. Place the order only when the final page shows the same totals.
3. After provisioning, run the checks in [Secure the server](02-secure-the-server.md) and measure disk and network inside the refund window.

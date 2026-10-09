#!/usr/bin/env python3
"""Monthly network usage of the server, for the LUMIO sidebar. Root cron, every 5 minutes:
   */5 * * * * /usr/bin/python3 /opt/jellyfin/scripts/netstat.py"""
import json, os, subprocess, time
OUT = "/opt/jellyfin/uploads/data/net.json"
iface = [l.split()[4] for l in subprocess.run(["ip", "route"], capture_output=True, text=True).stdout.splitlines() if l.startswith("default")][0]
def rd(n): return int(open("/sys/class/net/%s/statistics/%s_bytes" % (iface, n)).read())
tx, rx = rd("tx"), rd("rx")
month = time.strftime("%Y-%m")
try:
    d = json.load(open(OUT))
    if d.get("month") != month: d = {"month": month, "tx": 0, "rx": 0, "last_tx": tx, "last_rx": rx}
except Exception:
    d = {"month": month, "tx": tx, "rx": rx, "last_tx": tx, "last_rx": rx}     # first run: count what was sent since boot
d["tx"] += tx - d["last_tx"] if tx >= d["last_tx"] else tx                      # the counters restart at boot
d["rx"] += rx - d["last_rx"] if rx >= d["last_rx"] else rx
d["last_tx"], d["last_rx"], d["t"] = tx, rx, int(time.time())
tmp = OUT + ".tmp"
with open(tmp, "w") as f: json.dump(d, f)
os.chmod(tmp, 0o644); os.replace(tmp, OUT)

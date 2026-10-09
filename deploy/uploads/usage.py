#!/usr/bin/env python3
"""What uses the server right now, for the admin panel (click CPU or RAM). Root cron, every minute:
   * * * * * /usr/bin/python3 /opt/jellyfin/scripts/usage.py"""
import json, os, subprocess, time
OUT = "/opt/jellyfin/uploads/data/usage.json"

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=40).stdout

containers = []
for line in run(["docker", "stats", "--no-stream", "--format", "{{json .}}"]).splitlines():
    try:
        d = json.loads(line)
        used = d["MemUsage"].split("/")[0].strip()
        num = float("".join(c for c in used if c.isdigit() or c == "."))
        mib = num * (1024 if "GiB" in used else 1 if "MiB" in used else 1 / 1024)
        containers.append({"name": d["Name"], "cpu": float(d["CPUPerc"].rstrip("%")), "mem_mb": round(mib)})
    except Exception:
        pass
procs = []
for line in run(["ps", "-eo", "pcpu,rss,comm", "--sort=-pcpu", "--no-headers"]).splitlines()[:8]:
    p = line.split(None, 2)
    if len(p) == 3: procs.append({"cpu": float(p[0]), "mem_mb": int(p[1]) // 1024, "name": p[2][:40]})
mem = {l.split(":")[0]: int(l.split()[1]) // 1024 for l in open("/proc/meminfo")}
out = {"t": int(time.time()), "load": open("/proc/loadavg").read().split()[:3], "cpus": os.cpu_count(),
       "mem_total_mb": mem["MemTotal"], "mem_available_mb": mem["MemAvailable"], "swap_used_mb": mem["SwapTotal"] - mem["SwapFree"],
       "containers": sorted(containers, key=lambda c: -c["cpu"]), "processes": procs}
tmp = OUT + ".tmp"
with open(tmp, "w") as f: json.dump(out, f)
os.chmod(tmp, 0o644); os.replace(tmp, OUT)

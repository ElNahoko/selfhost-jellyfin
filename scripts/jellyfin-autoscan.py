#!/usr/bin/env python3
"""Asks Jellyfin to scan ONLY the affected library after media changes have settled.

Why this exists: Jellyfin's real-time watcher only starts for library folders that already contain files when
Jellyfin starts or scans. A library that was empty then (a new TV Shows / Music library) is never watched, so
uploads into it stay invisible until the 12-hourly scan.

Behaviour (all concrete, nothing fancy):
  * Debounce: a scan is requested only after QUIET seconds with no filesystem activity, so a multi-file or
    multi-GB upload produces ONE scan, not one per file or per chunk.
  * Uploads in progress: while a "*.uploading" temp file (the upload page's resumable uploads) was written to
    recently, no scan starts, even if the connection is slow.
  * Per library: changes are mapped to the library that owns the folder; only those libraries are refreshed.
  * No overlap: waits while Jellyfin's own "Scan Media Library" task is running; at least COOLDOWN seconds
    between scans of the same library.
  * Survives restarts: systemd restarts the service; on start it catches up on anything that changed while it (or
    Jellyfin, or the whole VPS) was down; failed requests are retried until Jellyfin answers.
The API key lives in /etc/selfhost-jellyfin/autoscan.env (mode 600); calls run inside the Jellyfin container, so
Jellyfin's port is never published on the host.
"""
import os
import queue
import subprocess
import sys
import threading
import time

WATCH = os.environ.get("WATCH", "/srv/media")
CONTAINER = os.environ.get("CONTAINER", "jellyfin")
CONTAINER_ROOT = os.environ.get("CONTAINER_ROOT", "/media")        # where WATCH is mounted inside the container
ENVF = os.environ.get("ENVF", "/etc/selfhost-jellyfin/autoscan.env")
STATE = os.environ.get("STATE", "/var/lib/selfhost-jellyfin/autoscan")
QUIET = int(os.environ.get("QUIET", "60"))                          # seconds without changes before scanning
COOLDOWN = int(os.environ.get("COOLDOWN", "90"))                    # min seconds between scans of one library
STALE_UPLOAD = int(os.environ.get("STALE_UPLOAD", "1800"))          # a .uploading file untouched this long is abandoned
TEMP_SUFFIX = ".uploading"


def log(msg):
    print(msg, flush=True)


def load_key():
    with open(ENVF, encoding="utf-8") as f:
        for line in f:
            if line.startswith("JELLYFIN_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("JELLYFIN_API_KEY missing in " + ENVF)


KEY = load_key()


def api(method, path):
    """Call Jellyfin from inside its container. Returns response text, or None on any failure."""
    script = 'curl -fsS -m 30 -X "$0" -H "Authorization: MediaBrowser Token=\\"$K\\"" "http://localhost:8096$1"'
    try:
        r = subprocess.run(["docker", "exec", "-e", "K=" + KEY, CONTAINER, "sh", "-c", script, method, path],
                           capture_output=True, text=True, timeout=45)
    except Exception as e:                                           # docker missing, container stopped, timeout...
        log("api error: %s" % e)
        return None
    return r.stdout if r.returncode == 0 else None


def libraries():
    """[(library_id, name, [host_path_prefix, ...])] from Jellyfin; None if Jellyfin is not answering."""
    import json
    txt = api("GET", "/Library/VirtualFolders")
    if txt is None:
        return None
    out = []
    for lib in json.loads(txt):
        hosts = []
        for loc in lib.get("Locations", []):
            if loc == CONTAINER_ROOT or loc.startswith(CONTAINER_ROOT + "/"):
                hosts.append(WATCH + loc[len(CONTAINER_ROOT):])
        out.append((lib["ItemId"], lib["Name"], hosts))
    return out


def library_for(path, libs):
    for lid, name, hosts in libs:
        for h in hosts:
            if path == h or path.startswith(h.rstrip("/") + "/"):
                return lid
    return None


def jellyfin_scan_running():
    import json
    txt = api("GET", "/ScheduledTasks")
    if txt is None:
        return False
    return any(t.get("Name") == "Scan Media Library" and t.get("State") == "Running" for t in json.loads(txt))


def upload_in_progress():
    """True if any *.uploading temp file was touched within STALE_UPLOAD seconds."""
    now = time.time()
    for root, _dirs, files in os.walk(WATCH):
        for name in files:
            if name.endswith(TEMP_SUFFIX):
                try:
                    if now - os.stat(os.path.join(root, name)).st_mtime < STALE_UPLOAD:
                        return True
                except OSError:
                    pass
    return False


def marker(lid):
    return os.path.join(STATE, lid + ".marker")


def changed_since_marker(hosts, lid):
    """Catch-up after downtime: anything newer than the last scan marker in this library's folders?"""
    m = marker(lid)
    if not os.path.exists(m):
        return False                                                 # first run: nothing to compare with
    ref = os.stat(m).st_mtime
    for h in hosts:
        for root, dirs, files in os.walk(h):
            for n in dirs + files:
                if n.endswith(TEMP_SUFFIX):
                    continue
                try:
                    if os.stat(os.path.join(root, n)).st_mtime > ref:
                        return True
                except OSError:
                    pass
    return False


def touch(path):
    with open(path, "a"):
        os.utime(path, None)


def reader(q):
    cmd = ["inotifywait", "-m", "-r", "-q", "-e", "close_write,moved_to,create,delete,moved_from",
           "--format", "%w%f", WATCH]
    while True:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        for line in p.stdout:
            q.put(line.rstrip("\n"))
        log("inotifywait ended, restarting in 5 s")
        time.sleep(5)


def main():
    os.makedirs(STATE, exist_ok=True)
    q = queue.Queue()
    threading.Thread(target=reader, args=(q,), daemon=True).start()
    log("watching %s (quiet %ds, cooldown %ds)" % (WATCH, QUIET, COOLDOWN))

    pending = set()            # library ids with unscanned changes
    last_event = 0.0
    last_scan = {}             # library id -> time of last scan request
    libs = None
    libs_at = 0.0
    caught_up = False

    def refresh_libs(max_age):
        nonlocal libs, libs_at
        if libs is None or time.time() - libs_at >= max_age:
            got = libraries()                                        # one cheap call; skipped while idle
            libs_at = time.time()
            if got is not None:
                libs = got

    while True:
        try:
            path = q.get(timeout=5)
        except queue.Empty:
            path = None

        if path is not None:                                         # a filesystem event
            last_event = time.time()
            if "/lost+found" in path or path.endswith(TEMP_SUFFIX):
                continue                                             # temp files only keep the debounce running
            refresh_libs(300)
            lid = library_for(path, libs or [])
            if lid:
                pending.add(lid)                                     # folders outside every library are ignored
            continue

        # idle tick: do real work only when there is something to do
        if not caught_up:
            refresh_libs(10)
            if libs is None:
                continue                                             # Jellyfin not up yet; try again on the next tick
            caught_up = True
            for lid, name, hosts in libs:
                if changed_since_marker(hosts, lid):
                    log("catch-up: %s changed while the service or Jellyfin was down" % name)
                    pending.add(lid)
                    last_event = 0.0
                elif not os.path.exists(marker(lid)):
                    touch(marker(lid))
        if not pending or time.time() - last_event < QUIET:
            continue
        refresh_libs(60)
        if libs is None or upload_in_progress() or jellyfin_scan_running():
            continue                                                 # slow upload still running / Jellyfin busy

        targets = [l for l in libs if l[0] in pending and time.time() - last_scan.get(l[0], 0) >= COOLDOWN]
        for lid, name, _h in targets:
            started = time.time()
            ok = api("POST", "/Items/%s/Refresh?Recursive=true&ImageRefreshMode=Default&MetadataRefreshMode=Default"
                             "&ReplaceAllImages=false&ReplaceAllMetadata=false" % lid) is not None
            if ok:
                last_scan[lid] = started
                touch(marker(lid))
                os.utime(marker(lid), (started, started))
                pending.discard(lid)
                log("refresh requested for library: %s" % name)
            else:
                log("refresh FAILED for %s (Jellyfin not answering?), will retry" % name)


if __name__ == "__main__":
    main()

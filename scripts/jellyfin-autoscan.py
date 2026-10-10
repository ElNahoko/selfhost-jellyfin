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
  * Subtitles: Subs/<video>/2_English.srt style files are copied next to the video as <video>.eng.srt so Jellyfin
    sees them (see "subtitle importer" below; originals stay untouched).
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


# ---- subtitle importer -------------------------------------------------------------------------------------------
# Jellyfin only finds external subtitles that sit next to the video and start with its file name. Many releases ship
# them as  Subs/<video name>/2_English.srt  instead, so Jellyfin shows no subtitle button. Before each scan, COPY
# such files next to the video as  <video name>.<lang>.srt  (originals are never touched or removed).
VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".wmv")
SUB_EXT = (".srt", ".ass", ".ssa", ".vtt")
SUB_DIRS = ("subs", "subtitles", "subtitle", "sub")
FLAGS = {"forced", "default", "sdh", "cc", "hi"}
# names/aliases -> codes Jellyfin recognises (ISO 639-2/T)
LANG = {}
for _code, _names in {
    "eng": "english en", "fra": "french fre fr", "spa": "spanish es", "ara": "arabic ar", "deu": "german ger de",
    "ita": "italian it", "por": "portuguese pt brazilian", "nld": "dutch dut nl", "pol": "polish pl",
    "rus": "russian ru", "tur": "turkish tr", "jpn": "japanese ja", "kor": "korean ko", "zho": "chinese chi zh",
    "vie": "vietnamese vi", "ind": "indonesian id", "tha": "thai th", "hin": "hindi hi", "heb": "hebrew he",
    "ell": "greek gre el", "swe": "swedish sv", "nor": "norwegian no nob", "dan": "danish da", "fin": "finnish fi",
    "ces": "czech cze cs", "hun": "hungarian hu", "ron": "romanian rum ro", "bul": "bulgarian bg",
    "hrv": "croatian hr", "srp": "serbian sr", "slv": "slovenian slv sl", "slk": "slovak slo sk",
    "ukr": "ukrainian uk", "fas": "persian farsi per fa", "msa": "malay may ms", "ben": "bengali bn",
    "tam": "tamil ta", "tel": "telugu te", "isl": "icelandic ice is", "lit": "lithuanian lt", "lav": "latvian lv",
    "est": "estonian et", "sqi": "albanian alb sq", "mkd": "macedonian mac mk", "cat": "catalan ca",
    "eus": "basque baq eu", "glg": "galician gl", "urd": "urdu ur", "fil": "filipino tagalog tl",
}.items():
    LANG[_code] = _code
    for _n in _names.split():
        LANG[_n] = _code


def parse_sub_name(fname, stem):
    """'2_English.forced.srt' -> ('eng', ['forced']); None if it is not a subtitle file."""
    base, ext = os.path.splitext(fname)
    if ext.lower() not in SUB_EXT:
        return None
    if base.lower().startswith(stem.lower()):
        base = base[len(stem):]
    base = base.lstrip("0123456789").lstrip("_-. ") if base.lstrip("0123456789")[:1] in "_-. " else base.lstrip("_-. ")
    lang, flags, extra = None, [], []
    for tok in [t for t in base.replace("_", ".").replace("-", ".").replace(" ", ".").split(".") if t]:
        t = tok.lower()
        if t in FLAGS:
            flags.append(t)
        elif lang is None and t in LANG:
            lang = LANG[t]
        else:
            extra.append(tok)
    if lang is None:
        lang = "und"
    return lang, flags, extra, ext.lower()


def sync_subtitles(hosts, made):
    """Copy Subs/... files next to their video. Returns how many files were created."""
    created = 0
    for host in hosts:
        for root, dirs, files in os.walk(host):
            dirs[:] = [d for d in dirs if d.lower() not in SUB_DIRS]    # videos live outside the Subs folders
            videos = [f for f in files if f.lower().endswith(VIDEO_EXT)]
            for v in videos:
                stem = os.path.splitext(v)[0]
                vpath = os.path.join(root, v)
                for sd in os.listdir(root):
                    if sd.lower() not in SUB_DIRS or not os.path.isdir(os.path.join(root, sd)):
                        continue
                    sdir = os.path.join(root, sd)
                    cands = []                                         # (directory, require_name_prefix)
                    own = [d for d in os.listdir(sdir) if d.lower() == stem.lower() and os.path.isdir(os.path.join(sdir, d))]
                    for d in own:
                        cands.append((os.path.join(sdir, d), False))
                    cands.append((sdir, len(videos) > 1))             # files directly in Subs/: need the name match if several videos
                    for cdir, need_prefix in cands:
                        try:
                            names = sorted(os.listdir(cdir), key=lambda n: (int(n.split("_")[0]) if n.split("_")[0].isdigit() else 0, n))
                        except OSError:
                            continue
                        seen = {}
                        for n in names:
                            fp = os.path.join(cdir, n)
                            if not os.path.isfile(fp) or (need_prefix and not n.lower().startswith(stem.lower())):
                                continue
                            p = parse_sub_name(n, stem)
                            if p is None:
                                continue
                            lang, flags, extra, ext = p
                            key = (lang, tuple(flags))
                            seen[key] = seen.get(key, 0) + 1
                            tokens = [lang] + flags + ([str(seen[key])] if seen[key] > 1 else []) + extra
                            target = os.path.join(root, stem + "." + ".".join(tokens) + ext)
                            if os.path.exists(target):
                                continue
                            tmp = target + TEMP_SUFFIX
                            try:
                                with open(fp, "rb") as src, open(tmp, "wb") as dst:
                                    dst.write(src.read())
                                st = os.stat(vpath)
                                os.chown(tmp, st.st_uid, st.st_gid)
                                os.chmod(tmp, 0o644)
                                made[target] = time.time()
                                made[tmp] = time.time()
                                os.replace(tmp, target)
                                created += 1
                            except OSError as e:
                                log("subtitle copy failed for %s: %s" % (n, e))
                                try:
                                    os.remove(tmp)
                                except OSError:
                                    pass
    return created


def prune_missing(lid, name, hosts):
    """Remove library entries whose file or folder no longer exists on disk.

    Why: Jellyfin refuses to remove anything when a library folder has become completely EMPTY (it cannot tell "you
    deleted everything" from "the disk is not mounted"), so deleted movies stay in the library as ghosts. We can tell the
    difference: the data disk must be a real mount point and the library folder must exist. Entries are removed in
    Jellyfin's database only (the media is mounted read-only inside the container and the files are already gone).
    """
    import json
    if os.environ.get("PRUNE", "1") == "0":
        return
    if os.environ.get("PRUNE_REQUIRE_MOUNT", "1") == "1" and not os.path.ismount(WATCH):
        log("prune skipped: %s is not a mount point (disk missing?)" % WATCH)
        return
    if not hosts or not all(os.path.isdir(h) for h in hosts):
        log("prune skipped for %s: library folder missing" % name)
        return
    txt = api("GET", "/Items?ParentId=%s&Recursive=true&Fields=Path&EnableTotalRecordCount=false" % lid)
    if txt is None:
        return
    gone = 0
    for it in json.loads(txt).get("Items", []):
        path = it.get("Path") or ""
        if not (path == CONTAINER_ROOT or path.startswith(CONTAINER_ROOT + "/")):
            continue
        if it.get("LocationType") == "Virtual" or it.get("IsFolder") and it.get("Type") in ("CollectionFolder", "UserView"):
            continue
        if not os.path.exists(WATCH + path[len(CONTAINER_ROOT):]):
            if api("DELETE", "/Items/%s" % it["Id"]) is not None:
                gone += 1
                log("removed from library (file is gone): %s" % (it.get("Name") or path))
    if gone:
        log("%d missing entr%s removed from %s" % (gone, "y" if gone == 1 else "ies", name))


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


SUBQ = os.environ.get("SUBQ", "/opt/jellyfin/uploads/data/subocr-queue.json")


def queue_subtitles():
    """New videos: ask the subtitle converter (watch.sh starts it within a minute) to turn their picture subtitles into
    text and take the picture tracks out. Not while it runs: then the change is its own work."""
    try:
        busy = "subocr" in subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20).stdout.split()
    except Exception:
        busy = True
    if busy or os.path.exists(SUBQ) or not os.path.isdir(os.path.dirname(SUBQ)):
        return
    with open(SUBQ, "w") as f:
        f.write('{"all": true, "reason": "new videos"}')
    os.chmod(SUBQ, 0o666)
    log("subtitles: converter queued for the new videos")


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
    made = {}                  # files this service created itself -> time; their events must not trigger scans
    videos = False             # a video arrived or changed: queue the subtitle converter after the scan

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
            if time.time() - made.get(path, 0) < 120:
                continue                                             # our own subtitle copy
            last_event = time.time()
            if "/lost+found" in path or path.endswith(TEMP_SUFFIX) or path.endswith(".nahoko-tmp.mkv"):
                continue                                             # temp files only keep the debounce running
            if path.lower().endswith(".mkv"):
                videos = True
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
                n = sync_subtitles(hosts, made)
                if n:
                    log("subtitles: copied %d file(s) next to their videos in %s" % (n, name))
                    pending.add(lid)
                    last_event = 0.0
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
        for lid, name, hosts in targets:
            n = sync_subtitles(hosts, made)
            if n:
                log("subtitles: copied %d file(s) next to their videos in %s" % (n, name))
            started = time.time()
            ok = api("POST", "/Items/%s/Refresh?Recursive=true&ImageRefreshMode=Default&MetadataRefreshMode=Default"
                             "&ReplaceAllImages=false&ReplaceAllMetadata=false" % lid) is not None
            if ok:
                last_scan[lid] = started
                touch(marker(lid))
                os.utime(marker(lid), (started, started))
                pending.discard(lid)
                log("refresh requested for library: %s" % name)
                prune_missing(lid, name, hosts)
            else:
                log("refresh FAILED for %s (Jellyfin not answering?), will retry" % name)
        if videos and not pending:                                   # every changed library scanned: subtitles next
            videos = False
            queue_subtitles()


if __name__ == "__main__":
    main()

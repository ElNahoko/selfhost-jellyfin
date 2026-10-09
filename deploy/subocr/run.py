"""Picture subtitles (PGS) -> text .srt files next to each video, with a status file for the LUMIO admin panel.

MODE=all    (nightly)  check every video, convert what is missing
MODE=queue  (panel)    convert only what the panel asked for in /status/subocr-queue.json
Status: /status/subocr.json. Runs at the lowest CPU priority, so playback always wins."""
import glob, json, os, subprocess, time

MEDIA = "/media"
STATUS = "/status/subocr.json"
QUEUE = "/status/subocr-queue.json"
LANGS = os.environ.get("LANGS", "en fr").split()
MODE = os.environ.get("MODE", "all")
ISO = {"eng": "en", "fre": "fr", "fra": "fr", "spa": "es", "ger": "de", "deu": "de", "ita": "it", "por": "pt", "jpn": "ja",
       "dan": "da", "fin": "fi", "nob": "nb", "nor": "no", "swe": "sv", "dut": "nl", "nld": "nl", "pol": "pl", "rus": "ru"}

def save(st):
    st["updated"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, separators=(",", ":"))
    os.replace(tmp, STATUS)

def pgs_langs(path):
    """Languages of the picture subtitle tracks, from mkvmerge (fast, reads only the headers)."""
    try:
        info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True, timeout=120).stdout)
    except Exception:
        return None
    out = []
    for t in info.get("tracks", []):
        if t.get("type") == "subtitles" and "PGS" in (t.get("codec") or ""):
            p = t.get("properties", {})
            l = (p.get("language_ietf") or "").split("-")[0] or ISO.get(p.get("language") or "", p.get("language") or "und")
            if l not in out: out.append(l)
    return out

def srt_langs(path):
    base = path[:-4]
    have = set()
    for f in glob.glob(glob.escape(base) + ".*.srt"):
        parts = f[len(base) + 1:-4].split(".")
        if parts: have.add(parts[0])
    return sorted(have)

def inventory(old):
    prev = {f["p"]: f for f in old.get("files", [])}
    files = []
    for root, _, names in os.walk(MEDIA):
        for n in sorted(names):
            if not n.lower().endswith(".mkv"): continue
            p = os.path.join(root, n); rel = os.path.relpath(p, MEDIA)
            pg = pgs_langs(p)
            if not pg: continue                                  # no picture subtitles: nothing to do
            have = srt_langs(p)
            want = [l for l in LANGS if l in pg]
            todo = [l for l in want if l not in have]
            state = "other" if not want else ("done" if not todo else ("failed" if prev.get(rel, {}).get("state") == "failed" else "todo"))
            files.append({"p": rel, "pgs": pg, "srt": have, "state": state, "err": prev.get(rel, {}).get("err", "") if state == "failed" else ""})
    files.sort(key=lambda f: f["p"])
    return files

def convert(st, f):
    path = os.path.join(MEDIA, f["p"])
    st["current"] = f["p"]; st["current_t"] = int(time.time()); save(st)
    args = ["nice", "-n", "19", "pgsrip", "rip"]
    for l in LANGS: args += ["-l", l]
    args += ["--one-per-language", "--engine", "tesseract", "--workers", "1", path]
    r = subprocess.run(args, capture_output=True, text=True)
    f["srt"] = srt_langs(path)
    todo = [l for l in LANGS if l in f["pgs"] and l not in f["srt"]]
    for l in todo:          # pgsrip holds every image of a track at once and dies on long films: read those one subtitle at a time
        r = subprocess.run(["nice", "-n", "19", "python", "/stream_ocr.py", path, l], capture_output=True, text=True)
    f["srt"] = srt_langs(path)
    todo = [l for l in LANGS if l in f["pgs"] and l not in f["srt"]]
    if todo:
        f["state"] = "failed"; f["err"] = (r.stderr or r.stdout or "no subtitle written").strip().splitlines()[-1][:200] if (r.stderr or r.stdout) else "no subtitle written"
    else:
        f["state"] = "done"; f["err"] = ""
    print(time.strftime("%H:%M"), f["state"], f["p"], flush=True)

def take_queue():
    try:
        with open(QUEUE) as q: want = json.load(q)
        os.remove(QUEUE)
        return want
    except (OSError, ValueError):
        return None

def main():
    try:
        with open(STATUS) as s: old = json.load(s)
    except (OSError, ValueError):
        old = {}
    st = {"running": True, "mode": MODE, "started": int(time.time()), "langs": LANGS, "files": old.get("files", []), "current": ""}
    save(st)
    st["files"] = inventory(old); save(st)
    by_path = {f["p"]: f for f in st["files"]}
    done_any = False
    while True:
        q = take_queue()
        if q is not None:
            sel = [f for f in st["files"] if f["state"] in ("todo", "failed")] if q.get("all") else [by_path[p] for p in q.get("files", []) if p in by_path]
        elif MODE == "all":
            sel = [f for f in st["files"] if f["state"] == "todo"]
        else:
            sel = []
        if not sel: break
        for f in sel:
            if f["state"] == "done" or f["state"] == "other": continue
            convert(st, f); done_any = True; save(st)
            if os.path.exists(QUEUE): break               # the panel asked for something else: do that next
        if MODE != "all" and not os.path.exists(QUEUE): break
        if MODE == "all" and not os.path.exists(QUEUE): break
    st["running"] = False; st["current"] = ""; st["finished"] = int(time.time()); save(st)
    print("converted" if done_any else "nothing to convert", flush=True)
    raise SystemExit(0 if done_any else 3)

if __name__ == "__main__":
    main()

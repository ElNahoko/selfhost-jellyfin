"""Low-memory OCR of one picture-subtitle track: one subtitle image at a time, written as it goes.

pgsrip decodes every image of a track before reading them, which for a feature film needs more memory than
this server has. This reads, OCRs and writes each subtitle in turn, so memory stays flat (~100 MB).
Usage: stream_ocr.py VIDEO LANG       (LANG: en, fr, ...)  ->  VIDEO-without-extension.LANG.srt"""
import json, os, re, subprocess, sys, tempfile
import numpy as np
import pytesseract
from pgsrip.formats import pgs

TESS = {"en": "eng", "fr": "fra"}
ISO = {"eng": "en", "fre": "fr", "fra": "fr"}

def pick_track(path, lang):
    info = json.loads(subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True).stdout)
    tracks = []
    for t in info.get("tracks", []):
        p = t.get("properties", {})
        l = (p.get("language_ietf") or "").split("-")[0] or ISO.get(p.get("language") or "", "")
        if t.get("type") == "subtitles" and "PGS" in (t.get("codec") or "") and l == lang:
            tracks.append((bool(p.get("forced_track")), bool(p.get("flag_hearing_impaired")), t["id"]))
    tracks.sort()                           # full subtitles first, then hearing-impaired, forced-only last
    return tracks[0][2] if tracks else None

def stamp(ms):
    ms = max(0, int(ms))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)

def clean(text):
    lines = [re.sub(r"\s+", " ", l).strip().replace("|", "I") for l in text.splitlines()]
    return "\n".join(l for l in lines if l)

def read(group, image_lang):
    img = pgs.first_image(group)
    if img is None or not img.palette or not img.rle_data: return ""
    bitmap = pgs.decode_rle_image(img.rle_data, img.palette)
    bitmap = np.pad(bitmap, 12, constant_values=255)
    return clean(pytesseract.image_to_string(bitmap, lang=image_lang, config="--psm 6 --oem 1"))

def main(path, lang):
    track = pick_track(path, lang)
    if track is None: print("no picture subtitles in", lang); return 2
    out = os.path.splitext(path)[0] + "." + lang + ".srt"
    with tempfile.TemporaryDirectory() as tmp:
        sup = os.path.join(tmp, "t.sup")
        subprocess.run(["mkvextract", path, "tracks", "%d:%s" % (track, sup)], capture_output=True, check=True)
        data = open(sup, "rb").read()
        n, group, prev = 0, [], None
        with open(out + ".part", "w", encoding="utf-8") as srt:
            def flush(g, next_start):
                nonlocal n
                start = pgs.start_time(g)
                end = max((t for ds in g if (t := ds.pcs.presentation_timestamp) is not None), default=None)
                if start is None: return
                if end is None or end <= start: end = next_start if next_start else start + 2000
                text = read(g, TESS.get(lang, "eng"))
                if not text: return
                n += 1
                srt.write("%d\n%s --> %s\n%s\n\n" % (n, stamp(start), stamp(end), text))
            for ds in pgs.read_display_sets(data, os.path.basename(path)):
                if ds.error(): continue
                if ds.is_start() and group:
                    flush(group, pgs.start_time([ds]))
                    group = []
                group.append(ds)
            if group: flush(group, None)
    if n == 0:
        os.remove(out + ".part"); print("nothing readable"); return 1
    os.replace(out + ".part", out)
    print(n, "subtitles ->", os.path.basename(out))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))

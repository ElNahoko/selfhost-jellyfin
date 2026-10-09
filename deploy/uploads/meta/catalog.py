"""Browsable catalogue for the request page, built from IMDb's free daily datasets (ratings, titles, genres, runtime).
Posters and plot summaries come from Jellyfin's own TMDb lookup (by IMDb id) and are cached in SQLite, so the page loads fast.
Everything lives in /db. Rebuilt weekly in the background at low priority.

Shelves are rules over that data ("hidden gems", "mind-benders", "short and sweet", ...), shown on the home view; every
shelf can also be opened on its own with up to 60 titles."""
import gzip, json, os, sqlite3, threading, time, urllib.request
from datetime import date

DBDIR = os.environ.get("DB_DIR", "/db")
CAT = os.path.join(DBDIR, "catalog.json")
TITLES_DB = os.path.join(DBDIR, "titles.db")
BASE = "https://datasets.imdbws.com/"
MAXAGE = 7 * 86400
HOME_N = 16
_state = {"building": False, "error": ""}
_lock = threading.Lock()
_mem = {"t": 0, "cat": None, "titles": {}}
_pending = set()
_resolver = None

KINDS = {"movie": ("movie",), "series": ("tvSeries", "tvMiniSeries")}
MIN_VOTES = {"movie": 60000, "series": 30000}

def _rules(kind, this):
    g = lambda i, *gs: any(x in i["g"] for x in gs)
    a = lambda i, *gs: all(x in i["g"] for x in gs)
    W, V = "w", "v"
    R = []
    def add(id_, name, fn, sort=W, home=False, pool="main"):
        R.append({"id": id_, "name": name, "fn": fn, "sort": sort, "home": home, "pool": pool})
    add("new", "New and notable", lambda i: i["y"] and i["y"] >= this - 1, V, True)
    add("top", "Top rated", lambda i: True, W, True)
    add("popular", "Popular", lambda i: i["y"] and i["y"] >= this - 15, V, True)
    add("gems", "Hidden gems", lambda i: i["r"] >= 7.8 and 35000 <= i["v"] <= 160000, W, True, "all")
    if kind == "movie":
        add("mind", "Mind-benders", lambda i: i["r"] >= 7.3 and ((a(i, "Thriller") and g(i, "Mystery", "Sci-Fi")) or a(i, "Sci-Fi", "Mystery")), W, True)
        add("feel", "Feel-good", lambda i: i["r"] >= 7.0 and (g(i, "Family") or (a(i, "Comedy") and g(i, "Romance", "Music"))), W, True)
        add("edge", "Edge of your seat", lambda i: i["r"] >= 7.2 and a(i, "Thriller") and g(i, "Crime", "Mystery", "Horror"), W, True)
        add("family", "Family night", lambda i: i["v"] >= 80000 and g(i, "Family", "Animation"))
        add("true", "Based on true stories", lambda i: i["r"] >= 7.3 and g(i, "Biography", "History"))
        add("short", "Short and sweet (under 95 min)", lambda i: i["rt"] and i["rt"] <= 95 and i["r"] >= 7.3)
        add("classics", "Classics", lambda i: i["y"] and i["y"] < 1985)
        add("nineties", "The 90s", lambda i: i["y"] and 1990 <= i["y"] <= 1999)
        add("noughties", "The 2000s", lambda i: i["y"] and 2000 <= i["y"] <= 2009)
        for gname, label in (("Action", "Action"), ("Comedy", "Comedy"), ("Drama", "Drama"), ("Sci-Fi", "Sci-Fi"), ("Horror", "Horror"),
                             ("Animation", "Animation"), ("Crime", "Crime"), ("Romance", "Romance"), ("Fantasy", "Fantasy"), ("War", "War"), ("Music", "Music")):
            add("g-" + gname.lower(), label, (lambda gn: lambda i: gn in i["g"])(gname))
    else:
        add("binge", "Binge-worthy", lambda i: i["r"] >= 8.2 and i["v"] >= 100000, V, True)
        add("mini", "Miniseries", lambda i: i["tt"] == "tvMiniSeries", W, True)
        add("mind", "Mind-bending", lambda i: i["r"] >= 7.8 and g(i, "Sci-Fi", "Mystery", "Fantasy"), W, True)
        add("sitcom", "Sitcoms", lambda i: i["rt"] and i["rt"] <= 35 and a(i, "Comedy"))
        add("prestige", "Prestige drama", lambda i: i["r"] >= 8.3 and a(i, "Drama"))
        add("truecrime", "True crime", lambda i: a(i, "Crime", "Documentary"))
        add("docu", "Docuseries", lambda i: a(i, "Documentary"))
        add("anim", "Animated", lambda i: a(i, "Animation"))
        add("family", "Family", lambda i: a(i, "Family"))
        add("action", "Action and adventure", lambda i: g(i, "Action", "Adventure"))
        add("scifi", "Sci-Fi and fantasy", lambda i: g(i, "Sci-Fi", "Fantasy"))
        add("crime", "Crime", lambda i: a(i, "Crime"))
        add("comedy", "Comedy", lambda i: a(i, "Comedy"))
        add("drama", "Drama", lambda i: a(i, "Drama"))
        add("reality", "Reality", lambda i: a(i, "Reality-TV"))
    return R

def _download(name, dest):
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "upload-meta"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)

def _rows(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        next(f)
        for line in f:
            yield line.rstrip("\n").split("\t")

def _build():
    os.makedirs(DBDIR, exist_ok=True)
    rp, bp = os.path.join(DBDIR, "ratings.tsv.gz"), os.path.join(DBDIR, "basics.tsv.gz")
    _download("title.ratings.tsv.gz", rp)
    ratings = {}
    for r in _rows(rp):
        try:
            v = int(r[2])
        except ValueError:
            continue
        if v >= 20000: ratings[r[0]] = (float(r[1]), v)
    _download("title.basics.tsv.gz", bp)
    items = {}
    want = {t: k for k, ts in KINDS.items() for t in ts}
    for r in _rows(bp):
        if r[0] in ratings and r[1] in want and r[4] == "0":
            rt, v = ratings[r[0]]
            items[r[0]] = {"id": r[0], "k": want[r[1]], "tt": r[1], "n": r[2], "y": int(r[5]) if r[5].isdigit() else None,
                           "rt": int(r[7]) if r[7].isdigit() else None, "g": [] if r[8] == "\\N" else r[8].split(","), "r": rt, "v": v}
    os.remove(bp); os.remove(rp)
    this = date.today().year
    out = {"built": int(time.time()), "items": {}, "rows": {"movie": [], "series": []}}
    for kind in KINDS:
        everyone = [i for i in items.values() if i["k"] == kind]
        main = [i for i in everyone if i["v"] >= MIN_VOTES[kind]]
        C, m = 6.8, MIN_VOTES[kind] * 2
        for i in everyone: i["w"] = (i["v"] / (i["v"] + m)) * i["r"] + (m / (i["v"] + m)) * C
        for rule in _rules(kind, this):
            pool = everyone if rule["pool"] == "all" else main
            lst = sorted([i for i in pool if rule["fn"](i)], key=lambda i: -i[rule["sort"]])[:60]
            if len(lst) < 8: continue
            out["rows"][kind].append({"id": rule["id"], "name": rule["name"], "home": rule["home"], "ids": [i["id"] for i in lst]})
            for i in lst: out["items"][i["id"]] = {k: v for k, v in i.items() if k not in ("w", "tt")}
    tmp = CAT + ".tmp"
    with open(tmp, "w") as f: json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, CAT)

def _titles():
    c = sqlite3.connect(TITLES_DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS titles(id TEXT PRIMARY KEY, img TEXT, overview TEXT)")
    return c

def _read():
    try:
        with open(CAT) as f: return json.load(f)
    except Exception:
        return None

def load():
    """Catalogue dict or None; starts a background build when missing or older than a week."""
    with _lock:
        if time.time() - _mem["t"] > 30:
            _mem["cat"] = _read()
            try:
                with _titles() as c: _mem["titles"] = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id,img,overview FROM titles")}
            except Exception:
                pass
            _mem["t"] = time.time()
        cat = _mem["cat"]
        if (cat is None or time.time() - cat["built"] > MAXAGE) and not _state["building"]:
            _state["building"] = True
            threading.Thread(target=_background, daemon=True).start()
        return cat

def _background():
    try:
        try: os.nice(10)
        except OSError: pass
        _build()
        with _lock:
            _mem["cat"] = _read(); _mem["t"] = time.time()
        cat = _mem["cat"]
        if cat:
            first = {}
            for kind in KINDS:
                for r in cat["rows"][kind]:
                    if r["home"]:
                        for i in r["ids"][:HOME_N]: first[i] = cat["items"][i]
            queue_resolve(list(first.values()))
    except Exception as e:
        _state["error"] = str(e)[:200]
    finally:
        _state["building"] = False

def set_resolver(fn):
    global _resolver; _resolver = fn

def queue_resolve(items):
    """Fetch posters/plots for titles we don't have yet, in the background (never blocks a page load)."""
    todo = [i for i in items if i["id"] not in _mem["titles"] and i["id"] not in _pending]
    if not todo or not _resolver: return
    for i in todo: _pending.add(i["id"])
    def run():
        try: _resolver(todo)
        finally:
            for i in todo: _pending.discard(i["id"])
    threading.Thread(target=run, daemon=True).start()

def save_title(i, img, overview):
    with _titles() as c: c.execute("INSERT OR REPLACE INTO titles VALUES(?,?,?)", (i, img or "", overview or ""))
    _mem["titles"][i] = (img or "", overview or "")

def known(i):
    return _mem["titles"].get(i)

def _dress(cat, i):
    it = dict(cat["items"][i]); k = known(i)
    if k: it["img"], it["o"] = k
    return it

def view(kind, row=None):
    cat = load()
    if not cat:
        return {"building": True, "error": _state["error"], "rows": [], "chips": []}
    rows = cat["rows"][kind]
    chips = [{"id": r["id"], "name": r["name"], "home": r["home"]} for r in rows]
    if row:
        r = next((r for r in rows if r["id"] == row), None)
        if not r: return {"rows": [], "chips": chips}
        its = [_dress(cat, i) for i in r["ids"]]
        queue_resolve([cat["items"][i] for i in r["ids"]])
        return {"rows": [{"id": r["id"], "name": r["name"], "items": its}], "chips": chips, "ready": all("img" in x for x in its)}
    out = []
    for r in rows:
        if not r["home"]: continue
        ids = r["ids"][:HOME_N]
        out.append({"id": r["id"], "name": r["name"], "items": [_dress(cat, i) for i in ids]})
    queue_resolve([cat["items"][i] for r in rows if r["home"] for i in r["ids"][:HOME_N]])
    total = sum(len(r["items"]) for r in out)
    return {"building": _state["building"], "ready": sum(1 for r in out for x in r["items"] if "img" in x) >= total * 0.95, "rows": out, "chips": chips}

def item(i):
    cat = load()
    if not cat or i not in cat["items"]: return None
    return _dress(cat, i)

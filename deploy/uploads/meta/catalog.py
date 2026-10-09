"""Browsable catalogue for the request page, built from IMDb's free daily datasets (ratings, titles, genres, runtime).
Posters and plot summaries come from Jellyfin's own TMDb lookup (by IMDb id) and are cached in SQLite, so the page loads fast.
Everything lives in /db. Rebuilt weekly in the background at low priority.

Shelves are rules over that data ("hidden gems", "mind-benders", "short and sweet", ...), shown on the home view; every
shelf can also be opened on its own with up to 60 titles."""
import gzip, json, os, random, sqlite3, threading, time, urllib.parse, urllib.request
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
_failed = {}
_resolver = None
_bulk = {"running": False, "t": 0}

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
        add("mind", "Mind-benders", lambda i: i["r"] >= 7.0 and ((a(i, "Thriller") and g(i, "Mystery", "Sci-Fi")) or a(i, "Sci-Fi", "Mystery") or (a(i, "Drama") and g(i, "Mystery") and g(i, "Sci-Fi", "Thriller"))), W, True, "all")
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
    out = {"built": int(time.time()), "items": {}, "rows": {"movie": [], "series": []}, "rowids": []}
    for kind in KINDS:
        everyone = [i for i in items.values() if i["k"] == kind]
        main = [i for i in everyone if i["v"] >= MIN_VOTES[kind]]
        C, m = 6.8, MIN_VOTES[kind] * 2
        for i in everyone: i["w"] = (i["v"] / (i["v"] + m)) * i["r"] + (m / (i["v"] + m)) * C
        for rule in _rules(kind, this):
            pool = everyone if rule["pool"] == "all" else main
            lst = sorted([i for i in pool if rule["fn"](i)], key=lambda i: -i[rule["sort"]])[:300]
            if len(lst) < 8: continue
            out["rows"][kind].append({"id": rule["id"], "name": rule["name"], "home": rule["home"], "by": "rating" if rule["sort"] == "w" else "votes", "ids": [i["id"] for i in lst]})
            out["rowids"].extend(i["id"] for i in lst)
    for i in items.values():
        if "w" in i: out["items"][i["id"]] = {k: (round(v, 3) if k == "w" else v) for k, v in i.items() if k != "tt"}
    out["rowids"] = list(dict.fromkeys(out["rowids"]))
    tmp = CAT + ".tmp"
    with open(tmp, "w") as f: json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, CAT)

GROUPS = [("FR", "French cinema", ["Q142"]), ("ES", "Spanish cinema", ["Q29", "Q96", "Q414", "Q739", "Q298"]),
          ("IT", "Italian cinema", ["Q38"]), ("DE", "German cinema", ["Q183"]), ("JP", "Japanese cinema", ["Q17"]),
          ("KR", "Korean cinema", ["Q884"]), ("IN", "Indian cinema", ["Q668"])]
COUNTRIES_F = os.path.join(DBDIR, "countries.json")
_cty = {"t": 0, "d": None, "building": False, "fail": 0}

def _wikidata(cls, qids):
    q = "SELECT DISTINCT ?imdb WHERE { VALUES ?c { %s } ?f wdt:P31 wd:%s; wdt:P495 ?c; wdt:P345 ?imdb. }" % (" ".join("wd:" + x for x in qids), cls)
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={"User-Agent": "lumio-catalogue/1.0 (self-hosted media catalogue)", "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=150) as r:
        d = json.load(r)
    return [b["imdb"]["value"] for b in d["results"]["bindings"] if str(b["imdb"]["value"]).startswith("tt")]

def _build_countries():
    cat = _mem["cat"] or _read()
    if not cat: return
    have = set(cat["items"])
    out = {"built": int(time.time())}
    ok = tried = 0
    for code, label, qids in GROUPS:
        out[code] = {}
        for kind, cls in (("movie", "Q11424"), ("series", "Q5398426")):
            ids = []
            tried += 1
            for attempt in range(3):
                try:
                    ids = [i for i in _wikidata(cls, qids) if i in have]; ok += 1; break
                except Exception:
                    time.sleep(10 * (attempt + 1))
            out[code][kind] = ids
            time.sleep(3)
    if ok < tried * 0.7:            # most queries failed (network, rate limit): keep the old file and try again later
        _cty["fail"] = time.time()
        return
    tmp = COUNTRIES_F + ".tmp"
    with open(tmp, "w") as f: json.dump(out, f, separators=(",", ":"))
    os.replace(tmp, COUNTRIES_F)
    _cty["t"] = 0

def _countries_job():
    try:
        try: os.nice(10)
        except OSError: pass
        _build_countries()
    finally:
        _cty["building"] = False

def countries():
    """{code: {kind: set(ids)}} or None while it is still being collected (started in the background when missing/old)."""
    if time.time() - _cty["t"] > 120:
        _cty["t"] = time.time()
        try:
            with open(COUNTRIES_F) as f: raw = json.load(f)
            _cty["d"] = {c: {k: set(v) for k, v in raw[c].items()} for c in raw if c != "built"}
            old = time.time() - raw.get("built", 0) > MAXAGE
        except Exception:
            _cty["d"] = None; old = True
        if old and not _cty["building"] and _mem["cat"] and time.time() - _cty["fail"] > 900:
            _cty["building"] = True
            threading.Thread(target=_countries_job, daemon=True).start()
    return _cty["d"]

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
            try: mt = os.path.getmtime(CAT)
            except OSError: mt = 0
            if mt != _mem.get("mt") or _mem["cat"] is None:      # re-read the (large) catalogue file only when it changed
                _mem["cat"] = _read(); _mem["mt"] = mt
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
            ensure_all()
            countries()
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
    now = time.time()
    todo = [i for i in items if i["id"] not in _mem["titles"] and i["id"] not in _pending and now - _failed.get(i["id"], 0) > 1800]
    if not todo or not _resolver: return
    for i in todo: _pending.add(i["id"])
    def run():
        try: _resolver(todo)
        finally:
            for i in todo: _pending.discard(i["id"])
    threading.Thread(target=run, daemon=True).start()

def _bulk_run():
    try:
        try: os.nice(10)
        except OSError: pass
        cat = _mem["cat"]
        if not cat: return
        order, seen = [], set()
        def add(i):
            if i not in seen: seen.add(i); order.append(cat["items"][i])
        for kind in KINDS:                                   # first what the home screen shows, then every category in turn
            for r in cat["rows"][kind]:
                if r["home"]:
                    for i in r["ids"][:HOME_N]: add(i)
        for kind in KINDS:
            for r in cat["rows"][kind]:
                for i in r["ids"]: add(i)
        for k in range(0, len(order), 40):
            chunk = [i for i in order[k:k + 40] if i["id"] not in _mem["titles"] and time.time() - _failed.get(i["id"], 0) > 1800]
            if chunk and _resolver: _resolver(chunk)
    finally:
        _bulk["running"] = False

def ensure_all():
    """Make sure every title in the catalogue gets a poster, a few at a time, in the background."""
    if _bulk["running"] or time.time() - _bulk["t"] < 60 or not _mem["cat"]: return
    _bulk["t"] = time.time()
    if all(i in _mem["titles"] for i in _mem["cat"].get("rowids", [])): return
    _bulk["running"] = True
    threading.Thread(target=_bulk_run, daemon=True).start()

def save_guess(key, img):
    with _titles() as c:
        c.execute("CREATE TABLE IF NOT EXISTS guesses(key TEXT PRIMARY KEY, img TEXT)")
        c.execute("INSERT OR REPLACE INTO guesses VALUES(?,?)", (key, img or ""))

def get_guess(key):
    try:
        with _titles() as c:
            c.execute("CREATE TABLE IF NOT EXISTS guesses(key TEXT PRIMARY KEY, img TEXT)")
            r = c.execute("SELECT img FROM guesses WHERE key=?", (key,)).fetchone()
            return None if r is None else r[0]
    except Exception:
        return None

def save_title(i, img, overview):
    with _titles() as c: c.execute("INSERT OR REPLACE INTO titles VALUES(?,?,?)", (i, img or "", overview or ""))
    _mem["titles"][i] = (img or "", overview or "")

def known(i):
    return _mem["titles"].get(i)

def _dress(cat, i):
    it = dict(cat["items"][i]); k = known(i)
    if k: it["img"], it["o"] = k
    return it

SORTS = {"rating": lambda i: (-i["r"], -i["v"]), "votes": lambda i: (-i["v"], -i["r"]),
         "newest": lambda i: (-(i["y"] or 0), -i["v"]), "name": lambda i: (i["n"].lower(),)}

def _ordered(cat, ids, sort):
    return sorted((cat["items"][i] for i in ids), key=SORTS[sort])

def view(kind, row=None, sort=None, offset=0, limit=40):
    cat = load()
    ensure_all()
    if not cat:
        return {"building": True, "error": _state["error"], "rows": [], "chips": []}
    rows = cat["rows"][kind]
    chips = [{"id": r["id"], "name": r["name"], "home": r["home"]} for r in rows]
    if row:
        r = next((r for r in rows if r["id"] == row), None)
        if not r: return {"rows": [], "chips": chips}
        sort = sort if sort in SORTS else r.get("by", "rating")
        allit = _ordered(cat, r["ids"], sort)
        page = allit[offset:offset + limit]
        queue_resolve(page)
        its = [_dress(cat, i["id"]) for i in page]
        return {"rows": [{"id": r["id"], "name": r["name"], "items": its}], "chips": chips, "total": len(allit), "offset": offset,
                "sort": sort, "default": r.get("by", "rating"), "ready": all("img" in x for x in its)}
    out = []
    for r in rows:
        if not r["home"]: continue
        # the shelf is the top of the category; show it ordered by what it is about (rating, or popularity for "Popular")
        top = _ordered(cat, r["ids"][:HOME_N], r.get("by", "rating"))
        out.append({"id": r["id"], "name": r["name"], "items": [_dress(cat, i["id"]) for i in top]})
    queue_resolve([cat["items"][i] for r in rows if r["home"] for i in r["ids"][:HOME_N]])
    total = sum(len(r["items"]) for r in out)
    return {"building": _state["building"], "ready": sum(1 for r in out for x in r["items"] if "img" in x) >= total * 0.95, "rows": out, "chips": chips}

FSORTS = dict(SORTS)
FSORTS["best"] = lambda i: (-i.get("w", 0), -i["v"])

def _filter_pool(kind, f):
    cat = load()
    if not cat: return None, []
    ids = None
    if f.get("country"):
        cd = countries()
        if cd is None or f["country"] not in cd: return cat, []
        ids = cd[f["country"]].get(kind, set())
    dec = f.get("decade")
    out = []
    for i, it in cat["items"].items():
        if it["k"] != kind or (ids is not None and i not in ids): continue
        if f.get("genre") and f["genre"] not in it["g"]: continue
        if dec and not (it["y"] and dec <= it["y"] <= dec + 9): continue
        if f.get("min") and it["r"] < f["min"]: continue
        out.append(it)
    return cat, out

def view_filter(kind, f, sort=None, offset=0, limit=40):
    cat, pool = _filter_pool(kind, f)
    if cat is None: return {"building": True, "rows": [], "chips": []}
    sort = sort if sort in FSORTS else "best"
    pool.sort(key=FSORTS[sort])
    page = pool[offset:offset + limit]
    queue_resolve(page)
    its = [_dress(cat, i["id"]) for i in page]
    return {"rows": [{"id": "filter", "name": "Results", "items": its}], "total": len(pool), "offset": offset, "sort": sort, "default": "best",
            "ready": all("img" in x for x in its), "countries_ready": countries() is not None}

def lucky(kind, f):
    cat, pool = _filter_pool(kind, f)
    if not pool: return None
    good = [i for i in pool if i["r"] >= 7.0 and i["v"] >= 40000] or pool
    it = random.choice(good)
    return it["id"]

def filters_info(kind):
    cat = load()
    if not cat: return {"countries": [], "genres": [], "decades": []}
    gs = {}
    for it in cat["items"].values():
        if it["k"] == kind:
            for g in it["g"]: gs[g] = gs.get(g, 0) + 1
    cd = countries()
    return {"countries": [{"id": c, "name": n} for c, n, _ in GROUPS if cd and c in cd and cd[c].get(kind)],
            "genres": sorted(g for g, n in gs.items() if n >= 40), "decades": list(range(2020, 1909, -10))}

def item(i):
    cat = load()
    if not cat or i not in cat["items"]: return None
    return _dress(cat, i)

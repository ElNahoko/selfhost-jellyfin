"""Browsable catalogue for the request page, built from IMDb's free daily datasets (ratings, titles, genres, runtime).
Posters and plot summaries come from Jellyfin's own TMDb lookup (by IMDb id) and are cached in SQLite, so the page loads fast.
Everything lives in /db. Rebuilt weekly in the background at low priority.

Shelves are rules over that data ("hidden gems", "mind-benders", "short and sweet", ...), shown on the home view; every
shelf can also be opened on its own with up to 60 titles."""
import gzip, json, math, os, random, sqlite3, threading, time, urllib.error, urllib.parse, urllib.request
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

# "French cinema" means films whose ORIGINAL LANGUAGE is French (so English-language co-productions are not mixed in);
# for films without a language on Wikidata the country of origin is used. Country-only groups exclude English-language films.
GROUPS = [("FR", "French cinema", {"langs": ["Q150"], "countries": ["Q142"]}),
          ("ES", "Spanish cinema", {"langs": ["Q1321"], "countries": ["Q29", "Q96", "Q414"]}),
          ("IT", "Italian cinema", {"langs": ["Q652"], "countries": ["Q38"]}),
          ("DE", "German cinema", {"langs": ["Q188"], "countries": ["Q183"]}),
          ("JP", "Japanese cinema", {"langs": ["Q5287"], "countries": ["Q17"]}),
          ("KR", "Korean cinema", {"langs": ["Q9176"], "countries": ["Q884"]}),
          ("IN", "Indian cinema", {"langs": ["Q1568", "Q5885", "Q8097", "Q36236", "Q33673", "Q9610"], "countries": ["Q668"]}),
          ("BE", "Belgian cinema", {"langs": [], "countries": ["Q31"]}),
          ("BR", "Brazilian cinema", {"langs": [], "countries": ["Q155"]}),
          ("SE", "Scandinavian cinema", {"langs": [], "countries": ["Q34", "Q35", "Q20", "Q33"]}),
          ("CN", "Chinese cinema", {"langs": [], "countries": ["Q148", "Q8646", "Q865"]}),
          ("IR", "Iranian cinema", {"langs": [], "countries": ["Q794"]}),
          ("TR", "Turkish cinema", {"langs": [], "countries": ["Q43"]})]
COUNTRIES_F = os.path.join(DBDIR, "countries.json")
_cty = {"t": 0, "d": None, "building": False, "fail": 0}

def _wikidata_pair(spec):
    """Films and series of a "cinema" group (Wikidata, free). One request returns both kinds: {"movie": [...], "series": [...]}.
    Wikidata allows about one request a minute for us, so a 429 is waited out (Retry-After) instead of hammered."""
    parts = []
    if spec["langs"]:      # by original language: fast, and keeps English-language co-productions out
        parts.append("{ VALUES ?lang { %s } ?f wdt:P364 ?lang. }" % " ".join("wd:" + x for x in spec["langs"]))
    else:                  # by country of origin, English-language films excluded
        parts.append("{ VALUES ?c { %s } ?f wdt:P495 ?c. FILTER NOT EXISTS { ?f wdt:P364 wd:Q1860 } }" % " ".join("wd:" + x for x in spec["countries"]))
    q = "SELECT DISTINCT ?imdb ?cls WHERE { VALUES ?cls { wd:Q11424 wd:Q5398426 } ?f wdt:P31 ?cls; wdt:P345 ?imdb. %s }" % " UNION ".join(parts)
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    last = None
    for attempt in range(8):
        req = urllib.request.Request(url, headers={"User-Agent": "lumio-catalogue/1.0 (self-hosted media catalogue)", "Accept": "application/sparql-results+json"})
        try:
            with urllib.request.urlopen(req, timeout=170) as r:
                d = json.load(r)
            out = {"movie": [], "series": []}
            for b in d["results"]["bindings"]:
                i = b["imdb"]["value"]
                if i.startswith("tt"): out["movie" if b["cls"]["value"].endswith("Q11424") else "series"].append(i)
            return out
        except urllib.error.HTTPError as e:
            last = e
            wait = 65
            try: wait = max(wait, min(int(e.headers.get("Retry-After", "0")), 1500) + 5)
            except ValueError: pass
            if e.code not in (429, 503) and attempt >= 1: break          # a query that keeps failing is skipped, not retried for ever
            time.sleep(wait if e.code in (429, 503) else 30)
        except Exception as e:
            last = e
            time.sleep(30)
    raise last

def _build_countries():
    cat = _mem["cat"] or _read()
    if not cat: return
    have = set(cat["items"])
    out = {"built": int(time.time()), "complete": False}
    ok = 0
    try:      # carry on from a run that was interrupted a short while ago (restart, rate limit)
        with open(COUNTRIES_F) as f: prev = json.load(f)
        if not prev.get("complete") and time.time() - prev.get("built", 0) < 3600:
            out["built"] = prev["built"]
            out.update({c: v for c, v in prev.items() if c not in ("built", "complete") and (v.get("movie") or v.get("series"))})
    except Exception:
        pass
    for n, (code, label, spec) in enumerate(GROUPS):
        if code in out:
            ok += 1
            continue
        try:
            pair = _wikidata_pair(spec)
            out[code] = {k: [i for i in v if i in have] for k, v in pair.items()}
            ok += 1
        except Exception:
            out[code] = {"movie": [], "series": []}
        out["complete"] = (n == len(GROUPS) - 1) and ok == len(GROUPS)
        tmp = COUNTRIES_F + ".tmp"      # saved after every country, so the first ones (French, Spanish) show up early
        with open(tmp, "w") as f: json.dump(out, f, separators=(",", ":"))
        os.replace(tmp, COUNTRIES_F); _cty["t"] = 0
        time.sleep(62)
    if not out["complete"]: _cty["fail"] = time.time()      # try again in 15 minutes

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
            _cty["d"] = {c: {k: set(v) for k, v in raw[c].items()} for c in raw if c not in ("built", "complete")}
            old = time.time() - raw.get("built", 0) > MAXAGE or not raw.get("complete", True)
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

def get_trailer(key):
    try:
        with _titles() as c:
            c.execute("CREATE TABLE IF NOT EXISTS trailers(key TEXT PRIMARY KEY, vid TEXT, title TEXT, ts INTEGER)")
            r = c.execute("SELECT vid, title, ts FROM trailers WHERE key=?", (key,)).fetchone()
            return r
    except Exception:
        return None

def save_trailer(key, vid, title):
    with _titles() as c:
        c.execute("CREATE TABLE IF NOT EXISTS trailers(key TEXT PRIMARY KEY, vid TEXT, title TEXT, ts INTEGER)")
        c.execute("INSERT OR REPLACE INTO trailers VALUES(?,?,?,?)", (key, vid or "", title or "", int(time.time())))

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

def _byname(cat):
    ix = _mem.get("byname")
    if ix is None or ix[0] is not cat:
        _mem["byname"] = ix = (cat, {(it["n"].lower(), it["y"]): it for it in cat["items"].values()})
    return ix[1]

def genres_for(pairs):
    """Genre counts for (title, year) pairs we know (the library, what somebody requested): the household's taste."""
    cat = load()
    if not cat: return {}
    ix, cnt = _byname(cat), {}
    for n, y in pairs:
        it = ix.get((n, y))
        if it:
            for g in it["g"]: cnt[g] = cnt.get(g, 0) + 1
    return cnt

def lucky(kind, f, exclude=(), taste=None, seen=(), fresh=True):
    """One good title to watch, picked by weighted chance:
       quality (the vote-weighted rating)  x  freshness (new releases win unless a decade was chosen or 'Any era')
       x  popularity (so it is not obscure)  x  taste (genres the library and the requests lean towards).
       Titles already in the library, already requested or already shown in this session are skipped."""
    cat, pool = _filter_pool(kind, f)
    if not pool: return None, []
    this = date.today().year
    taste = taste or {}
    top = max(taste.values()) if taste else 0
    use_fresh = fresh and not f.get("decade")
    def candidates(strict):
        out = []
        for it in pool:
            if it["id"] in seen: continue
            if strict and (it["n"].lower(), it["y"]) in exclude: continue
            if it["r"] < 6.6 or it["v"] < 20000: continue
            q = max(0.05, it.get("w", it["r"]) - 6.3)
            age = max(0, this - (it["y"] or this - 30))
            rec = (0.12 + 0.88 * math.exp(-age / 4.5)) if use_fresh else 1.0
            pop = math.log10(max(it["v"], 10)) / 6.0
            aff = sum(taste.get(g, 0) for g in it["g"]) / (top * max(1, len(it["g"]))) if top else 0
            out.append((it, q ** 1.6 * rec * pop * (1 + 0.9 * aff), aff))
        return out
    cands = candidates(True) or candidates(False)
    if not cands: return None, []
    it, _, aff = random.choices(cands, weights=[c[1] for c in cands], k=1)[0]
    why = []
    age = this - (it["y"] or this)
    if age <= 1: why.append("New release")
    elif age <= 4: why.append("Recent")
    if it["r"] >= 8.0: why.append("Critics and fans love it")
    elif it["r"] >= 7.4: why.append("Highly rated")
    if it["v"] < 90000 and it["r"] >= 7.5: why.append("Hidden gem")
    if aff >= 0.45 and top:
        mg = [g for g in sorted(it["g"], key=lambda g: -taste.get(g, 0)) if taste.get(g, 0) >= top * 0.5][:1]
        if mg: why.append("Matches your taste: " + mg[0])
    return it["id"], why[:3]

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

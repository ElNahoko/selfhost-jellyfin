"""Browsable catalogue for the request page, built from IMDb's free daily datasets (ratings, titles, genres, runtime).
Posters and plot summaries come from Jellyfin's own TMDb lookup (by IMDb id) and are cached in SQLite, so the page loads fast.
Everything lives in /db. Rebuilt weekly in the background at low priority.

Shelves are rules over that data ("hidden gems", "mind-benders", "short and sweet", ...), shown on the home view; every
shelf can also be opened on its own with up to 60 titles."""
import difflib, gzip, json, math, os, random, re, sqlite3, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import date

DBDIR = os.environ.get("DB_DIR", "/db")
CAT = os.path.join(DBDIR, "catalog.json")
TITLES_DB = os.path.join(DBDIR, "titles.db")
BASE = "https://datasets.imdbws.com/"
MAXAGE = 7 * 86400
SCHEMA = 8      # bump to make the next start rebuild the catalogue in the background (the old one keeps being served meanwhile)
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

ANIME_F = os.path.join(DBDIR, "anime.json")
_anime = {"s": set(), "mt": 0, "building": False, "t": 0}

def _anime_set():
    try:
        mt = os.path.getmtime(ANIME_F)
        if mt != _anime["mt"]:
            _anime["s"] = set(json.load(open(ANIME_F))); _anime["mt"] = mt
    except (OSError, ValueError):
        pass
    return _anime["s"]

_JP_SCRIPT = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_ROMAJI = re.compile(r"[\u00e2\u00ee\u00fb\u00f4\u00ea]|(^|\s)(no|wa)(\s|$|:)", re.I)   # "Tonari no Totoro", "Shippûden"
_OTHER = {"FR", "ES", "DE", "IT", "BR", "PT", "CN", "TR", "CZ", "HU", "PL", "RU", "AR", "MX"}   # regions that list their own originals
_NA = "\\N"

def _norm(t): return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

def anime_verdict(orig, us, other_same, cty):
    """Is this animated title Japanese? Its original title (IMDb) against its English one, with the country lists as a check."""
    if "JP" in cty: return True
    if cty: return False                              # Wikidata says French, Korean, Chinese, ...
    if _JP_SCRIPT.search(orig): return True
    if not orig: return False
    o, u = _norm(orig), _norm(us)
    if not o or o == u: return False                  # the original title is the English one
    if _ROMAJI.search(orig): return True
    if u and (u in o or o in u or difflib.SequenceMatcher(None, o, u).ratio() >= .8): return False   # "Tim Burton's The Nightmare ..."
    if other_same: return False
    return not (set(o.split()) & {"the", "and", "of", "a", "an", "in", "on", "with", "for", "from", "my", "your", "little", "big"})

def anime_scan(path, anim, cty_of):
    """Streams title.akas: original title, English title and whether another country lists the original, per animated title."""
    orig, us, rows = {}, {}, {}
    for r in _rows(path):
        t = r[0]
        if t not in anim: continue
        if r[7] == "1": orig[t] = r[2]
        elif r[3] == "US" and r[4] in (_NA, "en") and (r[5] == "imdbDisplay" or t not in us): us[t] = r[2]
        elif r[3] in _OTHER: rows.setdefault(t, []).append(r[2])
    out = set()
    for t in anim:
        o = _norm(orig.get(t, ""))
        other = any(_norm(x) == o for x in rows.get(t, ())) if o else False
        if anime_verdict(orig.get(t, ""), us.get(t, ""), other, cty_of(t)): out.add(t)
    return out

def _anime_job():
    """Anime = animated titles that are Japanese. Streams IMDb's akas file (large), low priority, once a week."""
    try:
        try: os.nice(10)
        except OSError: pass
        cat = _mem["cat"] or _read()
        if not cat: return
        anim = {i for i, it in cat["items"].items() if "Animation" in it["g"]}
        ap = os.path.join(DBDIR, "akas.tsv.gz")
        _download("title.akas.tsv.gz", ap)
        cd = _cty.get("d") or {}
        def cty_of(t): return {c for c in cd if any(t in cd[c].get(k, ()) for k in KINDS)}
        found = anime_scan(ap, anim, cty_of)
        os.remove(ap)
        tmp = ANIME_F + ".tmp"
        json.dump(sorted(found), open(tmp, "w")); os.replace(tmp, ANIME_F)
        if found != _anime["s"]: _anime["mt"] = 0; _anime_set(); refresh_rows()
    except Exception as e:
        _state["error"] = ("anime: " + str(e))[:200]
    finally:
        _anime["building"] = False

def ensure_anime():
    cat = _mem["cat"]
    if _anime["building"] or time.time() - _anime["t"] < 600 or not cat or cat.get("schema") != SCHEMA: return
    _anime["t"] = time.time()
    try:
        if os.path.getmtime(ANIME_F) > cat["built"]: return
    except OSError:
        pass
    _anime["building"] = True
    threading.Thread(target=_anime_job, daemon=True).start()

def _rules(kind, this):
    g = lambda i, *gs: any(x in i["g"] for x in gs)
    a = lambda i, *gs: all(x in i["g"] for x in gs)
    W, V = "w", "v"
    R = []
    def add(id_, name, fn, sort=W, home=False, pool="main"):
        R.append({"id": id_, "name": name, "fn": fn, "sort": sort, "home": home, "pool": pool})
    add("new", "New and notable", lambda i: i["y"] and i["y"] >= this - 1, V, True, "all")
    add("fresh", "Just released", lambda i: i["y"] == this, V, True, "all")
    add("newanime", "Latest anime", lambda i: i["y"] and i["y"] >= this - 1 and i["id"] in _anime_set(), V, True, "all")
    add("top", "Top rated", lambda i: True, W, True)
    add("popular", "Popular", lambda i: i["y"] and i["y"] >= this - 15, V, True, "all")
    add("anime", "Anime", lambda i: i["id"] in _anime_set(), W, True, "all")
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
        add("mini", "Miniseries", lambda i: i.get("tt") == "tvMiniSeries", W, True)
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

def _make_rows(items, this):
    """The shelves: every rule applied to the titles of its kind (the "main" pool needs many votes, "all" takes everything)."""
    rows, rowids = {"movie": [], "series": []}, []
    for kind in KINDS:
        everyone = [i for i in items.values() if i["k"] == kind]
        main = [i for i in everyone if i["v"] >= MIN_VOTES[kind]]
        for rule in _rules(kind, this):
            pool = everyone if rule["pool"] == "all" else main
            lst = sorted([i for i in pool if rule["fn"](i)], key=lambda i: -i[rule["sort"]])[:2000]
            if len(lst) < 8: continue
            rows[kind].append({"id": rule["id"], "name": rule["name"], "home": rule["home"], "by": "rating" if rule["sort"] == "w" else "votes", "ids": [i["id"] for i in lst]})
            rowids.extend(i["id"] for i in lst)
    return rows, list(dict.fromkeys(rowids))

def refresh_rows():
    """Recomputes the shelves from the catalogue in memory (after the anime list changed, for example) and saves them."""
    cat = _mem["cat"]
    if not cat: return
    with _lock:
        cat["rows"], cat["rowids"] = _make_rows(cat["items"], date.today().year)
        tmp = CAT + ".tmp"
        with open(tmp, "w") as f: json.dump(cat, f, separators=(",", ":"))
        os.replace(tmp, CAT)
    _cnt["t"] = 0

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
        if v >= 500: ratings[r[0]] = (float(r[1]), v)
    _download("title.basics.tsv.gz", bp)
    items = {}
    want = {t: k for k, ts in KINDS.items() for t in ts}
    this = date.today().year
    for r in _rows(bp):
        if r[0] in ratings and r[1] in want and r[4] == "0":
            rt, v = ratings[r[0]]
            anim, recent = "Animation" in r[8], r[5].isdigit() and int(r[5]) >= this - 1
            if want[r[1]] == "movie": need = (500 if anim else 1000) if recent else (2000 if anim else 5000)
            else: need = (200 if anim else 400) if recent else (800 if anim else 2000)
            if v < need: continue
            items[r[0]] = {"id": r[0], "k": want[r[1]], "tt": r[1], "n": r[2], "y": int(r[5]) if r[5].isdigit() else None,
                           "rt": int(r[7]) if r[7].isdigit() else None, "g": [] if r[8] == "\\N" else r[8].split(","), "r": rt, "v": v}
    os.remove(bp); os.remove(rp)
    out = {"built": int(time.time()), "schema": SCHEMA, "items": {}, "rows": {"movie": [], "series": []}, "rowids": []}
    for kind in KINDS:
        everyone = [i for i in items.values() if i["k"] == kind]
        C, m = 6.8, MIN_VOTES[kind] * 2
        for i in everyone: i["w"] = (i["v"] / (i["v"] + m)) * i["r"] + (m / (i["v"] + m)) * C
    out["rows"], out["rowids"] = _make_rows(items, this)
    for i in items.values():
        if "w" in i: out["items"][i["id"]] = {k: (round(v, 3) if k == "w" else v) for k, v in i.items()}
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
        parts.append("{ VALUES ?lang { %s } ?f wdt:P364 ?lang. FILTER NOT EXISTS { ?f wdt:P364 wd:Q1860 } }" % " ".join("wd:" + x for x in spec["langs"]))   # not also English
    else:                  # by country of origin, English-language films excluded
        parts.append("{ VALUES ?c { %s } ?f wdt:P495 ?c. FILTER NOT EXISTS { ?f wdt:P364 wd:Q1860 } }" % " ".join("wd:" + x for x in spec["countries"]))
    q = "SELECT DISTINCT ?imdb ?cls WHERE { VALUES ?cls { wd:Q11424 wd:Q5398426 } ?f wdt:P31 ?cls; wdt:P345 ?imdb. %s }" % " UNION ".join(parts)
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    last = None
    for attempt in range(8):
        req = urllib.request.Request(url, headers={"User-Agent": "LumioCatalogue/1.1 (https://github.com/ElNahoko/selfhost-jellyfin; self-hosted media catalogue)", "Accept": "application/sparql-results+json"})
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
    d = _cty["d"]
    if d is not None and _mem["cat"]:
        jp = d.setdefault("JP", {"movie": set(), "series": set()})
        for i in _anime_set():
            it = _mem["cat"]["items"].get(i)
            if it: jp.setdefault(it["k"], set()).add(i)
    return d

EPS_DB = os.path.join(DBDIR, "episodes.db")
_eps = {"building": False, "t": 0}

def _epdb(path=EPS_DB):
    c = sqlite3.connect(path, timeout=30)
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("CREATE TABLE IF NOT EXISTS ep(tconst TEXT PRIMARY KEY, series TEXT, season INTEGER, ep INTEGER, rating REAL, votes INTEGER, title TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS ep_series ON ep(series)")
    c.execute("CREATE TABLE IF NOT EXISTS info(k TEXT PRIMARY KEY, v TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS epx(series TEXT, season INTEGER, ep INTEGER, airdate TEXT, runtime INTEGER, title TEXT, PRIMARY KEY(series, season, ep))")
    c.execute("CREATE TABLE IF NOT EXISTS epx_info(series TEXT PRIMARY KEY, fetched INTEGER, tvmaze INTEGER, status TEXT, premiered TEXT, ended TEXT, runtime INTEGER)")
    return c

def _build_episodes(series_ids):
    """Seasons/episodes of every catalogue series with their ratings and titles. Streams IMDb's files, small memory."""
    os.makedirs(DBDIR, exist_ok=True)
    paths = {n: os.path.join(DBDIR, "e_" + n + ".gz") for n in ("episode", "ratings", "basics")}
    for n, f in (("episode", "title.episode.tsv.gz"), ("ratings", "title.ratings.tsv.gz"), ("basics", "title.basics.tsv.gz")):
        _download(f, paths[n])
    new = EPS_DB + ".new"
    for q in (new, new + "-journal"):
        try: os.remove(q)
        except OSError: pass
    c = _epdb(new)
    wanted, batch = set(series_ids), []
    tconsts = set()
    for r in _rows(paths["episode"]):
        if r[1] in wanted:
            sn = int(r[2]) if r[2].isdigit() else 0; en = int(r[3]) if r[3].isdigit() else 0
            batch.append((r[0], r[1], sn, en)); tconsts.add(r[0])
            if len(batch) >= 5000:
                c.executemany("INSERT OR REPLACE INTO ep(tconst,series,season,ep) VALUES(?,?,?,?)", batch); batch = []
    if batch: c.executemany("INSERT OR REPLACE INTO ep(tconst,series,season,ep) VALUES(?,?,?,?)", batch)
    c.commit()
    batch = []
    for r in _rows(paths["ratings"]):
        if r[0] in tconsts:
            try: batch.append((float(r[1]), int(r[2]), r[0]))
            except ValueError: continue
            if len(batch) >= 5000:
                c.executemany("UPDATE ep SET rating=?, votes=? WHERE tconst=?", batch); batch = []
    if batch: c.executemany("UPDATE ep SET rating=?, votes=? WHERE tconst=?", batch)
    c.commit()
    batch = []
    for r in _rows(paths["basics"]):
        if r[0] in tconsts:
            batch.append((r[2], r[0]))
            if len(batch) >= 5000:
                c.executemany("UPDATE ep SET title=? WHERE tconst=?", batch); batch = []
    if batch: c.executemany("UPDATE ep SET title=? WHERE tconst=?", batch)
    c.execute("INSERT OR REPLACE INTO info VALUES('built', ?)", (str(int(time.time())),))
    try:                                                  # keep the air dates and runtimes already fetched
        c.execute("ATTACH DATABASE ? AS old", (EPS_DB,))
        c.execute("INSERT OR IGNORE INTO epx SELECT * FROM old.epx"); c.execute("INSERT OR IGNORE INTO epx_info SELECT * FROM old.epx_info")
        c.commit(); c.execute("DETACH DATABASE old")
    except sqlite3.Error:
        pass
    c.commit(); c.close()
    os.replace(new, EPS_DB)
    for p in paths.values():
        try: os.remove(p)
        except OSError: pass

def _episodes_job():
    try:
        try: os.nice(10)
        except OSError: pass
        cat = _mem["cat"] or _read()
        if cat: _build_episodes([i for i, it in cat["items"].items() if it["k"] == "series"])
    except Exception as e:
        _state["error"] = ("episodes: " + str(e))[:200]
    finally:
        _eps["building"] = False

def ensure_episodes():
    """Starts the (weekly, low-priority) download of season/episode data when it is missing or old."""
    if _eps["building"] or time.time() - _eps["t"] < 600 or not _mem["cat"]: return
    _eps["t"] = time.time()
    try:
        c = _epdb(); r = c.execute("SELECT v FROM info WHERE k='built'").fetchone(); c.close()
        if r and time.time() - int(r[0]) < MAXAGE and int(r[0]) >= _mem["cat"]["built"]: return
    except Exception:
        pass
    _eps["building"] = True
    threading.Thread(target=_episodes_job, daemon=True).start()

TVMAZE = "https://api.tvmaze.com"
UA = "LumioCatalogue/1.1 (https://github.com/ElNahoko/selfhost-jellyfin; self-hosted media catalogue)"

def _tvmaze(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=8) as r:
        return json.load(r)

def fetch_tvmaze(sid):
    """Air dates, runtimes and titles per episode from TVmaze (free, no key), by IMDb id. Kept in episodes.db; retried daily if missing."""
    c = _epdb()
    row = c.execute("SELECT fetched, tvmaze FROM epx_info WHERE series=?", (sid,)).fetchone()
    if row and time.time() - row[0] < (MAXAGE if row[1] else 86400): c.close(); return
    show, eps = None, []
    try:
        show = _tvmaze(TVMAZE + "/lookup/shows?imdb=" + sid)
        if show: eps = _tvmaze(TVMAZE + "/shows/%d/episodes?specials=1" % show["id"])
    except Exception:
        pass
    if show:
        rt = show.get("averageRuntime") or show.get("runtime")
        c.execute("INSERT OR REPLACE INTO epx_info VALUES(?,?,?,?,?,?,?)", (sid, int(time.time()), show["id"], show.get("status"), show.get("premiered"), show.get("ended"), rt))
        c.execute("DELETE FROM epx WHERE series=?", (sid,))
        c.executemany("INSERT OR REPLACE INTO epx VALUES(?,?,?,?,?,?)",
                      [(sid, e.get("season") or 0, e.get("number") or 0, e.get("airdate") or None, e.get("runtime"), (e.get("name") or "")[:120])
                       for e in eps if e.get("number")])
    else:
        c.execute("INSERT OR REPLACE INTO epx_info VALUES(?,?,?,?,?,?,?)", (sid, int(time.time()), None, None, None, None, None))
    c.commit(); c.close()

def episodes_for(sid):
    """Seasons with episode ratings (IMDb) and air dates / runtimes (TVmaze), plus totals: how long it takes to watch, best episode, next one."""
    try:
        fetch_tvmaze(sid)
        c = _epdb()
        rows = c.execute("SELECT season, ep, rating, votes, title FROM ep WHERE series=? ORDER BY season, ep", (sid,)).fetchall()
        x = {(s, e): (a, r, t) for s, e, a, r, t in c.execute("SELECT season, ep, airdate, runtime, title FROM epx WHERE series=?", (sid,))}
        info = c.execute("SELECT status, premiered, ended, runtime FROM epx_info WHERE series=? AND tvmaze IS NOT NULL", (sid,)).fetchone()
        c.close()
    except Exception:
        return None
    if not rows and not x: return None
    item = (_mem["cat"] or {}).get("items", {}).get(sid) or {}
    show_rt, imdb_rt = (info and info[3]) or None, item.get("rt") or None
    default_rt = imdb_rt or show_rt
    def ep_rt(tv):
        if tv and imdb_rt and show_rt and abs(tv - show_rt) >= 15: return tv      # a special-length episode
        return default_rt
    ep = {(s, e): [e, r, v, t or ""] for s, e, r, v, t in rows}
    for k, (a, r, t) in x.items():
        if k not in ep: ep[k] = [k[1], None, None, t or ""]
    seasons, today = {}, time.strftime("%Y-%m-%d")
    for (s, e), rec in sorted(ep.items()):
        a, r, t = x.get((s, e), (None, None, ""))
        if not rec[3] and t: rec[3] = t
        if a and a > today: rec[3] = rec[3] or t           # future episodes keep their title
        seasons.setdefault(s, []).append(rec + [a, ep_rt(r)])
    out, best, nxt = [], None, None
    for sn in sorted(seasons, key=lambda n: (n == 0, n)):
        eps = seasons[sn]; rated = [e[1] for e in eps if e[1]]
        dates = sorted(e[4] for e in eps if e[4])
        mins = sum(e[5] for e in eps if e[5]) or None
        out.append({"n": sn, "c": len(eps), "avg": round(sum(rated) / len(rated), 1) if rated else None, "eps": eps,
                    "minutes": mins, "first": dates[0] if dates else None, "last": dates[-1] if dates else None})
        for e in eps:
            if sn and e[1] and (e[2] or 0) >= 500 and (not best or e[1] > best[3]): best = [sn, e[0], e[3], e[1]]
            if sn and e[4] and e[4] > today and (not nxt or e[4] < nxt[2]): nxt = [sn, e[0], e[4]]
    real = [s for s in out if s["n"] > 0] or out
    rated_all = [e[1] for s in real for e in s["eps"] if e[1]]
    return {"seasons": out, "season_count": len([s for s in out if s["n"] > 0]), "episode_count": sum(s["c"] for s in real),
            "avg": round(sum(rated_all) / len(rated_all), 1) if rated_all else None,
            "minutes": sum(s["minutes"] or 0 for s in real) or None, "best": best, "next": nxt,
            "status": info and info[0], "premiered": min((s["first"] for s in real if s["first"]), default=info and info[1]),
            "ended": None if (info and info[0] == "Running") else max((s["last"] for s in real if s["last"]), default=info and info[2])}

# ---------- quick title search inside the catalogue (no network): exact, prefix, every word, substring, then near misses ----------
_fidx = {"built": None}

def _index(kind):
    cat = _mem["cat"]
    if _fidx["built"] != cat["built"]: _fidx.clear(); _fidx["built"] = cat["built"]
    if kind not in _fidx:
        _fidx[kind] = [(_norm(it["n"]), it) for it in cat["items"].values() if it["k"] == kind]
    return _fidx[kind]

def find(kind, q, limit=24):
    cat = load()
    if not cat: return []
    nq = _norm(q); toks = nq.split()
    if not toks: return []
    idx, out = _index(kind), []
    for name, it in idx:
        if name == nq: sc = 100
        elif name.startswith(nq): sc = 80
        else:
            words = name.split()
            if all(any(w.startswith(t) for w in words) for t in toks): sc = 60
            elif nq in name: sc = 50
            else: continue
        out.append((sc + min(20, math.log10(max(it["v"], 10)) * 3), it))
    if len(out) < 5 and len(nq) >= 4:                       # typos: "breakng bad"
        close = set(difflib.get_close_matches(nq, [n for n, _ in idx], n=8, cutoff=.75))
        out += [(30 + min(20, math.log10(max(it["v"], 10)) * 3), it) for n, it in idx if n in close]
    out.sort(key=lambda x: -x[0])
    seen, res = set(), []
    for _, it in out:
        if it["id"] in seen: continue
        seen.add(it["id"]); res.append(it)
        if len(res) >= limit: break
    queue_resolve(res)
    return [_dress(cat, it["id"]) for it in res]

# ---------- cast and directors (IMDb principals + names), kept in cast.db, rebuilt with the catalogue ----------
CAST_DB = os.path.join(DBDIR, "cast.db")
_cast = {"building": False, "t": 0}

def _castdb(path=CAST_DB):
    c = sqlite3.connect(path, timeout=30)
    c.execute("PRAGMA temp_store=MEMORY")          # the container has no writable /tmp
    c.execute("CREATE TABLE IF NOT EXISTS people(tconst TEXT, ord INTEGER, nconst TEXT, name TEXT, cat TEXT, chars TEXT, PRIMARY KEY(tconst, ord))")
    c.execute("CREATE TABLE IF NOT EXISTS photo(name TEXT PRIMARY KEY, url TEXT, t INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS info(k TEXT PRIMARY KEY, v TEXT)")
    return c

def _build_cast(ids):
    """The first people billed on every catalogue title (actors, director, writer), streamed from IMDb's files into SQLite."""
    wanted = set(ids)
    pp, npath = os.path.join(DBDIR, "principals.tsv.gz"), os.path.join(DBDIR, "names.tsv.gz")
    new = CAST_DB + ".new"
    for q in (new, new + "-journal"):
        try: os.remove(q)
        except OSError: pass
    c = _castdb(new); c.execute("CREATE TABLE nm(nconst TEXT PRIMARY KEY, name TEXT)")
    _download("title.principals.tsv.gz", pp)
    batch, need = [], set()
    for r in _rows(pp):
        if r[0] in wanted and r[3] in ("actor", "actress", "director", "writer", "creator") and r[1].isdigit() and (int(r[1]) <= 10 or r[3] != "actor" and r[3] != "actress"):
            ch = ""
            if r[5] != "\\N":
                try: ch = ", ".join(json.loads(r[5])[:2])
                except ValueError: ch = ""
            batch.append((r[0], int(r[1]), r[2], r[3], ch[:80])); need.add(r[2])
            if len(batch) >= 5000:
                c.executemany("INSERT OR REPLACE INTO people(tconst,ord,nconst,cat,chars) VALUES(?,?,?,?,?)", batch); batch = []
    if batch: c.executemany("INSERT OR REPLACE INTO people(tconst,ord,nconst,cat,chars) VALUES(?,?,?,?,?)", batch)
    c.commit(); os.remove(pp)
    _download("name.basics.tsv.gz", npath)
    batch = []
    for r in _rows(npath):
        if r[0] in need:
            batch.append((r[0], r[1]))
            if len(batch) >= 5000:
                c.executemany("INSERT OR REPLACE INTO nm VALUES(?,?)", batch); batch = []
    if batch: c.executemany("INSERT OR REPLACE INTO nm VALUES(?,?)", batch)
    os.remove(npath)
    c.execute("UPDATE people SET name=(SELECT name FROM nm WHERE nm.nconst=people.nconst)")
    try:
        c.execute("ATTACH DATABASE ? AS old", (CAST_DB,)); c.execute("INSERT OR IGNORE INTO photo SELECT * FROM old.photo"); c.commit(); c.execute("DETACH DATABASE old")
    except sqlite3.Error:
        pass
    c.execute("DELETE FROM people WHERE name IS NULL"); c.execute("DROP TABLE nm")
    c.execute("INSERT OR REPLACE INTO info VALUES('built', ?)", (str(int(time.time())),))
    c.commit(); c.close()
    os.replace(new, CAST_DB)

def _cast_job():
    try:
        try: os.nice(10)
        except OSError: pass
        cat = _mem["cat"] or _read()
        if cat: _build_cast(cat["items"].keys())
    except Exception as e:
        _state["error"] = ("cast: " + str(e))[:200]
    finally:
        _cast["building"] = False

def ensure_cast():
    cat = _mem["cat"]
    if _cast["building"] or _eps["building"] or time.time() - _cast["t"] < 600 or not cat: return
    _cast["t"] = time.time()
    try:
        c = _castdb(); r = c.execute("SELECT v FROM info WHERE k='built'").fetchone(); c.close()
        if r and time.time() - int(r[0]) < MAXAGE and int(r[0]) >= cat["built"]: return
    except Exception:
        pass
    _cast["building"] = True
    threading.Thread(target=_cast_job, daemon=True).start()

def cast_for(tid):
    try:
        c = _castdb(); rows = c.execute("SELECT name, cat, chars FROM people WHERE tconst=? ORDER BY ord", (tid,)).fetchall(); c.close()
    except Exception:
        return None
    if not rows: return None
    def uniq(seq):
        out = []
        for x in seq:
            if x not in out: out.append(x)
        return out
    cast, seen = [], {}
    for n, k, ch in rows:
        if k not in ("actor", "actress"): continue
        if n in seen:
            if ch and ch not in seen[n]["c"]: seen[n]["c"] += " / " + ch
        else:
            seen[n] = {"n": n, "c": ch}; cast.append(seen[n])
    out = {"directors": uniq(n for n, k, _ in rows if k == "director")[:3],
           "writers": uniq(n for n, k, _ in rows if k in ("writer", "creator"))[:3], "cast": cast[:8]}
    _photos(tid, out["cast"])
    return out

def _photos(tid, cast):
    """Portraits for the cast from TVmaze: the show's own cast list when we know the show, else a name search. Cached for good."""
    if not cast: return
    names = [c["n"] for c in cast]
    c = _castdb()
    have = {r[0]: r[1] for r in c.execute("SELECT name, url FROM photo WHERE name IN (%s)" % ",".join("?" * len(names)), names)}
    todo = [n for n in names if n not in have]
    if todo:
        found, t0 = {}, time.time()
        try:
            e = _epdb(); r = e.execute("SELECT tvmaze FROM epx_info WHERE series=? AND tvmaze IS NOT NULL", (tid,)).fetchone(); e.close()
        except Exception:
            r = None
        try:
            if r:
                for x in _tvmaze(TVMAZE + "/shows/%d/cast" % r[0]):
                    p = x.get("person") or {}
                    if p.get("name") in todo and p.get("image"): found[p["name"]] = p["image"].get("medium") or ""
            for n in todo:
                if n in found or time.time() - t0 > 5: continue
                for x in _tvmaze(TVMAZE + "/search/people?q=" + urllib.parse.quote(n)):
                    p = x.get("person") or {}
                    if p.get("name", "").lower() == n.lower():
                        found[n] = (p.get("image") or {}).get("medium") or ""; break
        except Exception:
            pass
        now = int(time.time())
        c.executemany("INSERT OR REPLACE INTO photo VALUES(?,?,?)", [(n, found.get(n, ""), now) for n in todo if n in found or time.time() - t0 > 5 or True])
        c.commit(); have.update(found)
    c.close()
    for x in cast:
        if have.get(x["n"]): x["p"] = have[x["n"]]

# ---------- admin tools: counts for the sidebar, status of the background jobs, "rebuild now" ----------
_cnt = {"t": 0, "d": {}}
_tvm = {"running": False, "t": 0}

def counts():
    """How much of the catalogue has its data (cached a minute, the sidebar polls)."""
    if time.time() - _cnt["t"] < 60: return _cnt["d"]
    _cnt["t"] = time.time()
    cat = _mem["cat"]
    if not cat: return {}
    items = cat["items"]
    d = {"titles": len(items), "movies": sum(1 for i in items.values() if i["k"] == "movie"), "series": sum(1 for i in items.values() if i["k"] == "series"),
         "posters": sum(1 for i in items if i in _mem["titles"]), "anime": len(_anime_set())}
    try:
        c = _epdb()
        d["episodes_series"] = c.execute("SELECT count(DISTINCT series) FROM ep").fetchone()[0]
        d["tvmaze"] = c.execute("SELECT count(*) FROM epx_info WHERE tvmaze IS NOT NULL").fetchone()[0]
        c.close()
    except Exception: pass
    try:
        c = _castdb(); d["cast"] = c.execute("SELECT count(DISTINCT tconst) FROM people").fetchone()[0]; c.close()
    except Exception: pass
    _cnt["d"] = d
    return d

def _tvmaze_prefetch():
    """Air dates and runtimes for every series, most popular first, one request every 1.5 s (TVmaze allows 20 per 10 s)."""
    try:
        cat = _mem["cat"]
        if not cat: return
        c = _epdb(); done = {r[0] for r in c.execute("SELECT series FROM epx_info")}; c.close()
        todo = sorted((it for it in cat["items"].values() if it["k"] == "series" and it["id"] not in done), key=lambda i: -i["v"])
        for it in todo:
            if not _tvm["running"]: break
            fetch_tvmaze(it["id"]); time.sleep(1.5)
    except Exception as e:
        _state["error"] = ("tvmaze: " + str(e))[:200]
    finally:
        _tvm["running"] = False

def ensure_tvmaze():
    if _tvm["running"] or _eps["building"] or time.time() - _tvm["t"] < 3600 or not _mem["cat"]: return
    _tvm["t"] = time.time(); _tvm["running"] = True
    threading.Thread(target=_tvmaze_prefetch, daemon=True).start()

JOBS = {"catalog": (_state, "building", "_background"), "episodes": (_eps, "building", "_episodes_job"), "cast": (_cast, "building", "_cast_job"),
        "anime": (_anime, "building", "_anime_job"), "countries": (_cty, "building", "_countries_job"), "posters": (_bulk, "running", "_bulk_run"),
        "tvmaze": (_tvm, "running", "_tvmaze_prefetch")}

def rebuild(what):
    """Starts one background job now (admin button), unless it is already running."""
    if what not in JOBS: return "unknown"
    st, key, fn = JOBS[what]
    if st.get(key): return "already running"
    if not _mem["cat"] and what != "catalog": return "no catalogue yet"
    st[key] = True; st["t"] = time.time()
    threading.Thread(target=globals()[fn], daemon=True).start()
    return "started"

def status():
    """What the background jobs are doing (admin only)."""
    cat = _mem["cat"]
    def built(path):
        try:
            c = sqlite3.connect(path); r = c.execute("SELECT v FROM info WHERE k='built'").fetchone(); c.close(); return int(r[0]) if r else None
        except Exception: return None
    return {"error": _state["error"], "counts": counts(),
            "catalog": {"building": _state["building"], "built": cat and cat["built"]},
            "posters": {"running": _bulk["running"]}, "tvmaze": {"running": _tvm["running"]},
            "episodes": {"building": _eps["building"], "built": built(EPS_DB)}, "cast": {"building": _cast["building"], "built": built(CAST_DB)},
            "anime": {"building": _anime["building"]},
            "countries": {"building": _cty["building"], "complete": bool(_cty.get("d")), "fail": _cty.get("fail")}}

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
        if (cat is None or time.time() - cat["built"] > MAXAGE or cat.get("schema") != SCHEMA) and not _state["building"]:
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
        cd = _cty.get("d") or {}
        for code in cd:                                      # then every title of every country list
            for kind in KINDS:
                for i in sorted(cd[code].get(kind, ())): 
                    if i in cat["items"]: add(i)
        for k in range(0, len(order), 40):
            chunk = [i for i in order[k:k + 40] if i["id"] not in _mem["titles"] and time.time() - _failed.get(i["id"], 0) > 1800]
            if chunk and _resolver: _resolver(chunk)
    finally:
        _bulk["running"] = False

def ensure_all():
    ensure_anime()
    ensure_episodes()
    ensure_cast()
    ensure_tvmaze()
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

def _scoped_rules(kind):
    this = date.today().year
    return {r["id"]: r for r in _rules(kind, this)}

def _genre_rows(pool, limit=5):
    cnt = {}
    for it in pool:
        for g in it["g"]: cnt[g] = cnt.get(g, 0) + 1
    return [g for g, n in sorted(cnt.items(), key=lambda x: -x[1]) if n >= 10][:limit]

def view_scoped(kind, f, row=None, sort=None, offset=0, limit=40):
    """The same shelves and categories, but computed only on titles that match the chosen filters (e.g. Korean cinema):
    "Top rated" becomes Korean top rated, and shelves for the genres this country is known for are added."""
    cat, pool = _filter_pool(kind, f)
    if cat is None: return {"building": True, "rows": [], "chips": []}
    rules = _scoped_rules(kind)
    chips = [{"id": r["id"], "name": r["name"], "home": r["home"]} for r in rules.values()]
    gnames = _genre_rows(pool)
    chips += [{"id": "gx-" + g.lower(), "name": g, "home": True} for g in gnames if "g-" + g.lower() not in rules]
    def members(rid):
        if rid in ("new", "popular") and f.get("decade"):      # "recent" makes no sense inside a chosen decade: show that decade's most voted
            return pool, "votes"
        if rid in rules:
            r = rules[rid]
            return [i for i in pool if r["fn"](i)], ("rating" if r["sort"] == "w" else "votes")
        if rid.startswith("gx-") or rid.startswith("g-"):
            g = next((x for x in {g for it in pool for g in it["g"]} if x.lower() == rid.split("-", 1)[1]), None)
            return ([i for i in pool if g in i["g"]] if g else []), "rating"
        return pool, "best"
    if row:
        its, by = members(row)
        fs = dict(FSORTS); sort = sort if sort in fs else by
        its = sorted(its, key=fs[sort]) if sort != "rating" else sorted(its, key=lambda i: (-i["r"], -i["v"]))
        page = its[offset:offset + limit]
        queue_resolve(page)
        d = [_dress(cat, i["id"]) for i in page]
        nm = next((c["name"] for c in chips if c["id"] == row), "Results")
        return {"rows": [{"id": row, "name": nm, "items": d}], "chips": chips, "total": len(its), "offset": offset, "sort": sort, "default": by,
                "ready": all("img" in x for x in d)}
    out = []
    for r in rules.values():
        if not r["home"]: continue
        its, by = members(r["id"])
        if len(its) < 4: continue
        its = sorted(its, key=lambda i: (-i["r"], -i["v"])) if by == "rating" else sorted(its, key=lambda i: (-i["v"], -i["r"]))
        out.append({"id": r["id"], "name": r["name"], "items": its[:HOME_N]})
    for g in gnames:
        its, by = members("gx-" + g.lower())
        if len(its) < 8: continue
        its = sorted(its, key=lambda i: (-i.get("w", 0), -i["v"]))
        out.append({"id": "gx-" + g.lower(), "name": g, "items": its[:HOME_N]})
    if not out and pool:      # a small list (e.g. 2 Italian series): one shelf with everything instead of an empty page
        out.append({"id": "__f", "name": "All titles", "items": sorted(pool, key=lambda i: (-i.get("w", 0), -i["v"]))[:HOME_N]})
    queue_resolve([it for r in out for it in r["items"]])
    d_out = [{"id": r["id"], "name": r["name"], "items": [_dress(cat, it["id"]) for it in r["items"]]} for r in out]
    total = sum(len(r["items"]) for r in d_out)
    return {"ready": sum(1 for r in d_out for x in r["items"] if "img" in x) >= total * 0.95, "rows": d_out, "chips": chips, "total_titles": len(pool)}

def view(kind, row=None, sort=None, offset=0, limit=40, flt=None):
    if flt: return view_scoped(kind, flt, row, sort, offset, limit)
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

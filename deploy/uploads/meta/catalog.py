"""Browsable catalogue for the request page, built from IMDb's free daily datasets (ratings, titles, genres, runtime).
Posters and plot summaries come from Jellyfin's own TMDb lookup (by IMDb id) and are cached in SQLite, so the page loads fast.
Everything lives in /db. Rebuilt weekly in the background at low priority.

Shelves are rules over that data ("hidden gems", "mind-benders", "short and sweet", ...), shown on the home view; every
shelf can also be opened on its own with up to 60 titles."""
import difflib, gzip, heapq, json, math, os, random, re, sqlite3, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import date

DBDIR = os.environ.get("DB_DIR", "/db")
CAT = os.path.join(DBDIR, "catalog.json")
TITLES_DB = os.path.join(DBDIR, "titles.db")
BASE = "https://datasets.imdbws.com/"
MAXAGE = 7 * 86400
SCHEMA = 17      # bump to make the next start rebuild the catalogue in the background (the old one keeps being served meanwhile)
HOME_N = 16
_state = {"building": False, "error": ""}
_lock = threading.Lock()
_mem = {"t": 0, "cat": None, "titles": set()}
_pending = set()
_failed = {}
_resolver = None
_bulk = {"running": False, "t": 0}

KINDS = {"movie": ("movie",), "series": ("tvSeries", "tvMiniSeries")}
MIN_VOTES = {"movie": 60000, "series": 30000}

ANIME_F = os.path.join(DBDIR, "anime.json")
_anime = {"s": set(), "m": {}, "mt": 0, "building": False, "t": 0}

ANILIST = "https://graphql.anilist.co"
FRIBB = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
ANIME_PAGES = 100           # 50 per page, most popular first: the 5,000 best-known anime on AniList

def _anime_meta():
    """{imdb id: [anilist popularity, anilist score]} from data/anime.json (written by the weekly anime job)."""
    try:
        mt = os.path.getmtime(ANIME_F)
        if mt != _anime["mt"]:
            d = json.load(open(ANIME_F))
            _anime["m"] = d if isinstance(d, dict) else {i: [0, None] for i in d}
            _anime["s"] = set(_anime["m"]); _anime["mt"] = mt
    except (OSError, ValueError):
        pass
    return _anime.get("m") or {}

def _anime_set():
    _anime_meta()
    return _anime["s"]

def _anilist_page(page):
    q = "query($p:Int){Page(page:$p,perPage:50){pageInfo{hasNextPage} media(type:ANIME,sort:POPULARITY_DESC,isAdult:false){id popularity averageScore}}}"
    req = urllib.request.Request(ANILIST, data=json.dumps({"query": q, "variables": {"p": page}}).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)["data"]["Page"]
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(int(e.headers.get("Retry-After") or 60) + 1); continue
            raise
    return None

def _anime_job():
    """Anime = what AniList (the anime community's database) lists, mapped to IMDb ids. Small downloads, once a week."""
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        fp = os.path.join(DBDIR, "anime-map.json")
        with urllib.request.urlopen(urllib.request.Request(FRIBB, headers={"User-Agent": UA}), timeout=120) as r, open(fp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b: break
                f.write(b)
        to_imdb = {}
        for x in json.load(open(fp, encoding="utf-8")):
            ids = x.get("imdb_id") or []
            if isinstance(ids, str): ids = [ids]
            if x.get("anilist_id") and ids: to_imdb[x["anilist_id"]] = [i for i in ids if re.fullmatch(r"tt\d{6,10}", i)]
        os.remove(fp)
        meta = {}
        for page in range(1, ANIME_PAGES + 1):
            pg = _anilist_page(page)
            if not pg: break
            for m in pg["media"]:
                for i in to_imdb.get(m["id"], ()):
                    old = meta.get(i)
                    if not old or (m["popularity"] or 0) > old[0]: meta[i] = [m["popularity"] or 0, m["averageScore"]]
            if not pg["pageInfo"]["hasNextPage"]: break
            time.sleep(2.2)                       # AniList allows 30 requests a minute
        if len(meta) < 200: raise RuntimeError("AniList returned too little (%d titles)" % len(meta))
        tmp = ANIME_F + ".tmp"
        json.dump(meta, open(tmp, "w")); os.replace(tmp, ANIME_F)
        _anime["mt"] = 0; found = set(_anime_meta())
        cat = _mem["cat"]
        if cat:
            with _lock:
                for i in [i for i, it in cat["items"].items() if it.get("la") and i not in found]: del cat["items"][i]
                for it in cat["items"].values(): it.pop("la", None)
        refresh_rows()
    except Exception as e:
        _state["error"] = ("anime: " + str(e))[:200]
    finally:
        _anime["building"] = False

def _norm(t): return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()      # used by the search

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
    DARK = ("Horror", "Thriller", "Crime", "War", "Mystery", "Action", "Documentary", "Sci-Fi", "Reality-TV", "Talk-Show", "Biography", "History")
    def feel(i, rmin, vmin):      # light and warm: comedies, romances, family films, musicals; nothing grim, no action, no documentaries
        if i["r"] < rmin or i["v"] < vmin or g(i, *DARK): return False
        if not g(i, "Comedy", "Family", "Romance", "Music", "Musical"): return False
        if i["id"] in _anime_set() and "Family" not in i["g"]: return False
        if "Drama" in i["g"] and not g(i, "Comedy", "Family"): return False      # a sad romance or a music drama is not feel-good
        return True
    a = lambda i, *gs: all(x in i["g"] for x in gs)
    W, V = "w", "v"
    R = []
    def add(id_, name, fn, sort=W, home=False, pool="main"):
        R.append({"id": id_, "name": name, "fn": fn, "sort": sort, "home": home, "pool": pool})
    add("new", "New and notable", lambda i: i["y"] and i["y"] >= this - 1 and i["r"] >= 6.0, V, True, "all")
    add("fresh", "Just released", lambda i: i["y"] == this and i["r"] >= 5.5, V, True, "all")
    add("top", "Top rated", lambda i: True, W, True)
    add("popular", "Popular", lambda i: i["y"] and i["y"] >= this - 15 and i["r"] >= 6.0, V, True, "all")
    add("anime", "Anime", lambda i: i["id"] in _anime_set(), "ap", True, "all")
    add("gems", "Hidden gems", lambda i: i["r"] >= 7.8 and 35000 <= i["v"] <= 160000, W, True, "all")
    if kind == "movie":
        add("mind", "Mind-benders", lambda i: i["r"] >= 7.0 and ((a(i, "Thriller") and g(i, "Mystery", "Sci-Fi")) or a(i, "Sci-Fi", "Mystery") or (a(i, "Drama") and g(i, "Mystery") and g(i, "Sci-Fi", "Thriller"))), W, True, "all")
        add("feel", "Feel-good", lambda i: feel(i, 7.0, 40000), W, True, "all")
        add("edge", "Edge of your seat", lambda i: i["r"] >= 7.2 and a(i, "Thriller") and g(i, "Crime", "Mystery", "Horror"), W, True)
        add("family", "Family night", lambda i: i["v"] >= 80000 and g(i, "Family", "Animation"))
        add("true", "Based on true stories", lambda i: i["r"] >= 7.3 and g(i, "Biography", "History"))
        add("short", "Short and sweet (under 95 min)", lambda i: i["rt"] and i["rt"] <= 95 and i["r"] >= 7.3)
        add("classics", "Classics", lambda i: i["y"] and i["y"] < 1985 and i["r"] >= 7.0, W, False, "all")
        add("silent", "The silent era", lambda i: i["y"] and i["y"] < 1930 and i["r"] >= 6.8, W, False, "all")
        add("golden", "Golden age (1930-1959)", lambda i: i["y"] and 1930 <= i["y"] <= 1959 and i["r"] >= 7.0, W, False, "all")
        add("noir", "Film noir", lambda i: "Film-Noir" in i["g"], W, False, "all")
        add("sixties", "The 60s", lambda i: i["y"] and 1960 <= i["y"] <= 1969 and i["r"] >= 6.8, W, False, "all")
        add("seventies", "The 70s", lambda i: i["y"] and 1970 <= i["y"] <= 1979 and i["r"] >= 6.8, W, False, "all")
        add("eighties", "The 80s", lambda i: i["y"] and 1980 <= i["y"] <= 1989, W)
        add("nineties", "The 90s", lambda i: i["y"] and 1990 <= i["y"] <= 1999)
        add("noughties", "The 2000s", lambda i: i["y"] and 2000 <= i["y"] <= 2009)
        for gname, label in (("Action", "Action"), ("Comedy", "Comedy"), ("Drama", "Drama"), ("Sci-Fi", "Sci-Fi"), ("Horror", "Horror"),
                             ("Animation", "Animation"), ("Crime", "Crime"), ("Romance", "Romance"), ("Fantasy", "Fantasy"), ("War", "War"), ("Music", "Music")):
            add("g-" + gname.lower(), label, (lambda gn: lambda i: gn in i["g"])(gname))
    else:
        add("binge", "Binge-worthy", lambda i: i["r"] >= 8.2 and i["v"] >= 100000, V, True)
        add("feel", "Feel-good", lambda i: feel(i, 7.5, 15000), W, True, "all")
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
        am = _anime_meta(); an = set(am)
        for i in items.values():
            if i["id"] in am: i["ap"] = am[i["id"]][0]
        everyone = [i for i in items.values() if i["k"] == kind and (not i.get("la") or i["id"] in an)]
        main = [i for i in everyone if i["v"] >= MIN_VOTES[kind]]
        for rule in _rules(kind, this):
            pool = everyone if rule["pool"] == "all" else main
            lst = sorted([i for i in pool if rule["fn"](i)], key=lambda i: -i.get(rule["sort"], 0))[:3000]
            if len(lst) < 8: continue
            rows[kind].append({"id": rule["id"], "name": rule["name"], "home": rule["home"], "by": "rating" if rule["sort"] == "w" else "votes", "keep": rule["sort"] == "ap", "ids": [i["id"] for i in lst]})
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

ANIME_MIN = 100     # animated titles enter from this many votes; the anime job keeps the Japanese ones and drops the rest

def _need(kind, anim, recent, year=None):
    old, older = bool(year and year < 1970), bool(year and year < 1990)      # old films have far fewer votes: the classics get in all the same
    if kind == "movie": return (500 if anim else 1000) if recent else (2000 if anim else 1000 if old else 2000 if older else 5000)
    return (200 if anim else 400) if recent else (800 if anim else 600 if older else 2000)

def _build():
    """IMDb ratings + basics -> catalogue. Both files are streamed into a scratch SQLite file and joined there,
    so the build needs a few MB of memory instead of holding every rated title in a dictionary."""
    os.makedirs(DBDIR, exist_ok=True)
    rp, bp, sp = os.path.join(DBDIR, "ratings.tsv.gz"), os.path.join(DBDIR, "basics.tsv.gz"), os.path.join(DBDIR, "build.db")
    for q in (sp, sp + "-journal"):
        try: os.remove(q)
        except OSError: pass
    db = sqlite3.connect(sp); db.execute("PRAGMA temp_store=MEMORY"); db.execute("PRAGMA journal_mode=OFF"); db.execute("PRAGMA synchronous=OFF")
    db.execute("CREATE TABLE r(id TEXT PRIMARY KEY, rating REAL, votes INTEGER)")
    db.execute("CREATE TABLE b(id TEXT PRIMARY KEY, tt TEXT, name TEXT, year INTEGER, rt INTEGER, genres TEXT)")
    _download("title.ratings.tsv.gz", rp)
    an = _anime_set()
    batch = []
    for r in _rows(rp):
        if r[2].isdigit() and (int(r[2]) >= ANIME_MIN or r[0] in an):
            batch.append((r[0], float(r[1]), int(r[2])))
            if len(batch) >= 20000: db.executemany("INSERT INTO r VALUES(?,?,?)", batch); batch = []
    if batch: db.executemany("INSERT INTO r VALUES(?,?,?)", batch)
    db.commit(); os.remove(rp)
    _download("title.basics.tsv.gz", bp)
    want = {t: k for k, ts in KINDS.items() for t in ts}
    batch = []
    for r in _rows(bp):
        if r[1] in want and r[4] == "0":
            batch.append((r[0], r[1], r[2], int(r[5]) if r[5].isdigit() else None, int(r[7]) if r[7].isdigit() else None, "" if r[8] == "\\N" else r[8]))
            if len(batch) >= 20000: db.executemany("INSERT INTO b VALUES(?,?,?,?,?,?)", batch); batch = []
    if batch: db.executemany("INSERT INTO b VALUES(?,?,?,?,?,?)", batch)
    db.commit(); os.remove(bp)
    this = date.today().year
    natl = set()      # films and series of the national cinemas (French, Korean, ...): they get far fewer IMDb votes, so they enter with fewer
    try:
        with open(COUNTRIES_F) as f: cj = json.load(f)
        for c, v in cj.items():
            if c not in META_KEYS: natl.update(v.get("movie", ())); natl.update(v.get("series", ()))
    except (OSError, ValueError):
        pass
    items = {}
    for i, tt, name, year, rt, genres, rating, votes in db.execute("SELECT b.id, tt, name, year, rt, genres, rating, votes FROM b JOIN r ON r.id = b.id"):
        kind = want[tt]; g = genres.split(",") if genres else []
        anim, recent = "Animation" in g, bool(year and year >= this - 1)
        need = _need(kind, anim, recent, year)
        if i in natl: need = min(need, 1000 if kind == "movie" else 400)
        if i in an: anim = True
        if votes < need and not anim: continue
        it = {"id": i, "k": kind, "tt": tt, "n": name, "y": year, "rt": rt, "g": g, "r": rating, "v": votes}
        if votes < need and i not in an: it["la"] = 1     # a low-vote animated title: kept only if AniList lists it
        items[i] = it
    db.close()
    for q in (sp, sp + "-journal"):
        try: os.remove(q)
        except OSError: pass
    out = {"built": int(time.time()), "schema": SCHEMA, "items": {}, "rows": {"movie": [], "series": []}, "rowids": []}
    for kind in KINDS:
        C, m = 6.8, MIN_VOTES[kind] * 2
        for it in items.values():
            if it["k"] == kind: it["w"] = (it["v"] / (it["v"] + m)) * it["r"] + (m / (it["v"] + m)) * C
    out["rows"], out["rowids"] = _make_rows(items, this)
    for it in items.values():
        out["items"][it["id"]] = {k: (round(v, 3) if k == "w" else v) for k, v in it.items()}
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
          ("SE", "Scandinavian cinema", {"langs": ["Q9027", "Q9035", "Q9043", "Q1412", "Q294"], "countries": ["Q34", "Q35", "Q20", "Q33"]}),
          ("CN", "Chinese cinema", {"langs": ["Q7850", "Q9192", "Q9186"], "countries": ["Q148", "Q8646", "Q865"]}),
          ("IR", "Iranian cinema", {"langs": ["Q9168"], "countries": ["Q794"]}),
          ("TR", "Turkish cinema", {"langs": ["Q256"], "countries": ["Q43"]})]
COUNTRIES_F = os.path.join(DBDIR, "countries.json")
META_KEYS = ("built", "complete", "done")
_cty = {"t": 0, "d": None, "building": False, "fail": 0}

FILM_CLS = ("Q11424", "Q202866", "Q93204", "Q506240", "Q24869", "Q24862")          # film, animated, documentary, TV film, feature, short
SERIES_CLS = ("Q5398426", "Q1259759", "Q581714", "Q117467246", "Q63952888", "Q526877", "Q15416")   # series, miniseries, animated, anime, web, TV programme

def _wikidata_pair(spec):
    """Films and series of a "cinema" group (Wikidata, free): original language in the group's languages OR made in one of its
    countries, and never English-language. One request returns both kinds: {"movie": [...], "series": [...]}.
    Wikidata allows about one request a minute for us, so a 429 is waited out (Retry-After) instead of hammered."""
    parts = []
    if spec["langs"]: parts.append("{ VALUES ?lang { %s } ?f wdt:P364 ?lang. }" % " ".join("wd:" + x for x in spec["langs"]))
    if spec["countries"]: parts.append("{ VALUES ?c { %s } ?f wdt:P495 ?c. }" % " ".join("wd:" + x for x in spec["countries"]))
    q = ("SELECT DISTINCT ?imdb ?cls WHERE { VALUES ?cls { %s } ?f wdt:P31 ?cls; wdt:P345 ?imdb. %s FILTER NOT EXISTS { ?f wdt:P364 wd:Q1860 } }"
         % (" ".join("wd:" + x for x in FILM_CLS + SERIES_CLS), " UNION ".join(parts)))
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q)
    last = None
    for attempt in range(8):
        req = urllib.request.Request(url, headers={"User-Agent": "NahokoCatalogue/1.2 (https://github.com/ElNahoko/selfhost-jellyfin; self-hosted media catalogue)", "Accept": "application/sparql-results+json"})
        try:
            with urllib.request.urlopen(req, timeout=170) as r:
                d = json.load(r)
            out = {"movie": [], "series": []}
            for b in d["results"]["bindings"]:
                i = b["imdb"]["value"]
                if i.startswith("tt"): out["movie" if b["cls"]["value"].rsplit("/", 1)[-1] in FILM_CLS else "series"].append(i)
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
    """Collects every country list again. The previous lists stay in use until each new one arrives (a country page is
    never empty during a rebuild); "done" remembers which ones this run has finished, so a restart carries on."""
    cat = _mem["cat"] or _read()
    if not cat: return
    out = {"built": int(time.time()), "complete": False, "done": []}
    try:
        with open(COUNTRIES_F) as f: prev = json.load(f)
        out.update({c: v for c, v in prev.items() if c not in META_KEYS and (v.get("movie") or v.get("series"))})
        if not prev.get("complete") and time.time() - prev.get("built", 0) < 6 * 3600:      # an interrupted run: carry on
            out["built"] = prev.get("built", out["built"])
            out["done"] = list(prev["done"]) if "done" in prev else [c for c in prev if c not in META_KEYS and (prev[c].get("movie") or prev[c].get("series"))]
    except Exception:
        pass
    for code, label, spec in GROUPS:
        if code in out["done"]: continue
        try:
            try: pair = _wikidata_pair(spec)
            except Exception:      # too slow for Wikidata (504): by language alone, which is quick
                if not spec["langs"]: raise
                time.sleep(62); pair = _wikidata_pair(dict(spec, countries=[]))
            if pair.get("movie") or pair.get("series"):
                out[code] = {k: sorted(set(v)) for k, v in pair.items()}
                out["done"].append(code)
        except Exception:
            pass      # keeps the previous list for this country
        out["complete"] = len(out["done"]) == len(GROUPS)
        tmp = COUNTRIES_F + ".tmp"      # saved after every country, so new lists show up as they come
        with open(tmp, "w") as f: json.dump(out, f, separators=(",", ":"))
        os.replace(tmp, COUNTRIES_F); _cty["t"] = 0
        time.sleep(62)
    if not out["complete"]: _cty["fail"] = time.time()      # try the missing ones again in 15 minutes

def _countries_job():
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        _build_countries()
    finally:
        _cty["building"] = False

def countries():
    """{code: {kind: set(ids)}} or None while it is still being collected (started in the background when missing/old)."""
    if time.time() - _cty["t"] > 120:
        _cty["t"] = time.time()
        try:
            with open(COUNTRIES_F) as f: raw = json.load(f)
            _cty["d"] = {c: {k: set(v) for k, v in raw[c].items()} for c in raw if c not in META_KEYS}
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
    c.execute("CREATE TABLE IF NOT EXISTS epimg(series TEXT, season INTEGER, ep INTEGER, img TEXT, PRIMARY KEY(series, season, ep))")      # a still per episode
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
        try: c.execute("INSERT OR IGNORE INTO epimg SELECT * FROM old.epimg")
        except sqlite3.Error: pass
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
        except (OSError, AttributeError): pass
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
UA = "NahokoCatalogue/1.2 (https://github.com/ElNahoko/selfhost-jellyfin; self-hosted media catalogue)"

def _tvmaze(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=8) as r:
        return json.load(r)

def fetch_tvmaze(sid):
    """Air dates, runtimes and titles per episode from TVmaze (free, no key), by IMDb id. Kept in episodes.db; retried daily if missing."""
    c = _epdb()
    row = c.execute("SELECT fetched, tvmaze FROM epx_info WHERE series=?", (sid,)).fetchone()
    stills = c.execute("SELECT 1 FROM epimg WHERE series=? LIMIT 1", (sid,)).fetchone()      # fetched before the stills were kept: once more
    if row and time.time() - row[0] < (MAXAGE if row[1] else 86400) and (stills or not row[1] or time.time() - row[0] < 3600): c.close(); return
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
        c.execute("DELETE FROM epimg WHERE series=?", (sid,))
        c.executemany("INSERT OR REPLACE INTO epimg VALUES(?,?,?,?)", [(sid, e.get("season") or 0, e.get("number"), (e.get("image") or {}).get("medium") or "")
                                                                       for e in eps if e.get("number") and (e.get("image") or {}).get("medium")] or [(sid, -1, -1, "")])
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
        stills = {(s, e): u.replace("https://static.tvmaze.com/uploads/images/", "") for s, e, u in c.execute("SELECT season, ep, img FROM epimg WHERE series=? AND img != ''", (sid,))}      # short: the page adds the start
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
        seasons.setdefault(s, []).append(rec + [a, ep_rt(r), stills.get((s, e), "")])
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
    c.execute("CREATE INDEX IF NOT EXISTS people_name ON people(name, cat)")      # "more from this director"
    c.execute("CREATE INDEX IF NOT EXISTS people_who ON people(nconst)")         # a person's page: everything they made
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
        except (OSError, AttributeError): pass
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
        c = _castdb(); rows = c.execute("SELECT name, cat, chars, nconst FROM people WHERE tconst=? ORDER BY ord", (tid,)).fetchall(); c.close()
    except Exception:
        return None
    if not rows: return None
    def uniq(seq):
        out = []
        for x in seq:
            if x not in out: out.append(x)
        return out
    cast, seen = [], {}
    for n, k, ch, nm in rows:
        if k not in ("actor", "actress"): continue
        if n in seen:
            if ch and ch not in seen[n]["c"]: seen[n]["c"] += " / " + ch
        else:
            seen[n] = {"n": n, "c": ch, "id": nm}; cast.append(seen[n])
    crew = lambda ks: uniq({"n": n, "id": nm} for n, k, _, nm in rows if k in ks)[:3]
    out = {"directors": uniq(n for n, k, _, _ in rows if k == "director")[:3],
           "writers": uniq(n for n, k, _, _ in rows if k in ("writer", "creator"))[:3], "cast": cast[:10],
           "dirs": crew(("director",)), "wrs": crew(("writer", "creator"))}      # the same, with the ids of their pages
    if _photos(tid, out["cast"]): out["pending"] = True      # some portraits are still being looked up (ask again in a moment)
    return out

def _photos(tid, cast):
    """Portraits for the cast from TVmaze: the show's own cast list when we know the show, else a name search. Cached for good."""
    if not cast: return
    names = [c["n"] for c in cast]
    c = _castdb()
    old = int(time.time()) - 14 * 86400      # a portrait not found is looked for again after two weeks
    have = {r[0]: r[1] for r in c.execute("SELECT name, url FROM photo WHERE name IN (%s) AND (url != '' OR t > ?)" % ",".join("?" * len(names)), names + [old])}
    c.close()
    for x in cast:
        if have.get(x["n"]): x["p"] = have[x["n"]]
    todo = [n for n in names if n not in have]
    if not todo or tid in _photo_busy: return bool(todo)
    _photo_busy.add(tid)                     # look the missing ones up in the background: the dialog never waits for TVmaze
    threading.Thread(target=_photo_fetch, args=(tid, todo), daemon=True).start()
    return True

_photo_busy = set()
person_lookup = None      # set by meta.py: name -> portrait url (TMDB, through Jellyfin's own metadata search)

def _photo_fetch(tid, todo):
    try:
        c = _castdb()
        found, tried, t0 = {}, set(), time.time()
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
                if n in found or not person_lookup or time.time() - t0 > 25: continue
                try: u = person_lookup(n)
                except Exception: u = None
                if u: found[n] = u
            for n in todo:
                if n in found or time.time() - t0 > 30: continue
                tried.add(n)
                for x in _tvmaze(TVMAZE + "/search/people?q=" + urllib.parse.quote(n)):
                    p = x.get("person") or {}
                    if p.get("name", "").lower() == n.lower():
                        if (p.get("image") or {}).get("medium"): found[n] = p["image"]["medium"]
                        break
        except Exception:
            pass
        now = int(time.time())
        c.executemany("INSERT OR REPLACE INTO photo VALUES(?,?,?)", [(n, found.get(n, ""), now) for n in todo if n in found or n in tried])
        c.commit(); c.close()
    finally:
        _photo_busy.discard(tid)

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
            "countries": {"building": _cty["building"], "complete": bool(_cty.get("d")), "fail": _cty.get("fail")},
            "per_country": _per_country()}

def _per_country():
    """How many films and series of each national cinema are in the catalogue (for the admin overview)."""
    cat, cd = _mem["cat"], _cty.get("d")
    if not cat or not cd: return []
    out = []
    for code, label, _ in GROUPS:
        ids = cd.get(code) or {}
        out.append({"code": code, "name": label, "movie": sum(1 for i in ids.get("movie", ()) if i in cat["items"]),
                    "series": sum(1 for i in ids.get("series", ()) if i in cat["items"]),
                    "known": len(ids.get("movie", ())) + len(ids.get("series", ()))})
    return out

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
                with _titles() as c: _mem["titles"] = {r[0] for r in c.execute("SELECT id FROM titles")}     # ids only
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
        except (OSError, AttributeError): pass
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
        except (OSError, AttributeError): pass
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
        for it in sorted(cat["items"].values(), key=lambda x: -x["v"]): add(it["id"])     # then everything else, best known first
        for k in range(0, len(order), 40):
            chunk = [i for i in order[k:k + 40] if i["id"] not in _mem["titles"] and time.time() - _failed.get(i["id"], 0) > 86400]
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
    now = time.time()
    if all(i in _mem["titles"] or now - _failed.get(i, 0) < 86400 for i in _mem["cat"]["items"]): return     # every title has a poster or was tried today
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
    _mem["titles"].add(i)

def known(i):
    if i not in _mem["titles"]: return None
    try:
        c = _titles(); r = c.execute("SELECT img, overview FROM titles WHERE id=?", (i,)).fetchone(); c.close()
        return (r[0], r[1]) if r else None
    except Exception:
        return None

def _dress(cat, i):
    it = dict(cat["items"][i]); k = known(i)
    if k: it["img"], it["o"] = k
    return it

# "rating" is vote-weighted: a 9.1 from 2,000 votes does not beat 12 Angry Men
SORTS = {"rating": lambda i: (-i.get("w", i["r"]), -i["v"]), "votes": lambda i: (-i["v"], -i["r"]),
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

def _local(i):
    """Rating weighted for a smaller pool (one country, one genre): a 9.9 from a few thousand votes does not beat
    a well-loved film; national favourites still rise. Titles without a poster yet go after the others."""
    v = i["v"]; m = 8000.0
    return ((v / (v + m)) * i["r"] + (m / (v + m)) * 6.6)

def _has_pic(i): return i["id"] in _mem["titles"]      # titles whose poster was found (kept in memory, no database call)

def view_scoped(kind, f, row=None, sort=None, offset=0, limit=40):
    """The same shelves and categories, but computed only on titles that match the chosen filters (e.g. Korean cinema):
    "Top rated" becomes Korean top rated, and shelves for the genres this country is known for are added."""
    cat, pool = _filter_pool(kind, f)
    if cat is None: return {"building": True, "rows": [], "chips": []}
    rules = _scoped_rules(kind)
    if f.get("country") and f["country"] != "JP": rules.pop("anime", None)      # anime is Japanese; elsewhere the Animation genre shelf shows cartoons
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
        its = sorted(its, key=fs[sort]) if sort != "rating" else sorted(its, key=lambda i: (-_local(i), -i["v"]))
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
        its = sorted(its, key=lambda i: (-_local(i), -i["v"])) if by == "rating" else sorted(its, key=lambda i: (-i["v"], -i["r"]))
        its = sorted(its[:60], key=lambda i: not _has_pic(i))      # the shelf shows titles with a poster first
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
        allit = [cat["items"][i] for i in r["ids"]] if r.get("keep") and sort == r.get("by") else _ordered(cat, r["ids"], sort)
        page = allit[offset:offset + limit]
        queue_resolve(page)
        its = [_dress(cat, i["id"]) for i in page]
        return {"rows": [{"id": r["id"], "name": r["name"], "items": its}], "chips": chips, "total": len(allit), "offset": offset,
                "sort": sort, "default": r.get("by", "rating"), "ready": all("img" in x for x in its)}
    out = []
    for r in rows:
        if not r["home"]: continue
        # the shelf is the top of the category; show it ordered by what it is about (rating, or popularity for "Popular")
        top = [cat["items"][i] for i in r["ids"][:HOME_N]] if r.get("keep") else _ordered(cat, r["ids"][:HOME_N], r.get("by", "rating"))
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

# ---------- related titles (the details dialog: "More like this", "More from <director>") ----------
def related(tid, n=14):
    """Titles like this one: the same genres above all, then the same country, a close year, and good ones first.
    Anime goes with anime. Plus the other titles of its director (films) or creator (series) that the catalogue has."""
    cat = load()
    if not cat or tid not in cat["items"]: return None
    it = cat["items"][tid]; gs = set(it["g"]); y = it["y"] or 2000
    an = _anime_set(); is_an = tid in an
    cd = countries() or {}
    mine = set()
    for v in cd.values():
        if tid in v.get(it["k"], ()): mine |= v.get(it["k"], set())
    def score(o):
        og = set(o["g"]); inter = len(gs & og)
        if not inter: return None
        s = 3.0 * inter / len(gs | og)
        if (o["id"] in an) != is_an: s -= 1.5
        if mine and o["id"] in mine: s += 0.8
        s -= min(abs((o["y"] or y) - y), 40) / 25.0
        return s + o.get("w", o["r"]) / 3.0
    best = heapq.nlargest(n, ((sc, o["id"]) for o in cat["items"].values()
                              if o["k"] == it["k"] and o["id"] != tid and (sc := score(o)) is not None))
    sim = [_dress(cat, i) for _, i in best]
    queue_resolve([cat["items"][i] for _, i in best])
    out = {"similar": sim}
    try:
        cd2 = cast_for(tid) or {}
        who = (cd2.get("directors") or [])[:1] if it["k"] == "movie" else (cd2.get("writers") or [])[:1]
        if who:
            c = _castdb()
            rows = c.execute("SELECT DISTINCT tconst FROM people WHERE name=? AND cat IN (%s)" %
                             ("'director'" if it["k"] == "movie" else "'creator','writer'"), (who[0],)).fetchall(); c.close()
            ids = [r[0] for r in rows if r[0] != tid and r[0] in cat["items"] and r[0] not in {x["id"] for x in sim}]
            ids.sort(key=lambda i: -cat["items"][i].get("w", 0))
            if ids:
                queue_resolve([cat["items"][i] for i in ids[:n]])
                out["by"] = {"name": who[0], "role": "director" if it["k"] == "movie" else "creator", "items": [_dress(cat, i) for i in ids[:n]]}
    except Exception:
        pass
    return out

# ---------- wide background picture for a series' page (TVmaze "background"), looked up once and kept ----------
def backdrop(tid):
    """-> url of a wide picture for the title page, '' when there is none (films have none in our free sources)."""
    try:
        with _titles() as c:
            c.execute("CREATE TABLE IF NOT EXISTS backdrops(id TEXT PRIMARY KEY, url TEXT, t INTEGER)")
            r = c.execute("SELECT url FROM backdrops WHERE id=?", (tid,)).fetchone()
        if r is not None: return r[0]
    except Exception:
        return ""
    url = ""
    try:
        e = _epdb(); r = e.execute("SELECT tvmaze FROM epx_info WHERE series=? AND tvmaze IS NOT NULL", (tid,)).fetchone(); e.close()
        if r:
            imgs = _tvmaze(TVMAZE + "/shows/%d/images" % r[0]) or []
            bg = [x for x in imgs if x.get("type") == "background"]
            bg.sort(key=lambda x: (not x.get("main"), abs(((x.get("resolutions") or {}).get("original") or {}).get("width", 0) - 1920)))
            if bg: url = bg[0]["resolutions"]["original"]["url"]
    except Exception:
        return ""
    try:
        with _titles() as c: c.execute("INSERT OR REPLACE INTO backdrops VALUES(?,?,?)", (tid, url, int(time.time())))
    except Exception:
        pass
    return url

def get_cover(tid):
    """The wide picture chosen for a title page: url, '' (none exists), or None (not looked up yet)."""
    try:
        with _titles() as c:
            c.execute("CREATE TABLE IF NOT EXISTS covers(id TEXT PRIMARY KEY, url TEXT, t INTEGER)")
            r = c.execute("SELECT url FROM covers WHERE id=?", (tid,)).fetchone()
        return r[0] if r else None
    except Exception:
        return None

def save_cover(tid, url):
    with _titles() as c:
        c.execute("CREATE TABLE IF NOT EXISTS covers(id TEXT PRIMARY KEY, url TEXT, t INTEGER)")
        c.execute("INSERT OR REPLACE INTO covers VALUES(?,?,?)", (tid, url or "", int(time.time())))

# ---------- people pages: what they made comes from cast.db (IMDb); portrait, dates and a short bio from Wikidata + Wikipedia ----------
LANGS = ("en", "fr", "es", "de", "it", "pt", "ar", "tr", "nl", "pl", "ru", "ja", "ko", "zh", "hi")
_UA = {"User-Agent": "NahokoCatalogue/1.0 (self-hosted film catalogue)"}
WDAPI = "https://www.wikidata.org/w/api.php"

def _wget(url, timeout=12):
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r: return json.load(r)

def _commons(fname, w=400):
    """The address of a Wikimedia Commons picture at a given width (the same path Wikipedia uses)."""
    import hashlib
    f = fname.replace(" ", "_"); h = hashlib.md5(f.encode()).hexdigest()
    q = urllib.parse.quote(f)
    return "https://upload.wikimedia.org/wikipedia/commons/thumb/%s/%s/%s/%dpx-%s%s" % (h[0], h[:2], q, w, q, ".png" if f.lower().endswith((".svg", ".tif", ".tiff")) else "")

def _wikidata_person(nm, lang):
    hit = _wget(WDAPI + "?action=query&list=search&srlimit=1&format=json&srsearch=" + urllib.parse.quote("haswbstatement:P345=" + nm))["query"]["search"]
    if not hit: return {}
    q = hit[0]["title"]
    e = _wget(WDAPI + "?action=wbgetentities&format=json&props=claims|sitelinks|descriptions&languages=%s|en&ids=%s" % (lang, q))["entities"][q]
    cl = e.get("claims", {})
    def vals(p):
        return [s["mainsnak"]["datavalue"]["value"] for s in cl.get(p, []) if s.get("mainsnak", {}).get("datavalue")]
    out = {"wd": q, "v": 2}
    for p, k in (("P569", "born"), ("P570", "died")):
        v = vals(p)
        if v and isinstance(v[0], dict) and v[0].get("time"):
            out[k] = v[0]["time"][1:11] if v[0].get("precision", 0) >= 11 else v[0]["time"][1:5]
    img = vals("P18")
    if img: out["pic"] = _commons(img[0])
    ids = [v["id"] for v in vals("P19")[:1] + vals("P106")[:6] if isinstance(v, dict) and v.get("id")]
    if ids:
        lab = _wget(WDAPI + "?action=wbgetentities&format=json&props=labels&languages=%s|en&ids=%s" % (lang, "|".join(ids)))["entities"]
        name = lambda i: ((lab.get(i, {}).get("labels") or {}).get(lang) or (lab.get(i, {}).get("labels") or {}).get("en") or {}).get("value")
        bp = [v["id"] for v in vals("P19")[:1] if isinstance(v, dict)]
        if bp and name(bp[0]): out["place"] = name(bp[0])
        en = lambda i: ((lab.get(i, {}).get("labels") or {}).get("en") or {}).get("value") or ""
        screen = re.compile(r"act|direct|produc|writ|screen|comedian|presenter|host|voice|anim|film|televis|singer|musician|composer|model|stunt|cinemat|editor|novel|author|rapper|dancer|playwright", re.I)
        out["jobs"] = [n for n in (name(v["id"]) for v in vals("P106")[:6] if isinstance(v, dict) and screen.search(en(v["id"]))) if n][:4]      # their screen work, not "aircraft pilot"
    d = (e.get("descriptions") or {}).get(lang) or (e.get("descriptions") or {}).get("en")
    if d: out["desc"] = d["value"]
    sl = e.get("sitelinks") or {}
    for lg in ((lang, "en") if lang != "en" else ("en",)):
        if lg + "wiki" in sl:
            try:
                s = _wget("https://%s.wikipedia.org/api/rest_v1/page/summary/%s" % (lg, urllib.parse.quote(sl[lg + "wiki"]["title"].replace(" ", "_"), safe="")))
                if s.get("extract"):
                    out["bio"] = s["extract"][:1500]; out["wiki"] = (s.get("content_urls") or {}).get("desktop", {}).get("page", ""); out["bio_lang"] = lg
                if not out.get("pic") and (s.get("thumbnail") or {}).get("source"): out["pic"] = s["thumbnail"]["source"]
            except Exception:
                pass
            break
    return out

def _pinfo(nm, lang):
    """Wikidata/Wikipedia facts about a person, kept 30 days (a failed lookup is retried after a day)."""
    try:
        with _titles() as c:
            c.execute("CREATE TABLE IF NOT EXISTS pinfo(nconst TEXT, lang TEXT, v TEXT, t INTEGER, PRIMARY KEY(nconst, lang))")
            r = c.execute("SELECT v, t FROM pinfo WHERE nconst=? AND lang=?", (nm, lang)).fetchone()
        if r:
            v = json.loads(r[0])
            if time.time() - r[1] < (30 * 86400 if v else 3600) and (not v or v.get("v") == 2): return v
    except Exception:
        pass
    try: v = _wikidata_person(nm, lang)
    except Exception: return {}
    try:
        with _titles() as c: c.execute("INSERT OR REPLACE INTO pinfo VALUES(?,?,?,?)", (nm, lang, json.dumps(v), int(time.time())))
    except Exception:
        pass
    return v

def person(nm, lang="en"):
    """One person's page: who they are and every catalogue title they acted in, directed or wrote."""
    cat = load()
    if not cat: return None
    try:
        c = _castdb(); rows = c.execute("SELECT tconst, cat, chars, name FROM people WHERE nconst=?", (nm,)).fetchall()
        name = rows[0][3] if rows else None
        ph = c.execute("SELECT url FROM photo WHERE name=?", (name,)).fetchone() if name else None
        c.close()
    except Exception:
        return None
    if not rows: return None
    roles, seen, gender = {"acting": [], "directing": [], "writing": []}, {}, ""
    for t, k, ch, _ in rows:
        if t not in cat["items"]: continue
        r = "acting" if k in ("actor", "actress") else "directing" if k == "director" else "writing"
        if k in ("actor", "actress"): gender = k
        key = (r, t)
        if key in seen:
            if ch and ch not in (seen[key].get("as") or ""): seen[key]["as"] = ((seen[key].get("as") or "") + " / " + ch).strip(" /")
            continue
        it = _dress(cat, t)
        if ch: it["as"] = ch
        seen[key] = it; roles[r].append(it)
    for r in roles: roles[r].sort(key=lambda i: (-(i.get("y") or 0), -i["v"]))
    every = {i["id"]: i for r in roles.values() for i in r}
    best = sorted(every.values(), key=lambda i: -i["v"])[:12]
    queue_resolve([cat["items"][i] for i in every if known(i) is None][:60])      # posters still missing: fetched for next time
    info = _pinfo(nm, lang if lang in LANGS else "en")
    if not ph and person_lookup:      # never looked up: TMDB's portrait (through Jellyfin), kept like the cast photos
        try: u = person_lookup(name) or ""
        except Exception: u = None
        if u is not None:
            try:
                c = _castdb(); c.execute("INSERT OR REPLACE INTO photo VALUES(?,?,?)", (name, u, int(time.time()))); c.commit(); c.close()
            except Exception: pass
            ph = (u,) if u else None
    pic = ph[0].replace("/w185/", "/h632/").replace("/medium_portrait/", "/original_untouched/") if ph and ph[0] else info.get("pic", "")
    jobs = info.get("jobs") or [{"actor": "Actor", "actress": "Actress"}.get(gender, "")] + (["Director"] if roles["directing"] else []) + (["Writer"] if roles["writing"] else [])
    return {"id": nm, "n": name, "pic": pic, "jobs": [j for j in jobs if j][:4], "born": info.get("born"), "died": info.get("died"),
            "place": info.get("place"), "desc": info.get("desc"), "bio": info.get("bio"), "wiki": info.get("wiki"), "bio_lang": info.get("bio_lang"),
            "wd": info.get("wd"), "known": best, "roles": {k: v for k, v in roles.items() if v}, "count": len(every)}

def people_for_sitemap(limit=30000):
    """The people worth a page of their own: the first-billed cast and the directors of the better-known titles."""
    cat = load()
    if not cat: return []
    good = {i for i, o in cat["items"].items() if o["v"] >= 20000}
    try:
        c = _castdb()
        rows = c.execute("SELECT nconst, name, tconst FROM people WHERE ord<=4 OR cat='director'").fetchall(); c.close()
    except Exception:
        return []
    score = {}
    for nm, n, t in rows:
        if t in good:
            s = score.setdefault(nm, [n, 0]); s[1] += cat["items"][t]["v"]
    return [(nm, s[0]) for nm, s in sorted(score.items(), key=lambda x: -x[1][1])[:limit]]

# ---------- a title in another language: its name and description (TMDB when a key is set, else Wikidata + Wikipedia) ----------
TMDB_KEY = os.environ.get("TMDB_KEY", "")      # optional: a free TMDB key gives real synopses in every language

def _tmdb_i18n(tid, lang):
    u = "https://api.themoviedb.org/3/find/%s?external_source=imdb_id&language=%s" % (tid, lang)
    hd = dict(_UA)
    if len(TMDB_KEY) > 40: hd["Authorization"] = "Bearer " + TMDB_KEY      # a "read access token"
    else: u += "&api_key=" + TMDB_KEY
    with urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=10) as r: d = json.load(r)
    x = (d.get("movie_results") or d.get("tv_results") or [None])[0]
    if not x: return {}
    return {"n": x.get("title") or x.get("name") or "", "o": x.get("overview") or "", "src": "tmdb"}

def _wiki_i18n(tid, lang):
    hit = _wget(WDAPI + "?action=query&list=search&srlimit=1&format=json&srsearch=" + urllib.parse.quote("haswbstatement:P345=" + tid))["query"]["search"]
    if not hit: return {}
    q = hit[0]["title"]
    e = _wget(WDAPI + "?action=wbgetentities&format=json&props=labels|sitelinks&languages=%s&ids=%s" % (lang, q))["entities"][q]
    out = {"n": ((e.get("labels") or {}).get(lang) or {}).get("value", ""), "src": "wikipedia"}
    sl = (e.get("sitelinks") or {}).get(lang + "wiki")
    if sl:
        try:
            s = _wget("https://%s.wikipedia.org/api/rest_v1/page/summary/%s" % (lang, urllib.parse.quote(sl["title"].replace(" ", "_"), safe="")))
            out["o"] = (s.get("extract") or "")[:900]; out["wiki"] = (s.get("content_urls") or {}).get("desktop", {}).get("page", "")
        except Exception:
            pass
    return out

def title_i18n(tid, lang):
    """{"n": localized name, "o": localized description, "src": ...} or {} — kept 60 days (a miss is retried after 3 days)."""
    if lang not in LANGS or lang == "en": return {}
    try:
        with _titles() as c:
            c.execute("CREATE TABLE IF NOT EXISTS tl(id TEXT, lang TEXT, v TEXT, t INTEGER, PRIMARY KEY(id, lang))")
            r = c.execute("SELECT v, t FROM tl WHERE id=? AND lang=?", (tid, lang)).fetchone()
        if r:
            v = json.loads(r[0])
            if time.time() - r[1] < (60 * 86400 if v.get("o") else 3 * 86400 if v else 3600) and (v.get("src") == "tmdb" or not TMDB_KEY): return v
    except Exception:
        pass
    v = {}
    try: v = _tmdb_i18n(tid, lang) if TMDB_KEY else {}
    except Exception: v = {}
    if not v.get("o"):
        try:
            w = _wiki_i18n(tid, lang)
            v = {"n": v.get("n") or w.get("n", ""), "o": w.get("o", ""), "src": w.get("src", ""), "wiki": w.get("wiki", "")} if w else v
        except Exception:
            pass
    try:
        with _titles() as c: c.execute("INSERT OR REPLACE INTO tl VALUES(?,?,?,?)", (tid, lang, json.dumps(v), int(time.time())))
    except Exception:
        pass
    return v

def title_i18n_cached(tid, lang):
    """What we already know of a title in a language (never asks the network: used to write addresses)."""
    if lang == "en": return {}
    try:
        with _titles() as c:
            r = c.execute("SELECT v FROM tl WHERE id=? AND lang=?", (tid, lang)).fetchone()
        return json.loads(r[0]) if r else {}
    except Exception:
        return {}

_pname = {}
def person_name(nm):
    """A person's name from cast.db (None when they are not in the catalogue)."""
    if nm in _pname: return _pname[nm]
    try:
        c = _castdb(); r = c.execute("SELECT name FROM people WHERE nconst=? LIMIT 1", (nm,)).fetchone(); c.close()
    except Exception:
        return None
    if len(_pname) > 50000: _pname.clear()
    _pname[nm] = r[0] if r else None
    return _pname[nm]

# ---------- people in the search suggestions: the 30,000 best-known, matched by the start of a name ----------
_pidx = {"t": 0, "v": [], "busy": False}
def _pidx_build():
    try: _pidx["v"] = [(_norm(n), nm, n) for nm, n in people_for_sitemap()]; _pidx["t"] = time.time()
    finally: _pidx["busy"] = False

def people_find(q, limit=4):
    if (not _pidx["v"] or time.time() - _pidx["t"] > 12 * 3600) and not _pidx["busy"]:
        _pidx["busy"] = True; threading.Thread(target=_pidx_build, daemon=True).start()      # built once in the background
    nq = _norm(q)
    if len(nq) < 2: return []
    out = []
    for key, nm, n in _pidx["v"]:
        if key.startswith(nq) or (" " + nq) in (" " + key):
            out.append({"id": nm, "n": n})
            if len(out) >= limit: break
    if out:
        try:
            c = _castdb(); ph = dict(c.execute("SELECT name, url FROM photo WHERE name IN (%s)" % ",".join("?" * len(out)), [x["n"] for x in out]).fetchall()); c.close()
            for x in out:
                if ph.get(x["n"]): x["p"] = ph[x["n"]]
        except Exception:
            pass
    return out

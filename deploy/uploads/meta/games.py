"""Games for the catalogue (browse, search, filters, favorites; no requests, no lucky pick), from free sources without a key:

  Wikidata   every notable game on PC, PlayStation 5/4, Xbox Series/One and Switch that has an English Wikipedia article:
             platforms, release date, genres, developer, and how many Wikipedias write about it (its popularity)
  Wikipedia  the cover (the article's main picture), a short description, and the Metacritic score from the review box

The list is rebuilt weekly in the background (Wikidata: one query every few seconds). Covers, descriptions and scores are
then read from Wikipedia, most popular games first, and kept in games.db. A game is shown once its cover is known.
The interface mirrors catalog.py: view (shelves, one shelf, inside filters), find, filters_info, item."""
import html, json, math, os, re, sqlite3, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import date

DBDIR = os.environ.get("DB_DIR", "/db")
LIST = os.path.join(DBDIR, "games-wd.json")
GDB = os.path.join(DBDIR, "games.db")
MAXAGE = 7 * 86400
HOME_N = 16
UA = "NahokoCatalogue/1.3 (https://github.com/ElNahoko/selfhost-jellyfin; self-hosted media catalogue)"
PLATFORMS = [("PC", "PC", ["Q1406"]), ("PS5", "PlayStation 5", ["Q63184502"]), ("PS4", "PlayStation 4", ["Q5014725"]),
             ("XBX", "Xbox", ["Q98973368", "Q64513817", "Q13361286"]), ("SW", "Nintendo Switch", ["Q19610114", "Q122761124"])]
PNAME = {c: n for c, n, _ in PLATFORMS}
ADULT = re.compile(r"eroge|hentai|adult|pornograph|erotic", re.I)

_m = {"items": None, "t": 0, "raw": None, "mt": 0, "idx": None}
_st = {"building": False, "t": 0, "enriching": False, "et": 0, "error": ""}
_lock = threading.Lock()

def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=timeout) as r:
        return json.load(r)

def _sparql(q):
    for attempt in range(5):
        try:
            return _get("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q), 120)["results"]["bindings"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                try: wait = int(e.headers.get("Retry-After") or 60)
                except ValueError: wait = 60
                time.sleep(min(wait, 600) + 2); continue
            if attempt >= 2: raise
            time.sleep(20)
        except Exception:
            if attempt >= 2: raise
            time.sleep(20)
    return []

def _db():
    c = sqlite3.connect(GDB, timeout=30)
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("CREATE TABLE IF NOT EXISTS wiki(qid TEXT PRIMARY KEY, img TEXT, o TEXT, mc INTEGER, t1 INTEGER, t2 INTEGER)")
    return c

def _clean_genre(g):
    g = re.sub(r"\b(video )?game\b", "", g, flags=re.I).strip(" -")
    return g[:1].upper() + g[1:] if g else ""

# ---------- the list (weekly) ----------
def _build_job():
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        games = {}
        for code, _, qs in PLATFORMS:      # which games run on which platform, and how widely known they are
            rows = _sparql("SELECT ?g ?sl ?art WHERE { VALUES ?p { %s } ?g wdt:P400 ?p; wdt:P31 wd:Q7889; wikibase:sitelinks ?sl. "
                           "?art schema:about ?g; schema:isPartOf <https://en.wikipedia.org/>. }" % " ".join("wd:" + q for q in qs))
            for r in rows:
                q = r["g"]["value"].rsplit("/", 1)[-1]
                g = games.setdefault(q, {"q": q, "p": [], "sl": int(r["sl"]["value"]),
                                         "wp": urllib.parse.unquote(r["art"]["value"].rsplit("/wiki/", 1)[-1]).replace("_", " ")})
                if code not in g["p"]: g["p"].append(code)
            time.sleep(4)
        ids = sorted(games, key=lambda q: -games[q]["sl"])
        for k in range(0, len(ids), 250):      # names, release dates, genres, developers
            rows = _sparql("SELECT ?g ?gLabel ?date ?genreLabel ?devLabel WHERE { VALUES ?g { %s } "
                           "OPTIONAL { ?g wdt:P577 ?date } OPTIONAL { ?g wdt:P136 ?genre } OPTIONAL { ?g wdt:P178 ?dev } "
                           "SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } }" % " ".join("wd:" + q for q in ids[k:k + 250]))
            for r in rows:
                g = games[r["g"]["value"].rsplit("/", 1)[-1]]
                g["n"] = (r.get("gLabel") or {}).get("value") or g.get("n") or g["wp"]
                d = (r.get("date") or {}).get("value", "")[:10]
                if re.fullmatch(r"\d{4}-\d\d-\d\d", d) and (not g.get("d") or d < g["d"]): g["d"] = d
                gn = (r.get("genreLabel") or {}).get("value", "")
                if gn and not re.fullmatch(r"Q\d+", gn) and gn not in g.setdefault("gr", []): g["gr"].append(gn)
                dv = (r.get("devLabel") or {}).get("value", "")
                if dv and not re.fullmatch(r"Q\d+", dv) and dv not in g.setdefault("dv", []) and len(g["dv"]) < 3: g["dv"].append(dv)
            time.sleep(3)
        games = {q: g for q, g in games.items() if not any(ADULT.search(x) for x in g.get("gr", []))}
        if len(games) > 1000:
            tmp = LIST + ".tmp"
            with open(tmp, "w") as f: json.dump({"built": int(time.time()), "games": games}, f, separators=(",", ":"))
            os.replace(tmp, LIST)
        _st["error"] = ""
    except Exception as e:
        _st["error"] = ("games: " + str(e))[:200]
    finally:
        _st["building"] = False

def _ensure():
    now = time.time()
    if not _st["building"] and now - _st["t"] > 600:
        _st["t"] = now
        try: fresh = now - os.path.getmtime(LIST) < MAXAGE
        except OSError: fresh = False
        if not fresh:
            _st["building"] = True
            threading.Thread(target=_build_job, daemon=True).start()
    if not _st["enriching"] and now - _st["et"] > 120 and _raw():
        _st["et"] = now; _st["enriching"] = True
        threading.Thread(target=_enrich_job, daemon=True).start()

# ---------- covers, descriptions and Metacritic scores from Wikipedia (background, most popular first) ----------
WAPI = "https://en.wikipedia.org/w/api.php"

def _wapi(params):
    params = dict(params, format="json", formatversion="2", redirects="1")
    for attempt in range(4):
        try:
            return _get(WAPI + "?" + urllib.parse.urlencode(params), 60)
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(60); continue
            if attempt >= 1: raise
            time.sleep(10)
        except Exception:
            if attempt >= 2: raise
            time.sleep(10)
    return {}

def _pages(d):
    """title -> page, following the redirects and normalisations the API reports."""
    q = d.get("query") or {}
    alias = {}
    for k in ("normalized", "redirects"):
        for x in q.get(k) or []: alias[x["from"]] = x["to"]
    by = {p.get("title"): p for p in q.get("pages") or []}
    def look(t):
        for _ in range(3): t = alias.get(t, t)
        return by.get(t)
    return look

def _mc(text):
    """Metacritic score from the article's review box ({{Video game reviews | MC = PS5: 92/100 ...}}), else OpenCritic."""
    for key in ("MC", "OC"):
        m = re.search(r"\|\s*%s\s*=([^|]*)" % key, text)
        if m:
            nums = [int(x) for x in re.findall(r"\b(\d{2,3})\s*/\s*100", m.group(1)) if 10 <= int(x) <= 100]
            if nums: return round(sum(nums) / len(nums))
    return None

def _enrich_job():
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        raw = _raw()
        if not raw: return
        c = _db()
        have = {r[0]: r for r in c.execute("SELECT qid, img, o, mc, t1, t2 FROM wiki")}
        order = sorted(raw.values(), key=lambda g: -g["sl"])
        todo1 = [g for g in order if g["q"] not in have or not have[g["q"]][4]]
        for k in range(0, len(todo1), 20):      # 1) cover + description, 20 articles a request
            chunk = todo1[k:k + 20]
            d = _wapi({"action": "query", "prop": "pageimages|extracts", "piprop": "thumbnail", "pithumbsize": "500", "pilicense": "any",
                       "exintro": "1", "explaintext": "1", "exsentences": "3", "exlimit": "20", "titles": "|".join(g["wp"] for g in chunk)})
            look = _pages(d); now = int(time.time())
            for g in chunk:
                p = look(g["wp"]) or {}
                c.execute("INSERT INTO wiki(qid, img, o, t1) VALUES(?,?,?,?) ON CONFLICT(qid) DO UPDATE SET img=excluded.img, o=excluded.o, t1=excluded.t1",
                          (g["q"], (p.get("thumbnail") or {}).get("source") or "", (p.get("extract") or "")[:700], now))
            c.commit(); _m["t"] = 0 if k < 200 else _m["t"]
            time.sleep(1)
        have = {r[0]: r for r in c.execute("SELECT qid, img, o, mc, t1, t2 FROM wiki")}
        todo2 = [g for g in order if g["sl"] >= 3 and have.get(g["q"]) and have[g["q"]][1] and not have[g["q"]][5]]
        for k in range(0, len(todo2), 8):       # 2) the Metacritic score from the review box (whole article text, 8 a request)
            chunk = todo2[k:k + 8]
            d = _wapi({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": "|".join(g["wp"] for g in chunk)})
            look = _pages(d); now = int(time.time())
            for g in chunk:
                p = look(g["wp"]) or {}
                rv = (p.get("revisions") or [{}])[0]
                text = ((rv.get("slots") or {}).get("main") or {}).get("content") or rv.get("content") or ""
                c.execute("UPDATE wiki SET mc=?, t2=? WHERE qid=?", (_mc(text), now, g["q"]))
            c.commit()
            time.sleep(1.5)
        c.close()
    except Exception as e:
        _st["error"] = ("games details: " + str(e))[:200]
    finally:
        _st["enriching"] = False

# ---------- items in memory ----------
def _raw():
    try: mt = os.path.getmtime(LIST)
    except OSError: return None
    if _m["raw"] is None or mt != _m["mt"]:
        with open(LIST) as f: _m["raw"] = json.load(f)["games"]
        _m["mt"] = mt; _m["t"] = 0
    return _m["raw"]

def _items():
    """Every game with a cover, merged with its Wikipedia details (refreshed every 5 minutes while details arrive)."""
    raw = _raw()
    if raw is None: return None
    if _m["items"] is not None and time.time() - _m["t"] < 300: return _m["items"]
    with _lock:
        try:
            c = _db(); info = {r[0]: r for r in c.execute("SELECT qid, img, o, mc FROM wiki WHERE img != ''")}; c.close()
        except Exception:
            info = {}
        items = {}
        for g in raw.values():
            w = info.get(g["q"])
            if not w: continue
            d = g.get("d") or ""
            gen = []
            for x in g.get("gr", []):
                x = _clean_genre(x)
                if x and x not in gen: gen.append(x)
            mc = w[3]
            nm = g.get("n") or ""
            if not nm or re.fullmatch(r"Q\d+", nm): nm = re.sub(r"\s*\((video )?game\)$", "", g["wp"])      # no English label: the article title
            it = {"id": "wg" + g["q"][1:], "k": "game", "n": nm, "y": int(d[:4]) if d else None, "d": d, "g": gen[:4],
                  "r": round(mc / 10, 1) if mc else None, "mc": mc, "pop": g["sl"], "img": w[1], "o": html.unescape(w[2] or ""),
                  "dev": g["dv"][0] if len(g.get("dv", [])) == 1 else "",      # several studios come unordered: none is shown
                  "p": g["p"], "wp": g["wp"]}
            it["w"] = round((mc / 10 if mc else 6.4) + 0.9 * math.log10(g["sl"] + 1), 3)      # quality, lifted by how widely known it is
            items[it["id"]] = it
        _m["items"] = items; _m["t"] = time.time(); _m["idx"] = None
        return items

def _rules():
    today = date.today().isoformat()
    yago = date.fromordinal(date.today().toordinal() - 365).isoformat()
    on = lambda i, p: p in i["p"]
    mc = lambda i, n: (i["mc"] or 0) >= n
    R = []
    def add(id_, name, fn, sort, home=False):
        R.append({"id": id_, "name": name, "fn": fn, "sort": sort, "home": home})
    add("new", "New releases", lambda i: yago <= i["d"] <= today, "pop", True)
    add("soon", "Coming soon", lambda i: i["d"] > today, "date", True)
    add("top", "Top rated", lambda i: mc(i, 85), "w", True)
    add("popular", "Everyone plays these", lambda i: True, "pop", True)
    add("ps5", "Best on PlayStation 5", lambda i: on(i, "PS5") and mc(i, 75), "w", True)
    add("xbox", "Best on Xbox", lambda i: on(i, "XBX") and mc(i, 75), "w", True)
    add("pc", "Best on PC", lambda i: on(i, "PC") and mc(i, 80), "w", True)
    add("switch", "Best on Switch", lambda i: on(i, "SW") and mc(i, 75), "w", True)
    add("gems", "Hidden gems", lambda i: mc(i, 84) and i["pop"] <= 12, "mc", True)
    add("classics", "Classics", lambda i: i["y"] and i["y"] <= 2010 and mc(i, 88), "w", True)
    for gn, label in (("Action-adventure", "Action-adventure"), ("Role-playing", "Role-playing"), ("First-person shooter", "Shooters"),
                      ("Platform", "Platformers"), ("Racing", "Racing"), ("Sports", "Sports"), ("Fighting", "Fighting"),
                      ("Survival horror", "Survival horror"), ("Strategy", "Strategy"), ("Puzzle", "Puzzle"), ("Simulation", "Simulation")):
        add("g-" + re.sub(r"[^a-z]", "", gn.lower()), label, (lambda x: lambda i: any(x.lower() in g.lower() for g in i["g"]))(gn), "w")
    return R

def _dnum(i): return int(i["d"].replace("-", "")) if i["d"] else 0

KEY = {"w": lambda i: (-i["w"], -i["pop"]), "pop": lambda i: (-i["pop"], -(i["mc"] or 0)), "mc": lambda i: (-(i["mc"] or 0), -i["pop"]),
       "date": lambda i: (_dnum(i), -i["pop"])}
SORTS = {"rating": lambda i: (-(i["mc"] or 0), -i["pop"]), "votes": lambda i: (-i["pop"], -(i["mc"] or 0)),
         "newest": lambda i: (-_dnum(i), -i["pop"]), "name": lambda i: (i["n"].lower(),), "best": KEY["w"]}
BY = {"w": "best", "pop": "votes", "mc": "rating", "date": "newest"}

def _pool(items, f):
    f = f or {}
    dec = f.get("decade")
    out = []
    for it in items.values():
        if f.get("platform") and f["platform"] not in it["p"]: continue
        if f.get("genre") and f["genre"] not in it["g"]: continue
        if dec and not (it["y"] and dec <= it["y"] <= dec + 9): continue
        if f.get("min") and (it["r"] or 0) < f["min"]: continue
        out.append(it)
    return out

def _out(it):
    o = {k: it[k] for k in ("id", "k", "n", "y", "g", "img", "o", "dev", "mc", "wp") if it.get(k) is not None}
    if it["r"]: o["r"] = it["r"]
    o["plat"] = [PNAME[p] for p in it["p"]]
    if it["d"] > date.today().isoformat(): o["soon"] = it["d"]
    return o

def view(row=None, sort=None, offset=0, limit=40, flt=None):
    _ensure()
    items = _items()
    if not items: return {"building": True, "rows": [], "chips": []}
    pool = _pool(items, flt)
    rules = _rules()
    chips = [{"id": r["id"], "name": r["name"], "home": r["home"]} for r in rules]
    if row == "__f" and not flt: row = None
    if row:
        r = next((r for r in rules if r["id"] == row), None)
        its = [i for i in pool if r["fn"](i)] if r else pool
        by = BY[r["sort"]] if r else "best"
        sort = sort if sort in SORTS else by
        its.sort(key=KEY[r["sort"]] if r and sort == by else SORTS[sort])
        page = its[offset:offset + limit]
        return {"rows": [{"id": row, "name": r["name"] if r else "All games", "items": [_out(i) for i in page]}], "chips": chips,
                "total": len(its), "offset": offset, "sort": sort, "default": by, "ready": True}
    out = []
    for r in rules:
        if not r["home"]: continue
        its = sorted((i for i in pool if r["fn"](i)), key=KEY[r["sort"]])[:HOME_N]
        if len(its) >= 6: out.append({"id": r["id"], "name": r["name"], "items": [_out(i) for i in its]})
    if not out and pool:
        out.append({"id": "__f", "name": "All games", "items": [_out(i) for i in sorted(pool, key=KEY["w"])[:HOME_N]]})
    return {"building": _st["building"], "ready": True, "rows": out, "chips": chips, "total_titles": len(pool)}

def view_filter(f, sort=None, offset=0, limit=40):
    pool = _pool(_items() or {}, f)
    sort = sort if sort in SORTS else "best"
    pool.sort(key=SORTS[sort])
    return {"rows": [{"id": "filter", "name": "Results", "items": [_out(i) for i in pool[offset:offset + limit]]}], "total": len(pool),
            "offset": offset, "sort": sort, "default": "best", "ready": True, "countries_ready": True}

def _norm(t): return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

def find(q, limit=24):
    items = _items()
    if not items: return []
    if _m["idx"] is None: _m["idx"] = [(_norm(i["n"]), i) for i in items.values()]
    nq = _norm(q); toks = nq.split()
    if not toks: return []
    hits = []
    for name, it in _m["idx"]:
        if name == nq: sc = 100
        elif name.startswith(nq): sc = 80
        elif all(any(w.startswith(t) for w in name.split()) for t in toks): sc = 60
        elif nq in name: sc = 50
        else: continue
        hits.append((sc, it))
    hits.sort(key=lambda x: (-x[0], -x[1]["pop"]))
    return [_out(it) for _, it in hits[:limit]]

def filters_info():
    items = _items() or {}
    gs = {}
    for it in items.values():
        for g in it["g"]: gs[g] = gs.get(g, 0) + 1
    return {"countries": [{"id": c, "name": n, "platform": True} for c, n, _ in PLATFORMS],
            "genres": sorted(g for g, n in gs.items() if n >= 25), "decades": list(range(2020, 1979, -10))}

def item(i):
    it = (_items() or {}).get(i)
    return _out(it) if it else None

def status():
    c = _db(); n, covers, scored = c.execute("SELECT count(*), sum(img != ''), sum(mc IS NOT NULL) FROM wiki").fetchone(); c.close()
    return {"listed": len(_raw() or {}), "looked_up": n, "with_cover": covers or 0, "with_score": scored or 0,
            "building": _st["building"], "enriching": _st["enriching"], "error": _st["error"]}

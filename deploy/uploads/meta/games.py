"""Games for the catalogue (browse and favorites; no requests, no lucky pick): the most owned Steam games.

The list comes from SteamSpy (free, no key, one page of 1,000 games a minute) and is rebuilt weekly in the background.
Year, genres, description, developer and the adult-content flags come from the Steam store, looked up a game at a time
in the background (the store allows about 200 lookups per 5 minutes), most popular first and kept in games.db.
A game appears only once its store page has been read, so adult games are never shown by mistake.
The interface mirrors catalog.py: view (shelves, one shelf, inside filters), find, filters_info, item (no lucky pick for games)."""
import html, json, os, re, sqlite3, threading, time, urllib.error, urllib.request
from datetime import date

DBDIR = os.environ.get("DB_DIR", "/db")
LIST = os.path.join(DBDIR, "games.json")
GDB = os.path.join(DBDIR, "games.db")
PAGES = 8                 # 8,000 most owned games
MIN_REVIEWS = 1500        # ... with enough reviews for the score to mean something
MAXAGE = 7 * 86400
HOME_N = 16
UA = "NahokoCatalogue/1.2 (https://github.com/ElNahoko/selfhost-jellyfin; self-hosted media catalogue)"
CDN = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/%d/library_600x900.jpg"
NOT_GENRES = {"Free To Play", "Free to Play", "Early Access", "Indie"}
ADULT = {3, 4}            # Steam content descriptors: adult-only sexual content, frequent nudity

_m = {"items": None, "t": 0, "mt": 0, "rows": None, "idx": None}
_st = {"building": False, "t": 0, "enriching": False, "et": 0, "error": ""}
_lock = threading.Lock()

def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=timeout) as r:
        return json.load(r)

def _db():
    c = sqlite3.connect(GDB, timeout=30)
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("CREATE TABLE IF NOT EXISTS info(appid INTEGER PRIMARY KEY, ok INTEGER, name TEXT, y INTEGER, g TEXT, cats TEXT, dev TEXT, o TEXT, mc INTEGER, t INTEGER)")
    return c

# ---------- the list (weekly) ----------
def _build_job():
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        out = {}
        for p in range(PAGES):
            if p: time.sleep(62)
            d = _get("https://steamspy.com/api.php?request=all&page=%d" % p, 120)
            for x in d.values():
                pos, neg = int(x.get("positive") or 0), int(x.get("negative") or 0)
                if pos + neg < MIN_REVIEWS or not x.get("name"): continue
                out[str(x["appid"])] = {"a": int(x["appid"]), "n": x["name"], "pos": pos, "neg": neg, "ccu": int(x.get("ccu") or 0),
                                        "free": str(x.get("price") or "0") == "0" and str(x.get("initialprice") or "0") == "0", "dev": x.get("developer") or ""}
        if len(out) > 500:
            tmp = LIST + ".tmp"
            with open(tmp, "w") as f: json.dump({"built": int(time.time()), "games": out}, f, separators=(",", ":"))
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
    if not _st["enriching"] and now - _st["et"] > 120 and _m["items"] is not None:
        _st["et"] = now; _st["enriching"] = True
        threading.Thread(target=_enrich_job, daemon=True).start()

# ---------- details from the Steam store (a game at a time, in the background) ----------
def _enrich_job():
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        raw = _raw()
        if not raw: return
        c = _db()
        done = {r[0] for r in c.execute("SELECT appid FROM info")}
        todo = sorted((g for g in raw.values() if g["a"] not in done), key=lambda g: -(g["pos"] + g["neg"]))
        for g in todo:
            try:
                d = _get("https://store.steampowered.com/api/appdetails?appids=%d&l=english&cc=us" % g["a"], 25).get(str(g["a"])) or {}
            except urllib.error.HTTPError as e:
                if e.code in (429, 403): time.sleep(300); continue
                d = {}
            except Exception:
                time.sleep(20); continue
            x = d.get("data") or {}
            ok = 1 if d.get("success") and x.get("type") == "game" else 0
            desc = (x.get("content_descriptors") or {}).get("ids") or []
            if ok and (set(desc) & ADULT or str(x.get("required_age") or "0") in ("18",) and set(desc) & {1}): ok = 0
            yr = None
            m = re.search(r"(19|20)\d\d", (x.get("release_date") or {}).get("date") or "")
            if m: yr = int(m.group(0))
            gen = [y.get("description") for y in x.get("genres") or [] if y.get("description")]
            cats = [y.get("description") for y in x.get("categories") or [] if y.get("description")]
            o = html.unescape(re.sub(r"<[^>]+>", "", x.get("short_description") or ""))[:600]
            c.execute("INSERT OR REPLACE INTO info VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (g["a"], ok, x.get("name") or "", yr, json.dumps(gen), json.dumps(cats), ", ".join((x.get("developers") or [])[:2]), o,
                       (x.get("metacritic") or {}).get("score"), int(time.time())))
            c.commit()
            time.sleep(1.6)
        c.close()
    finally:
        _st["enriching"] = False

# ---------- items in memory ----------
def _raw():
    try:
        mt = os.path.getmtime(LIST)
    except OSError:
        return None
    if _m.get("raw") is None or mt != _m["mt"]:
        with open(LIST) as f: _m["raw"] = json.load(f)["games"]
        _m["mt"] = mt
    return _m["raw"]

def _items():
    """Every game whose store page was read, merged with its details (refreshed every 5 minutes while details arrive)."""
    raw = _raw()
    if raw is None: return None
    if _m["items"] is not None and time.time() - _m["t"] < 300: return _m["items"]
    with _lock:
        try:
            c = _db(); info = {r[0]: r for r in c.execute("SELECT * FROM info WHERE ok=1")}; c.close()
        except Exception:
            info = {}
        items = {}
        for g in raw.values():
            r = info.get(g["a"])
            if not r: continue
            v = g["pos"] + g["neg"]; pct = g["pos"] / v
            gen = json.loads(r[4] or "[]")
            it = {"id": "st%d" % g["a"], "k": "game", "n": html.unescape(r[2] or g["n"]), "y": r[3], "g": [x for x in gen if x not in NOT_GENRES][:4],
                  "r": round(pct * 10, 1), "v": v, "img": CDN % g["a"], "o": html.unescape(r[7] or ""), "dev": r[6] or g["dev"],
                  "free": g["free"] or "Free To Play" in gen or "Free to Play" in gen, "indie": "Indie" in gen, "ccu": g["ccu"],
                  "cats": json.loads(r[5] or "[]"), "mc": r[8]}
            m = 6000.0
            it["w"] = round((v / (v + m)) * it["r"] + (m / (v + m)) * 7.6, 3)
            items[it["id"]] = it
        _m["items"] = items; _m["t"] = time.time(); _m["rows"] = None; _m["idx"] = None
        return items

def _rules():
    this = date.today().year
    has = lambda i, *cs: any(c in i["cats"] for c in cs)
    g = lambda i, *gs: any(x in i["g"] for x in gs)
    R = []
    def add(id_, name, fn, sort, home=False):
        R.append({"id": id_, "name": name, "fn": fn, "sort": sort, "home": home})
    add("new", "New and notable", lambda i: i["y"] and i["y"] >= this - 1 and i["r"] >= 7.5, "v", True)
    add("top", "Top rated", lambda i: i["v"] >= 8000, "w", True)
    add("popular", "Played right now", lambda i: i["ccu"] > 0, "ccu", True)
    add("free", "Free to play", lambda i: i["free"], "v", True)
    add("gems", "Hidden gems", lambda i: i["r"] >= 9.0 and i["v"] <= 25000, "w", True)
    add("coop", "Play with friends", lambda i: has(i, "Online Co-op", "Co-op", "Online PvP", "Multi-player"), "w", True)
    add("solo", "Great on your own", lambda i: has(i, "Single-player") and not has(i, "Multi-player", "MMO") and g(i, "Adventure", "RPG", "Action"), "w", True)
    add("indie", "Indie favorites", lambda i: i["indie"], "w", True)
    add("critics", "Critics' picks", lambda i: (i["mc"] or 0) >= 85, "mc", True)
    add("classics", "Classics", lambda i: i["y"] and i["y"] <= 2012 and i["r"] >= 8.5, "w", True)
    for gn in ("Action", "Adventure", "RPG", "Strategy", "Simulation", "Casual", "Sports", "Racing", "Massively Multiplayer"):
        add("g-" + gn.lower().replace(" ", ""), "MMO" if gn == "Massively Multiplayer" else gn, (lambda x: lambda i: x in i["g"])(gn), "w")
    return R

KEY = {"w": lambda i: (-i["w"], -i["v"]), "v": lambda i: (-i["v"], -i["r"]), "ccu": lambda i: (-i["ccu"], -i["v"]),
       "mc": lambda i: (-(i["mc"] or 0), -i["w"])}
SORTS = {"rating": lambda i: (-i["r"], -i["v"]), "votes": lambda i: (-i["v"], -i["r"]), "newest": lambda i: (-(i["y"] or 0), -i["v"]),
         "name": lambda i: (i["n"].lower(),), "best": lambda i: (-i["w"], -i["v"])}
BY = {"w": "best", "v": "votes", "ccu": "votes", "mc": "best"}

def _pool(items, f):
    out = []
    dec = f.get("decade") if f else None
    for it in items.values():
        if f:
            if f.get("genre") and f["genre"] not in it["g"]: continue
            if dec and not (it["y"] and dec <= it["y"] <= dec + 9): continue
            if f.get("min") and it["r"] < f["min"]: continue
        out.append(it)
    return out

def _out(it):
    return {k: it[k] for k in ("id", "k", "n", "y", "g", "r", "v", "img", "o", "dev", "free") if k in it}

def view(row=None, sort=None, offset=0, limit=40, flt=None):
    _ensure()
    items = _items()
    if not items: return {"building": True, "rows": [], "chips": []}
    _ensure()
    pool = _pool(items, flt)
    rules = _rules()
    chips = [{"id": r["id"], "name": r["name"], "home": r["home"]} for r in rules]
    if row == "__f": row = None if not flt else "__f"
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
        out.append({"id": "__f", "name": "All games", "items": [_out(i) for i in sorted(pool, key=SORTS["best"])[:HOME_N]]})
    return {"building": _st["building"], "ready": True, "rows": out, "chips": chips, "total_titles": len(pool)}

def view_filter(f, sort=None, offset=0, limit=40):
    items = _items() or {}
    pool = _pool(items, f)
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
    hits.sort(key=lambda x: (-x[0], -x[1]["v"]))
    return [_out(it) for _, it in hits[:limit]]

def filters_info():
    items = _items() or {}
    gs = {}
    for it in items.values():
        for g in it["g"]: gs[g] = gs.get(g, 0) + 1
    return {"countries": [], "genres": sorted(g for g, n in gs.items() if n >= 15), "decades": list(range(2020, 1979, -10))}

def item(i):
    items = _items() or {}
    it = items.get(i)
    if not it: return None
    out = _out(it); out["cats"] = it["cats"][:8]; out["mc"] = it["mc"]
    return out

def status():
    c = _db(); n, ok = c.execute("SELECT count(*), sum(ok) FROM info").fetchone(); c.close()
    raw = _raw() or {}
    return {"listed": len(raw), "checked": n, "shown": ok or 0, "building": _st["building"], "enriching": _st["enriching"], "error": _st["error"]}

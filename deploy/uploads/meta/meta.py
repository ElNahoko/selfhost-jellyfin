#!/usr/bin/env python3
"""Backend for the Nahoko upload and catalogue site (reachable only through Caddy). Sign-in lives in auth.py.
Roles (enforced here on every call): admin = everything; uploader = upload + library; member = email sign-in, favorites,
requests once approved; no session = public catalogue (read only).
  GET /login, POST /auth/login and /auth/logout, GET /auth/check (Caddy asks this before serving anything else)
  GET  /_meta/items            library metadata from Jellyfin, keyed by upload-page path
  GET  /_meta/img/<id>?w=      a library poster (proxied from Jellyfin)
  GET  /_meta/space            disk usage
  GET  /_meta/sizes            folder sizes {"/shows/Name": [bytes, files], ...}
  GET  /_meta/search?type=movie|series&q=   title search through Jellyfin's TMDb provider
  GET  /_meta/rimg?u=<tmdb url>             search-result poster (only image.tmdb.org is allowed)
  GET  /_meta/catalog?type=movie|series     browse rows (top rated, popular, genres) from IMDb datasets
  GET  /_meta/title?id=tt...                one catalogue title with plot + poster
  GET  /_meta/stats                         server CPU and memory
  GET/POST /_meta/requests, POST/DELETE /_meta/requests/<id>   the request list (SQLite file in /db)
Writes need a JSON body and a same-site Origin, so another website cannot trigger them with the browser's saved login.
"""
import hashlib, json, os, re, shutil, sqlite3, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import auth, catalog, games, mail
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

JF = os.environ.get("JELLYFIN_URL", "http://jellyfin:8096")
KEY = os.environ["JELLYFIN_API_KEY"]
DATA = os.environ.get("DATA_DIR", "/data")
DB = os.environ.get("DB_PATH", "/db/requests.db")
TYPES = "Movie,Series,Season,Episode,MusicAlbum,MusicArtist,AudioBook,Book"
_lock = threading.Lock()
_items = {"t": 0, "body": b"{}"}
_sizes = {"t": 0, "body": b"{}"}

def jf(path, data=None, timeout=30, method=None):
    h = {"Authorization": 'MediaBrowser Token="%s"' % KEY}
    if data is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    return urllib.request.urlopen(urllib.request.Request(JF + path, data=data, headers=h, method=method), timeout=timeout)

# ---------- library metadata ----------
def build_items():
    d = json.load(jf("/Items?Recursive=true&IncludeItemTypes=%s&Fields=Path,Overview,ProductionYear,CommunityRating,ProviderIds"
                     "&Limit=20000&EnableTotalRecordCount=false" % TYPES))
    out = {}
    for it in d.get("Items", []):
        p = it.get("Path") or ""
        if not p.startswith("/media/"):
            continue
        p = p[len("/media"):]
        t = it.get("Type", "")
        has_img = bool((it.get("ImageTags") or {}).get("Primary"))
        rec = {"id": it["Id"] if has_img else (it.get("SeriesId") if t == "Season" else None),
               "n": it.get("Name", ""), "y": it.get("ProductionYear"), "r": it.get("CommunityRating"),
               "o": (it.get("Overview") or "")[:500], "t": t,
               "i": it.get("IndexNumber"), "s": it.get("ParentIndexNumber")}
        if t in ("Movie", "Series"):
            pid = it.get("ProviderIds") or {}
            rec["u"] = 0 if (pid.get("Tmdb") or pid.get("Imdb") or pid.get("Tvdb")) else 1      # 1 = Jellyfin could not match it to a real title (its picture may be a random video frame)
        out[p] = rec
        if t == "Movie":                       # movies live in "Title (Year)/Title.mkv": the folder gets the card too
            parent = p.rsplit("/", 1)[0]
            if parent.count("/") >= 2:
                out.setdefault(parent, rec)
    return out

def cached(store, build, ttl):
    with _lock:
        if time.time() - store["t"] > ttl:
            try:
                store["body"] = json.dumps(build(), separators=(",", ":")).encode()
                store["t"] = time.time()
            except Exception:
                store["t"] = time.time() - ttl + 10     # retry soon, keep the last good copy
        return store["body"]

def build_sizes():
    out = {}
    def walk(path, rel, depth):
        total = files = 0
        try:
            with os.scandir(path) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        b, f = walk(e.path, rel + "/" + e.name, depth + 1)
                        total += b; files += f
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat().st_size; files += 1
        except OSError:
            pass
        if depth >= 2:
            out[rel] = [total, files]
        return total, files
    for lib in ("movies", "shows", "music", "audiobooks"):
        walk(os.path.join(DATA, lib), "/" + lib, 1)
    return out

# ---------- title search (for requests) ----------
def search(kind, q):
    ep = "Movie" if kind == "movie" else "Series"
    r = json.load(jf("/Items/RemoteSearch/" + ep, {"SearchInfo": {"Name": q}, "IncludeDisabledProviders": False}, timeout=25))
    out = []
    for x in r[:20]:
        img = x.get("ImageUrl") or ""
        if urlparse(img).hostname != "image.tmdb.org":
            img = ""
        out.append({"n": x.get("Name", ""), "y": x.get("ProductionYear"), "img": img, "o": (x.get("Overview") or "")[:300]})
    return out

def fetch_poster(src, w):
    """A poster (TMDb at a given width, TVmaze, Wikipedia game covers), cached on disk (so a page never waits on TMDb for a title it has seen)."""
    src = re.sub(r"/t/p/[^/]+/", "/t/p/w%s/" % w, src, 1)
    fp = os.path.join("/db/img", hashlib.sha1(src.encode()).hexdigest() + ".jpg")
    if not os.path.exists(fp):
        os.makedirs("/db/img", exist_ok=True)
        data = urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": games.UA}), timeout=15).read(3_000_000)      # Wikimedia wants a real name
        with open(fp + ".tmp", "wb") as f: f.write(data)
        os.replace(fp + ".tmp", fp)
    return fp

def resolve_one(it):
    """Poster + plot for a catalogue title via Jellyfin's TMDb lookup (by IMDb id, else name+year)."""
    ep = "Movie" if it["k"] == "movie" else "Series"
    for info in ({"ProviderIds": {"Imdb": it["id"]}, "Name": it["n"], "Year": it["y"]}, {"Name": it["n"], "Year": it["y"]}):
        try:
            r = json.load(jf("/Items/RemoteSearch/" + ep, {"SearchInfo": info, "IncludeDisabledProviders": False}, timeout=25))
        except Exception:
            continue
        for x in r:
            img = x.get("ImageUrl") or ""
            if urlparse(img).hostname == "image.tmdb.org":
                catalog.save_title(it["id"], img, (x.get("Overview") or "")[:600])
                try: fetch_poster(img, "185")
                except Exception: pass
                return
    catalog._failed[it["id"]] = time.time()      # try again later, never block on it

def resolve_many(items):
    todo = [i for i in items if catalog.known(i["id"]) is None]
    with ThreadPoolExecutor(5) as ex:
        list(ex.map(resolve_one, todo))
catalog.set_resolver(resolve_many)

def person_photo(name):
    """A portrait from TMDB, through Jellyfin's person search (only an exact name match counts)."""
    r = json.load(jf("/Items/RemoteSearch/Person", {"SearchInfo": {"Name": name}, "IncludeDisabledProviders": False}, timeout=15))
    for x in r:
        u = x.get("ImageUrl") or ""
        if (x.get("Name") or "").lower() == name.lower() and urlparse(u).hostname == "image.tmdb.org":
            return u.replace("/t/p/original/", "/t/p/w185/")
    return None
catalog.person_lookup = person_photo

# ---------- server stats ----------
_stats = {"cpu": 0.0, "mem_used": 0, "mem_total": 0, "load": 0.0}
_net = {"t": 0, "d": None}
def net_usage():
    """Traffic this month, written by scripts/netstat.py on the host (root cron) into the data dir."""
    if time.time() - _net["t"] > 60:
        _net["t"] = time.time()
        try:
            with open("/db/net.json") as f: d = json.load(f)
            _net["d"] = {"tx": d["tx"], "rx": d["rx"], "month": d["month"]}
        except Exception:
            _net["d"] = None
    return _net["d"]

def stats_loop():
    prev = None
    while True:
        try:
            a = [int(x) for x in open("/proc/stat").readline().split()[1:]]
            idle, total = a[3] + a[4], sum(a)
            if prev: _stats["cpu"] = round(100 * (1 - (idle - prev[0]) / max(total - prev[1], 1)), 1)
            prev = (idle, total)
            mi = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in open("/proc/meminfo")}
            _stats["mem_total"] = mi["MemTotal"]; _stats["mem_used"] = mi["MemTotal"] - mi["MemAvailable"]
            _stats["load"] = float(open("/proc/loadavg").read().split()[0])
        except Exception:
            pass
        time.sleep(2)

# ---------- request list ----------
def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, year INTEGER,
                 poster TEXT, note TEXT, who TEXT, status TEXT DEFAULT 'open', created INTEGER)""")
    c.execute("CREATE TABLE IF NOT EXISTS request_votes(req_id INTEGER, who TEXT, PRIMARY KEY(req_id, who))")
    for col in ("tid TEXT", "season INTEGER"):          # added later: IMDb id (opens the details) and a season number
        try: c.execute("ALTER TABLE requests ADD COLUMN " + col)
        except sqlite3.OperationalError: pass
    return c

def clip(v, n):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]

ASSETS = os.environ.get("ASSETS_DIR", "/assets")
HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN = open(os.path.join(HERE, "login.html"), "rb").read()
ADMIN_ONLY = ("/_meta/space", "/_meta/stats", "/_meta/usage", "/_meta/users", "/_meta/members", "/_meta/admin/subtitles", "/_meta/status")
STAFF_ONLY = ("/_meta/items", "/_meta/sizes", "/_meta/have", "/_meta/match", "/_meta/guess")      # admin and uploaders
APPROVED_ONLY = ("/_meta/requests",)
SITE = os.environ.get("SITE_URL", "https://files.x0w1v75.com")
PUBLIC = {"user": "", "role": "public"}
_page = {"m": 0, "html": ""}
_rate = {}

def app_page(s, head=""):
    """The app, told who is looking (public, member, uploader, admin). `head` adds page-specific tags (SEO)."""
    p = os.path.join(ASSETS, "index.html")
    m = os.path.getmtime(p)
    if m != _page["m"]:
        _page["html"] = open(p, encoding="utf-8").read(); _page["m"] = m
    inj = '<script>window.__ROLE=%s;window.__ME=%s;window.__APPROVED=%s;window.__BRAND=%s;</script>' % (
        json.dumps(s["role"]), json.dumps(s["user"]), "true" if approved(s) else "false", json.dumps(mail.BRAND))
    return _page["html"].replace("<head>", "<head>" + inj + head, 1).encode()

def staff(s): return s["role"] in ("admin", "uploader")

# answers that are the same for everyone, kept a short while (the catalogue changes slowly; computing a view walks 30,000 titles)
_rc = {}
CACHED = {"/_meta/catalog": 60, "/_meta/search": 600, "/_meta/filters": 300, "/_meta/find": 120, "/_meta/title": 300, "/_meta/titles": 120,
          "/_meta/episodes": 600, "/_meta/cast": 3600}
def rc_get(key):
    v = _rc.get(key)
    return v[1] if v and v[0] > time.time() else None
def rc_put(key, ttl, body):
    if len(_rc) > 600:
        now = time.time()
        for k in [k for k, v in _rc.items() if v[0] <= now] or list(_rc)[:200]: _rc.pop(k, None)
    _rc[key] = (time.time() + ttl, body)
def approved(s): return staff(s) or (s["role"] == "member" and s.get("approved"))

def limited(user, key, per_min):
    now = time.time(); k = (user, key)
    with _lock:
        ts = [t for t in _rate.get(k, []) if now - t < 60]
        if len(ts) >= per_min: _rate[k] = ts; return True
        ts.append(now); _rate[k] = ts
    return False

def find_trailer(title, year, kind):
    """The best-looking trailer video for a title: first YouTube results page (no API key), scored, cached for a week."""
    q = urllib.parse.quote("%s %s %s" % (title, year or "", "game trailer" if kind == "game" else "official trailer tv series" if kind == "series" else "official trailer"))
    req = urllib.request.Request("https://www.youtube.com/results?search_query=%s&sp=EgIQAQ%%253D%%253D" % q, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+cb; SOCS=CAI"})
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m: return None
    found = []
    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                v = o["videoRenderer"]
                found.append(("".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])), v.get("videoId", ""), (v.get("lengthText") or {}).get("simpleText", "")))
            for x in o.values(): walk(x)
        elif isinstance(o, list):
            for x in o: walk(x)
    walk(json.loads(m.group(1)))
    words = [w for w in re.findall(r"[a-z0-9']+", title.lower()) if len(w) > 2]
    best = None
    for t, vid, ln in found[:12]:
        tl = t.lower(); sc = 0
        if "official trailer" in tl: sc += 3
        elif "trailer" in tl: sc += 2
        elif "teaser" in tl: sc += 1
        if re.search(r"reaction|review|explained|breakdown|ending|scene|clip|parody|fan made|fanmade|recap|spoiler", tl): sc -= 3
        if year and str(year) in tl: sc += 1
        if words and all(w in tl for w in words): sc += 2
        try:
            mm, ss = ln.split(":")[-2:]; secs = int(mm) * 60 + int(ss)
            if 25 <= secs <= 300: sc += 1
            elif secs > 600: sc -= 2
        except Exception: pass
        if vid and (best is None or sc > best[0]): best = (sc, vid, t)
    return (best[1], best[2]) if best and best[0] >= 2 else None

def filt(qs):
    f = {}
    c = re.sub(r"[^A-Z]", "", (qs.get("country") or [""])[0].upper())[:3]
    if c: f["country"] = c
    g = clip((qs.get("genre") or [""])[0], 20)
    if g: f["genre"] = g
    try:
        d = int((qs.get("decade") or [0])[0])
        if 1900 <= d <= 2030: f["decade"] = d - d % 10
    except ValueError: pass
    try:
        m = float((qs.get("min") or [0])[0])
        if 0 < m <= 10: f["min"] = m
    except ValueError: pass
    return f

def have_keys():
    try: d = json.loads(cached(_items, build_items, 20))
    except Exception: return []
    return sorted({"%s|%s|%s" % (v["t"], (v["n"] or "").lower(), v["y"] or "") for v in d.values() if v["t"] in ("Movie", "Series")})

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"        # keep-alive: Caddy reuses its connections instead of opening one per request
    disable_nagle_algorithm = True       # a small answer leaves at once (no 40 ms wait for the TCP ack)
    timeout = 60                         # an idle kept-alive connection is closed after a minute (no thread waits forever)
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json", cache="no-store", headers=None):
        k = getattr(self, "_rc_key", None)
        if k and code == 200 and ctype == "application/json":
            self._rc_key = None
            try:
                j = json.loads(body)
                if not (isinstance(j, dict) and (j.get("building") or j.get("pending"))): rc_put(k[0], k[1], body)
            except Exception: pass
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self._headers_buffer.append(b"\r\n" + body); self.flush_headers()      # headers and body in one packet
    def js(self, obj, code=200, headers=None): self.send(code, json.dumps(obj).encode(), headers=headers)
    def redirect(self, loc):
        self.send_response(302); self.send_header("Location", loc); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0"); self.end_headers()

    def sess(self): return auth.session(self.headers.get("Cookie"))
    def ip(self): return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()[:64]
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 8192: raise ValueError("too big")
        return json.loads(self.rfile.read(n) or b"{}")
    def origin_ok(self):
        o = self.headers.get("Origin")
        return (o is None or urlparse(o).netloc == self.headers.get("Host", "")) \
            and self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")
    def same_site(self):
        return self.origin_ok() and "application/json" in (self.headers.get("Content-Type") or "")

    def do_HEAD(self): self.send(405, b"")

    # ---------- GET ----------
    def do_GET(self):
        self._rc_key = None          # one handler serves several requests on a kept-alive connection
        u = urlparse(self.path); qs = parse_qs(u.query); path = u.path
        try:
            if path == "/_lib/gsap.min.js":         # the animation library (downloaded at setup, not shipped in the repo)
                try:
                    with open(os.path.join(HERE, "lib", "gsap.min.js"), "rb") as f: data = f.read()
                except OSError:
                    return self.send(404, b"{}")
                return self.send(200, data, "application/javascript", "public, max-age=31536000, immutable")
            if path in ("/manifest.webmanifest", "/sw.js") or re.fullmatch(r"/pwa/[a-z0-9-]+\.png", path):      # the installable app (phone home screen)
                name = os.path.basename(path)
                try:
                    with open(os.path.join(ASSETS, "pwa", name), "rb") as f: data = f.read()
                except OSError:
                    return self.send(404, b"{}")
                ct = {"webmanifest": "application/manifest+json", "js": "text/javascript", "png": "image/png"}[name.rsplit(".", 1)[1]]
                return self.send(200, data, ct, "no-cache" if not name.endswith(".png") else "public, max-age=604800",
                                 headers={"Service-Worker-Allowed": "/"} if name == "sw.js" else None)
            if path == "/login":
                if self.sess(): return self.redirect("/")
                return self.send(200, LOGIN, "text/html; charset=utf-8")
            if path in ("/about", "/privacy"):          # plain pages, the same for everyone
                with open(os.path.join(ASSETS, "pages", path[1:] + ".html"), "rb") as f: data = f.read()
                return self.send(200, data, "text/html; charset=utf-8", "public, max-age=300")
            if path in ("/", "/index.html", "/profiles", "/settings") or path.startswith(("/catalogue", "/title/")) or path.endswith("/"):
                return self.send(200, app_page(self.sess() or PUBLIC), "text/html; charset=utf-8")
            if path == "/auth/check":                       # Caddy asks this before serving the admin site or the guest page
                s = self.sess(); meth = self.headers.get("X-Forwarded-Method", "GET"); uri = self.headers.get("X-Forwarded-Uri", "/")
                if not s or not staff(s):           # the file server is for the admin and uploaders only
                    if meth == "GET" and "text/html" in (self.headers.get("Accept") or "") and not uri.startswith("/_meta"):
                        return self.redirect("/login?next=" + quote(uri, safe="/"))
                    return self.js({"error": "sign in"}, 401)
                if meth not in ("GET", "HEAD", "OPTIONS") and not self.origin_ok(): return self.js({"error": "forbidden"}, 403)
                if s["role"] == "uploader" and meth == "DELETE": return self.js({"error": "uploaders cannot delete"}, 403)
                return self.send(200, b"{}", headers={"X-Role": s["role"]})
            if not path.startswith("/_meta/"): return self.send(404, b"{}")
            s = self.sess() or PUBLIC
            admin = s["role"] == "admin"; rk = s["user"] or "ip:" + self.ip()
            if (path in ADMIN_ONLY and not admin) or ((path in STAFF_ONLY or path.startswith("/_meta/img/")) and not staff(s)) \
               or (path in APPROVED_ONLY and not approved(s)):
                return self.js({"error": "sign in" if s["role"] == "public" else "forbidden"}, 401 if s["role"] == "public" else 403)
            if path in CACHED:
                hit = rc_get(self.path)
                if hit is not None: return self.send(200, hit, cache="public, max-age=60")
                self._rc_key = (self.path, CACHED[path])
            if path == "/_meta/me": return self.js(dict(s, approved=approved(s), mail=mail.configured()))
            if path == "/_meta/favorites":
                if s["role"] != "member": return self.js([])
                return self.js(auth.favorites(s["user"]))
            if path == "/_meta/titles":      # several titles at once (favorites)
                ids = re.findall(r"tt\d{6,10}|wg\d{1,10}", (qs.get("ids") or [""])[0])[:200]
                return self.js([x for x in ((games.item(i) if i.startswith("wg") else catalog.item(i)) for i in ids) if x])
            if path == "/_meta/members": return self.js(auth.list_members())
            if path == "/_meta/usage":         # what uses the server: per service (written each minute by scripts/usage.py) + what plays now
                try:
                    with open("/db/usage.json") as f: u = json.load(f)
                except (OSError, ValueError):
                    u = {}
                plays = []
                try:
                    for x in json.load(jf("/Sessions?ActiveWithinSeconds=300", timeout=8)):
                        n = x.get("NowPlayingItem")
                        if not n: continue
                        t = x.get("TranscodingInfo") or {}
                        plays.append({"user": x.get("UserName"), "client": x.get("Client"), "device": x.get("DeviceName"),
                                      "title": (n.get("SeriesName") + " · " if n.get("SeriesName") else "") + (n.get("Name") or ""),
                                      "method": (x.get("PlayState") or {}).get("PlayMethod"), "reasons": t.get("TranscodeReasons") or [],
                                      "paused": (x.get("PlayState") or {}).get("IsPaused")})
                except Exception:
                    pass
                u["plays"] = plays
                try:
                    with open("/db/subocr.json") as f: so = json.load(f)
                    u["subocr"] = {"running": so.get("running"), "current": (so.get("current") or "").split("/")[-1],
                                   "done": sum(1 for x in so.get("files", []) if x["state"] == "done"), "todo": sum(1 for x in so.get("files", []) if x["state"] == "todo")}
                except (OSError, ValueError):
                    pass
                return self.js(u)
            if path == "/_meta/items": return self.send(200, cached(_items, build_items, 20))
            if path == "/_meta/sizes": return self.send(200, cached(_sizes, build_sizes, 60))
            if path == "/_meta/space":
                du = shutil.disk_usage(DATA); return self.js({"used": du.used, "total": du.total})
            if path == "/_meta/stats": return self.js(dict(_stats, net=net_usage(), catalog=catalog.counts()))
            if path == "/_meta/match":              # the official title for a messy folder name (used by Tidy up)
                kind = "series" if (qs.get("kind") or [""])[0] == "series" else "movie"
                q = clip((qs.get("q") or [""])[0], 80); yr = clip((qs.get("y") or [""])[0], 4)
                if len(q) < 2 or limited(rk, "match", 60): return self.js({})
                try:
                    res = search(kind, q)
                    pick = next((x for x in res if yr and str(x["y"]) == yr), None) or (res[0] if res else None)
                except Exception:
                    pick = None
                return self.js({"n": pick["n"], "y": pick["y"]} if pick else {})
            if path == "/_meta/trailer":
                title = clip((qs.get("title") or [""])[0], 100); year = clip((qs.get("year") or [""])[0], 4)
                kind = (qs.get("kind") or [""])[0]; kind = kind if kind in ("series", "game") else "movie"
                if len(title) < 2 or limited(rk, "trailer", 30): return self.js({})
                key = "%s|%s|%s" % (kind, title.lower(), year)
                row = catalog.get_trailer(key)
                if row and (row[0] or time.time() - row[2] < 6 * 3600) and time.time() - row[2] < 7 * 86400:
                    return self.send(200, json.dumps({"vid": row[0], "title": row[1]}).encode(), cache="private, max-age=3600")
                try: res = find_trailer(title, year, kind)
                except Exception: return self.js({})
                catalog.save_trailer(key, res[0] if res else "", res[1] if res else "")
                return self.send(200, json.dumps({"vid": res[0], "title": res[1]} if res else {}).encode(), cache="private, max-age=3600")
            if path == "/_meta/find" and (qs.get("type") or [""])[0] == "game":
                lim = (qs.get("limit") or ["24"])[0]
                return self.send(200, json.dumps(games.find(clip((qs.get("q") or [""])[0], 80), max(1, min(int(lim) if lim.isdigit() else 24, 40)))).encode(), cache="private, max-age=60")
            if path == "/_meta/find":
                kind = "series" if (qs.get("type") or [""])[0] == "series" else "movie"
                lim = (qs.get("limit") or ["24"])[0]
                body = json.dumps(catalog.find(kind, clip((qs.get("q") or [""])[0], 80), max(1, min(int(lim) if lim.isdigit() else 24, 40)))).encode()
                return self.send(200, body, cache="private, max-age=60")
            if path == "/_meta/cast":
                tid = (qs.get("id") or [""])[0]
                d = catalog.cast_for(tid) if re.fullmatch(r"tt\d{6,10}", tid) else None
                return self.send(200, json.dumps(d or {}).encode(), cache="private, max-age=86400" if d else "no-store")
            if path == "/_meta/episodes":
                sid = (qs.get("id") or [""])[0]
                d = catalog.episodes_for(sid) if re.fullmatch(r"tt\d{6,10}", sid) else None
                return self.send(200, json.dumps(d or {}).encode(), cache="private, max-age=300" if d else "no-store")
            if path == "/_meta/guess":              # a poster for a library folder Jellyfin does not know yet (by its name)
                kind = "series" if (qs.get("kind") or [""])[0] == "series" else "movie"
                q = clip((qs.get("q") or [""])[0], 80); yr = clip((qs.get("y") or [""])[0], 4)
                if len(q) < 2 or limited(rk, "guess", 240): return self.js({"img": ""})
                key = "%s|%s|%s" % (kind, q.lower(), yr)
                img = catalog.get_guess(key)
                if img is None:
                    img = ""
                    try:
                        res = search(kind, q)
                        pick = next((x for x in res if x["img"] and yr and str(x["y"]) == yr), None) or next((x for x in res if x["img"]), None)
                        img = pick["img"] if pick else ""
                    except Exception:
                        pass
                    catalog.save_guess(key, img)
                return self.send(200, json.dumps({"img": img}).encode(), cache="private, max-age=3600")
            if path == "/_meta/users": return self.js(auth.list_users())
            if path == "/_meta/have": return self.send(200, json.dumps(have_keys()).encode(), cache="private, max-age=30")
            if path == "/_meta/search" and (qs.get("type") or [""])[0] == "game": return self.js([])
            if path == "/_meta/search":
                if limited(rk, "search", 40 if s["user"] else 15): return self.js({"error": "slow down"}, 429)
                kind = (qs.get("type") or ["movie"])[0]; q = clip((qs.get("q") or [""])[0], 80)
                return self.js(search("series" if kind == "series" else "movie", q) if len(q) >= 2 else [])
            if path == "/_meta/admin/subtitles" and s["role"] == "admin":
                try:
                    with open("/db/subocr.json") as f: st = json.load(f)
                except (OSError, ValueError):
                    st = {}
                st["queued"] = os.path.exists("/db/subocr-queue.json")
                return self.js(st)
            if path == "/_meta/status" and s["role"] == "admin":
                return self.js(catalog.status())
            if path == "/_meta/filters" and (qs.get("type") or [""])[0] == "game":
                return self.send(200, json.dumps(games.filters_info()).encode(), cache="private, max-age=60")
            if path == "/_meta/filters":
                return self.send(200, json.dumps(catalog.filters_info("series" if (qs.get("type") or [""])[0] == "series" else "movie")).encode(), cache="private, max-age=60")
            if path in ("/_meta/lucky",):
                if (qs.get("type") or [""])[0] == "game": return self.js({}, 404)      # no lucky pick for games
                if limited(rk, "lucky", 60): return self.js({"error": "slow down"}, 429)
                kind = "series" if (qs.get("type") or [""])[0] == "series" else "movie"
                seen = set(re.findall(r"tt\d{6,10}", (qs.get("seen") or [""])[0]))
                fresh = (qs.get("fresh") or ["1"])[0] != "0"
                have = set()
                for k in have_keys():
                    if k.startswith("Series|" if kind == "series" else "Movie|"):
                        nm, yr = k.split("|", 1)[1].rsplit("|", 1)
                        have.add((nm, int(yr) if yr.isdigit() else None))
                with db() as c:
                    asked = c.execute("SELECT title, year FROM requests WHERE kind=? AND status='open'", (kind,)).fetchall()
                    mine = c.execute("SELECT r.title, r.year FROM requests r JOIN request_votes v ON v.req_id=r.id WHERE v.who=?", (s["user"],)).fetchall()
                exclude = have | {(r["title"].lower(), r["year"]) for r in asked}
                taste = {}
                if s["role"] != "public":       # visitors get no "matches your taste" (it would be the owner's library)
                    for g, n in catalog.genres_for(list(have)).items(): taste[g] = taste.get(g, 0) + n * 0.5        # what is in the library
                for g, n in catalog.genres_for([(r["title"].lower(), r["year"]) for r in mine]).items(): taste[g] = taste.get(g, 0) + n * 2   # what this person asked for
                tid, why = catalog.lucky(kind, filt(qs), exclude, taste, seen, fresh)
                it = catalog.item(tid) if tid else None
                if it and catalog.known(tid) is None:
                    resolve_one(it); it = catalog.item(tid)
                if it: it = dict(it, why=why)
                return self.js(it or {}, 200 if it else 404)
            if path == "/_meta/catalog" and (qs.get("type") or [""])[0] == "game":
                def num3(k, d, hi):
                    try: return max(0, min(int((qs.get(k) or [d])[0]), hi))
                    except ValueError: return d
                row = re.sub(r"[^a-z0-9_-]", "", (qs.get("row") or [""])[0]) or None
                f = filt(qs); f.pop("country", None)
                pl = (qs.get("country") or [""])[0].upper()      # for games the first filter is the platform
                if pl in games.PNAME: f["platform"] = pl
                if (qs.get("all") or [""])[0] == "1":
                    body = games.view_filter(f, (qs.get("sort") or [""])[0], num3("offset", 0, 8000), max(1, num3("limit", 40, 60)))
                else:
                    body = games.view(row, (qs.get("sort") or [""])[0], num3("offset", 0, 8000), max(1, num3("limit", 40, 60)), f or None)
                return self.send(200, json.dumps(body).encode(), cache="private, max-age=20")
            if path == "/_meta/catalog" and any(k in qs for k in ("country", "genre", "decade", "min")) and not (qs.get("row") or [""])[0]:
                def num2(k, d, hi):
                    try: return max(0, min(int((qs.get(k) or [d])[0]), hi))
                    except ValueError: return d
                kind2 = "series" if (qs.get("type") or [""])[0] == "series" else "movie"
                if (qs.get("all") or [""])[0] == "1":      # the flat list of every match
                    body = json.dumps(catalog.view_filter(kind2, filt(qs), (qs.get("sort") or [""])[0], num2("offset", 0, 8000), max(1, num2("limit", 40, 60)))).encode()
                else:                                       # shelves computed inside the filters
                    body = json.dumps(catalog.view(kind2, None, None, 0, 40, filt(qs))).encode()
                return self.send(200, body, cache="private, max-age=20")
            if path == "/_meta/catalog":
                row = re.sub(r"[^a-z0-9-]", "", (qs.get("row") or [""])[0]) or None
                def num(k, d, hi):
                    try: return max(0, min(int((qs.get(k) or [d])[0]), hi))
                    except ValueError: return d
                body = json.dumps(catalog.view("series" if (qs.get("type") or [""])[0] == "series" else "movie", row,
                                               (qs.get("sort") or [""])[0], num("offset", 0, 5000), max(1, num("limit", 40, 60)), filt(qs) or None)).encode()
                return self.send(200, body, cache="private, max-age=20")
            if path == "/_meta/title" and re.fullmatch(r"wg\d{1,10}", (qs.get("id") or [""])[0]):
                it = games.item(qs["id"][0])
                return self.js(it or {}, 200 if it else 404)
            if path == "/_meta/title":
                tid = (qs.get("id") or [""])[0]
                it = catalog.item(tid) if re.fullmatch(r"tt\d{6,10}", tid) else None
                if it and catalog.known(tid) is None:
                    resolve_one(it); it = catalog.item(tid)
                return self.js(it or {}, 200 if it else 404)
            if path == "/_meta/requests":
                with db() as c:
                    rows = [dict(r) for r in c.execute("SELECT r.*, (SELECT count(*) FROM request_votes v WHERE v.req_id=r.id) AS votes, (SELECT group_concat(who, ', ') FROM request_votes v WHERE v.req_id=r.id) AS voters FROM requests r ORDER BY (r.status='open') DESC, votes DESC, r.id DESC LIMIT 300")]
                return self.js(rows)
            m = re.fullmatch(r"/_meta/img/([0-9a-f]{32})", path)
            if m:
                w = max(60, min(int((qs.get("w") or ["320"])[0]), 800))
                r = jf("/Items/%s/Images/Primary?maxWidth=%d&quality=82" % (m.group(1), w), timeout=20)
                return self.send(200, r.read(), r.headers.get("Content-Type", "image/jpeg"), "private, max-age=86400")
            if path == "/_meta/rimg":
                src = (qs.get("u") or [""])[0]; pu = urlparse(src)
                if pu.scheme != "https" or pu.hostname not in ("image.tmdb.org", "static.tvmaze.com", "upload.wikimedia.org", "thumb.wikimedia.org"): return self.send(400, b"{}")
                w = (qs.get("w") or ["342"])[0]; w = w if w in ("185", "342", "500") else "342"
                with open(fetch_poster(src, w), "rb") as f: return self.send(200, f.read(), "image/jpeg", "public, max-age=31536000, immutable")
        except Exception:
            return self.send(404, b"{}")
        self.send(404, b"{}")

    # ---------- POST ----------
    def do_POST(self):
        if not self.same_site(): return self.js({"error": "forbidden"}, 403)
        path = urlparse(self.path).path
        try:
            b = self.body()
            if path == "/auth/login":
                tok, role = auth.login(b.get("user"), b.get("pass"), self.ip())
                if not tok: return self.js({"error": role}, 429 if role == "locked" else 401)
                return self.js({"ok": True, "role": role}, headers={"Set-Cookie": auth.cookie_value(tok)})
            if path == "/auth/logout":
                auth.logout(self.headers.get("Cookie"))
                return self.js({"ok": True}, headers={"Set-Cookie": auth.cookie_value("", clear=True)})
            if path == "/auth/code":                    # email sign-in, step 1: send a code
                if not mail.configured(): return self.js({"error": "Sign-in by email is not set up yet."}, 503)
                if limited("ip:" + self.ip(), "code", 5): return self.js({"error": "Too many tries. Wait a few minutes."}, 429)
                email, code = auth.request_code(b.get("email"), self.ip())
                if not email: return self.js({"error": code}, 400)
                try: mail.send_code(email, code, SITE)
                except Exception: return self.js({"error": "The email could not be sent. Try again later."}, 502)
                return self.js({"ok": True})
            if path == "/auth/verify":                  # step 2: the code -> a session
                tok, err = auth.verify_code(b.get("email"), b.get("code"), self.ip())
                if not tok: return self.js({"error": err}, 400)
                return self.js({"ok": True}, headers={"Set-Cookie": auth.cookie_value(tok)})
            s = self.sess()
            if not s: return self.js({"error": "sign in"}, 401)
            admin = s["role"] == "admin"
            if path == "/_meta/favorites":
                if s["role"] != "member": return self.js({"error": "members only"}, 403)
                ok = lambda l: [t for t in (l or [])[:500] if isinstance(t, str) and re.fullmatch(r"tt\d{6,10}|wg\d{1,10}", t)]
                auth.set_favorites(s["user"], ok(b.get("add")), ok(b.get("remove")))
                return self.js(auth.favorites(s["user"]))
            if path == "/_meta/password" and staff(s):
                err = auth.change_password(s["user"], b.get("current"), b.get("new"), self.headers.get("Cookie"))
                return self.js({"error": err}, 400) if err else self.js({"ok": True})
            if path == "/_meta/refresh" and staff(s):     # ask Jellyfin to scan the media folders now
                jf("/Library/Refresh", method="POST", timeout=20)
                _items["t"] = 0; _sizes["t"] = 0
                return self.js({"ok": True})
            if path.startswith("/_meta/requests") and not approved(s):
                return self.js({"error": "Requests are open to approved accounts."}, 403)
            if path == "/_meta/admin/subtitles" and admin:
                qf = "/db/subocr-queue.json"
                try:
                    with open(qf) as f: q = json.load(f)
                except (OSError, ValueError):
                    q = {"all": False, "files": []}
                if b.get("all"): q["all"] = True
                for p in (b.get("files") or [])[:2000]:
                    if isinstance(p, str) and ".." not in p and len(p) < 600 and p not in q["files"]: q["files"].append(p)
                with open(qf + ".tmp", "w") as f: json.dump(q, f)
                os.replace(qf + ".tmp", qf)
                return self.js({"ok": True})
            if path == "/_meta/usage" and admin:      # ask the host to measure now (systemd watches this file)
                open("/db/usage-request", "w").close()
                return self.js({"ok": True, "t": int(time.time())})
            if path == "/_meta/admin/rebuild" and admin:
                return self.js({"result": catalog.rebuild(clip(b.get("what"), 20))})
            if path == "/_meta/requests":
                kind = "series" if b.get("kind") == "series" else "movie"
                title = clip(b.get("title"), 120)
                if len(title) < 2: return self.js({"error": "title"}, 400)
                year = int(b["year"]) if str(b.get("year") or "").isdigit() else None
                poster = clip(b.get("poster"), 300)
                if poster and urlparse(poster).hostname != "image.tmdb.org": poster = ""
                who = s["user"] if not admin else (clip(b.get("who"), 40) or s["user"])
                tid = b.get("tid") if re.fullmatch(r"tt\d{6,10}", str(b.get("tid") or "")) else None
                season = int(b["season"]) if str(b.get("season") or "").isdigit() and 0 < int(b["season"]) <= 100 else None
                with db() as c:
                    if c.execute("SELECT count(*) FROM requests WHERE who=? AND created>?", (who, int(time.time()) - 3600)).fetchone()[0] >= 40:
                        return self.js({"error": "slow down"}, 429)
                    ex = c.execute("SELECT id FROM requests WHERE kind=? AND lower(title)=lower(?) AND IFNULL(year,0)=IFNULL(?,0) AND IFNULL(season,0)=IFNULL(?,0) AND status='open'", (kind, title, year, season)).fetchone()
                    if ex:
                        c.execute("INSERT OR IGNORE INTO request_votes VALUES(?,?)", (ex["id"], who))
                        return self.js({"id": ex["id"], "duplicate": True})
                    cur = c.execute("INSERT INTO requests(kind,title,year,poster,note,who,created,tid,season) VALUES(?,?,?,?,?,?,?,?,?)",
                                    (kind, title, year, poster, clip(b.get("note"), 200), who, int(time.time()), tid, season))
                    c.execute("INSERT OR IGNORE INTO request_votes VALUES(?,?)", (cur.lastrowid, who))
                return self.js({"id": cur.lastrowid})
            m = re.fullmatch(r"/_meta/requests/(\d+)/vote", path)
            if m:
                who = s["user"] if not admin else (clip(b.get("who"), 40) or s["user"])
                with db() as c:
                    had = c.execute("DELETE FROM request_votes WHERE req_id=? AND who=?", (int(m.group(1)), who)).rowcount
                    if not had: c.execute("INSERT OR IGNORE INTO request_votes VALUES(?,?)", (int(m.group(1)), who))
                return self.js({"ok": True, "voted": not had})
            if not admin: return self.js({"error": "forbidden"}, 403)
            m = re.fullmatch(r"/_meta/requests/(\d+)", path)
            if m:
                st = "done" if b.get("status") == "done" else "open"
                with db() as c: c.execute("UPDATE requests SET status=? WHERE id=?", (st, int(m.group(1))))
                return self.js({"ok": True})
            m = re.fullmatch(r"/_meta/members/(.+)", path)
            if m:
                email = m.group(1).lower()
                if b.get("action") in ("approve", "unapprove"):
                    return self.js({"ok": bool(auth.set_member(email, b["action"] == "approve"))})
            if path == "/_meta/users":
                pw, err = auth.create_user(b.get("name"), b.get("role"))
                return self.js({"error": err}, 400) if err else self.js({"name": str(b.get("name")).strip().lower(), "password": pw})
            m = re.fullmatch(r"/_meta/users/([a-z0-9._-]{3,24})", path)
            if m:
                act, who = b.get("action"), m.group(1)
                if act == "reset":
                    if who == s["user"]: return self.js({"error": "Use Settings to change your own password."}, 400)
                    pw = auth.reset_user(who); return self.js({"password": pw}) if pw else self.js({"error": "No such profile."}, 404)
                if act in ("disable", "enable"):
                    if act == "disable" and (who == s["user"] or (auth.get_role(who) == "admin" and auth.admin_count() <= 1)):
                        return self.js({"error": "You can't disable yourself or the last admin."}, 400)
                    auth.set_active(who, act == "enable"); return self.js({"ok": True})
        except Exception:
            return self.js({"error": "bad request"}, 400)
        self.js({"error": "not found"}, 404)

    # ---------- DELETE ----------
    def do_DELETE(self):
        if not self.origin_ok(): return self.js({"error": "forbidden"}, 403)
        s = self.sess()
        if not s: return self.js({"error": "sign in"}, 401)
        if s["role"] != "admin": return self.js({"error": "forbidden"}, 403)
        path = urlparse(self.path).path
        m = re.fullmatch(r"/_meta/requests/(\d+)", path)
        if m:
            with db() as c:
                c.execute("DELETE FROM requests WHERE id=?", (int(m.group(1)),)); c.execute("DELETE FROM request_votes WHERE req_id=?", (int(m.group(1)),))
            return self.js({"ok": True})
        m = re.fullmatch(r"/_meta/members/(.+)", path)
        if m:
            auth.delete_member(m.group(1).lower()); return self.js({"ok": True})
        m = re.fullmatch(r"/_meta/users/([a-z0-9._-]{3,24})", path)
        if m:
            who = m.group(1)
            if who == s["user"] or who == auth.ADMIN_USER.lower() or (auth.get_role(who) == "admin" and auth.admin_count() <= 1):
                return self.js({"error": "That account can't be deleted."}, 400)
            auth.delete_user(who); return self.js({"ok": True})
        self.js({"error": "not found"}, 404)

if __name__ == "__main__":
    threading.Thread(target=stats_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()

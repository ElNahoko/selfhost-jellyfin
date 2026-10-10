"""The news section (/news): movie, series, anime and game news from the publishers' own RSS feeds, plus Nahoko's posts.

Like a news aggregator: for each story only the headline, the short summary the publisher puts in its feed, the picture
and a link back are kept; every card and page names the source and sends readers to the full article there.
Feeds are read every 30 minutes in the background; stories older than 45 days are dropped.
Pictures come from the feed or, when it has none, from the article's own preview picture (og:image), and are served
through /_meta/nimg/<story id> (only pictures of stories we stored can be fetched)."""
import hashlib, html, json, math, os, re, sqlite3, threading, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

DBDIR = os.environ.get("DB_DIR", "/db")
SITE = os.environ.get("SITE_URL", "").rstrip("/")      # absolute links for search engines
NDB = os.path.join(DBDIR, "news.db")
IMGDIR = os.path.join(DBDIR, "img")
UA = "Mozilla/5.0 (compatible; NahokoNews/1.0; self-hosted media catalogue)"
KEEP_DAYS = 45
PER_PAGE = 24

SOURCES = [      # (section, source name, feed)
    ("movies", "Variety", "https://variety.com/v/film/feed/"),
    ("movies", "/Film", "https://www.slashfilm.com/feed/"),
    ("movies", "Screen Rant", "https://screenrant.com/feed/movie-news/"),
    ("movies", "IndieWire", "https://www.indiewire.com/c/film/feed/"),
    ("series", "Variety", "https://variety.com/v/tv/feed/"),
    ("series", "TVLine", "https://tvline.com/feed/"),
    ("anime", "Anime News Network", "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=w"),
    ("anime", "Anime Corner", "https://animecorner.me/feed/"),
    ("games", "IGN", "https://feeds.feedburner.com/ign/news"),
    ("games", "GameSpot", "https://www.gamespot.com/feeds/news/"),
    ("games", "Polygon", "https://www.polygon.com/rss/index.xml"),
    ("games", "Eurogamer", "https://www.eurogamer.net/feed/news"),
]
SECTIONS = [("", "All"), ("movies", "Movies"), ("series", "Series"), ("anime", "Anime"), ("games", "Games"), ("nahoko", "Nahoko")]
SNAME = dict(SECTIONS)
_st = {"running": False, "t": 0, "error": ""}

def _db():
    c = sqlite3.connect(NDB, timeout=20); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, sec TEXT, src TEXT, title TEXT, link TEXT, t INTEGER, excerpt TEXT, img TEXT, imgtry INTEGER DEFAULT 0)")
    c.execute("CREATE INDEX IF NOT EXISTS items_sec_t ON items(sec, t)")
    c.execute("CREATE INDEX IF NOT EXISTS items_t ON items(t)")
    return c

# ---------- reading the feeds ----------
def _get(url, limit=3_000_000, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=timeout) as r:
        return r.read(limit)

def _text(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*(The post .{0,200} appeared first on .{0,80}\.?|Continue reading.*|Read more.*)$", "", s, flags=re.I)
    return s

def _cut(s, n=300):
    if len(s) <= n: return s
    s = s[:n].rsplit(" ", 1)[0].rstrip(",;:—–- ")
    return s + "…"

def _img_in(it):
    for el in it.iter():
        t = el.tag.split("}")[-1]
        u = el.get("url") or ""
        if t in ("content", "thumbnail") and u and el.get("medium") in (None, "image") and re.search(r"\.(jpe?g|png|webp)|image|img", u + (el.get("type") or ""), re.I):
            return u
        if t == "enclosure" and (el.get("type") or "").startswith("image") and u: return u
    for el in it.iter():
        if el.text and "<img" in el.text:
            m = re.search(r"<img[^>]+src=[\"']([^\"']+)", el.text)
            if m: return html.unescape(m.group(1))
    return ""

def _when(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            import datetime
            d = datetime.datetime.strptime(s.strip().replace("GMT", "+0000").replace("UTC", "+0000"), fmt.replace("%Z", "%z"))
            return int(d.timestamp())
        except (ValueError, AttributeError):
            continue
    return int(time.time())

def _og_image(url):
    try:
        raw = _get(url, 250_000, 15).decode("utf-8", "replace")
    except Exception:
        return ""
    for pat in (r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)[\"'][^>]+content=[\"']([^\"']+)",
                r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"'](?:og:image|twitter:image)"):
        m = re.search(pat, raw, re.I)
        if m: return html.unescape(m.group(1))
    return ""

def _fetch_all():
    try:
        try: os.nice(10)
        except (OSError, AttributeError): pass
        c = _db(); now = int(time.time())
        for sec, src, url in SOURCES:
            try: root = ET.fromstring(_get(url))
            except Exception: continue
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for it in items[:40]:
                title = _text(it.findtext("title") or it.findtext("{http://www.w3.org/2005/Atom}title") or "")
                link = (it.findtext("link") or "").strip()
                if not link:
                    l = it.find("{http://www.w3.org/2005/Atom}link"); link = l.get("href") if l is not None else ""
                if not title or not link.startswith("http"): continue
                iid = hashlib.sha1(link.encode()).hexdigest()[:12]
                if c.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone(): continue
                desc = it.findtext("description") or it.findtext("{http://www.w3.org/2005/Atom}summary") or ""
                if len(_text(desc)) < 40:
                    desc = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or desc
                t = _when(it.findtext("pubDate") or it.findtext("{http://www.w3.org/2005/Atom}published") or it.findtext("{http://www.w3.org/2005/Atom}updated") or "")
                c.execute("INSERT OR IGNORE INTO items(id, sec, src, title, link, t, excerpt, img) VALUES(?,?,?,?,?,?,?,?)",
                          (iid, sec, src, title[:240], link[:600], min(t, now), _cut(_text(desc)), _img_in(it)[:600]))
            c.commit()
            time.sleep(2)
        for r in c.execute("SELECT id, link FROM items WHERE img='' AND imgtry=0 ORDER BY t DESC LIMIT 120").fetchall():
            c.execute("UPDATE items SET img=?, imgtry=1 WHERE id=?", (_og_image(r["link"])[:600], r["id"])); c.commit()
            time.sleep(1.5)
        c.execute("DELETE FROM items WHERE t < ?", (now - KEEP_DAYS * 86400,)); c.commit(); c.close()
        _st["error"] = ""
    except Exception as e:
        _st["error"] = str(e)[:200]
    finally:
        _st["running"] = False; _st["t"] = time.time()

def _loop():
    time.sleep(20)
    try: _known_names()      # ready before the first story page is opened
    except Exception: pass
    while True:
        if not _st["running"]:
            _st["running"] = True
            _fetch_all()
        time.sleep(1800)

threading.Thread(target=_loop, daemon=True).start()

# ---------- pictures ----------
def image_file(iid):
    """-> path of the stored picture of a story (downloaded once), or None."""
    if not re.fullmatch(r"[0-9a-f]{12}", iid or ""): return None
    c = _db(); r = c.execute("SELECT img FROM items WHERE id=?", (iid,)).fetchone(); c.close()
    if not r or not r["img"] or not r["img"].startswith("http"): return None
    fp = os.path.join(IMGDIR, "news-" + iid + ".jpg")
    if not os.path.exists(fp):
        os.makedirs(IMGDIR, exist_ok=True)
        data = _get(r["img"], 4_000_000, 15)
        with open(fp + ".tmp", "wb") as f: f.write(data)
        os.replace(fp + ".tmp", fp)
    return fp

# ---------- reading ----------
def _rows(sec="", offset=0, limit=PER_PAGE, with_img_first=False):
    c = _db()
    where, args = ("WHERE sec=?", [sec]) if sec else ("", [])
    total = c.execute("SELECT count(*) FROM items " + where, args).fetchone()[0]
    rows = [dict(r) for r in c.execute("SELECT * FROM items %s ORDER BY t DESC LIMIT ? OFFSET ?" % where, args + [limit, offset])]
    c.close()
    return rows, total

def story(iid):
    if not re.fullmatch(r"[0-9a-f]{12}", iid or ""): return None
    c = _db(); r = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone(); c.close()
    return dict(r) if r else None

STOP = set("the a an and or of in on to for with at by from is are was were be as it its this that new first after over into how why what who will can just".split())

def _words(t): return {w for w in re.findall(r"[a-z0-9']+", t.lower()) if len(w) > 2 and w not in STOP}

def related(s, n=6):
    c = _db(); rows = [dict(r) for r in c.execute("SELECT * FROM items WHERE sec=? AND id!=? ORDER BY t DESC LIMIT 300", (s["sec"], s["id"]))]; c.close()
    me = _words(s["title"])
    scored = sorted(rows, key=lambda r: (-len(me & _words(r["title"])), -r["t"]))
    return scored[:n]

_names = {"t": 0, "d": None}

def _known_names():
    """Well-known titles by name (lowercase), to spot them in a headline even without quotes. Rebuilt every 30 minutes."""
    if _names["d"] is not None:
        if time.time() - _names["t"] > 1800 and not _names.get("busy"):      # stale: rebuilt in the background, the old one serves meanwhile
            _names["busy"] = True; _names["t"] = time.time()
            threading.Thread(target=lambda: (_build_names(), _names.update(busy=False)), daemon=True).start()
        return _names["d"]
    return _build_names()

def _build_names():
    import catalog, games
    d, q = {}, {}      # d: names spotted anywhere; q: every name, for titles written between quotes
    try:
        cat = catalog.load()
        for it in (cat or {}).get("items", {}).values():
            n = it["n"].lower()
            if n not in q or it["v"] > q[n][1]: q[n] = (it["id"], it["v"])
            # without quotes only names of two words or more ("Breaking Bad", "The Ring"): one word is too often a plain word
            if " " not in n or len(n) < 8 or not (_words(n) - STOP): continue
            if it["v"] >= 30000 and (n not in d or it["v"] > d[n][1]): d[n] = (it["id"], it["v"])
    except Exception:
        pass
    try:
        for it in (games._items() or {}).values():
            n = it["n"].lower()
            if n not in q: q[n] = (it["id"], 0)
            if " " in n and len(n) >= 8 and it["pop"] >= 20 and n not in d: d[n] = (it["id"], 0)
    except Exception:
        pass
    _names["d"] = d; _names["q"] = q; _names["t"] = time.time()
    return d

def mentions(s, n=4):
    """Titles the story is about that the catalogue has: names in quotes first (as the trade press writes them),
    then well-known names anywhere in the headline or summary."""
    import catalog, games
    found, seen = [], set()
    def add(tid):
        if tid in seen or len(found) >= n: return
        it = games.item(tid) if tid.startswith("wg") else catalog.item(tid)
        if it: seen.add(tid); found.append(it)
    text = s["title"] + " " + (s.get("excerpt") or "")
    names = _known_names(); every = _names.get("q") or {}
    for q in re.findall(r"[‘'\"“]([^’'\"”]{2,70})[’'\"”]", text):      # an exact name lookup: no search through the catalogue
        hit = every.get(q.strip().lower().replace("’", "'"))
        if hit and hit[0].startswith("wg") == (s["sec"] == "games"): add(hit[0])
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’:&.-]*", text)
    low = [w.lower().replace("’", "'").rstrip(".:") for w in words]
    for size in range(6, 0, -1):      # longest names first, so "The Dark Knight Rises" wins over "The Dark Knight"
        for i in range(0, len(low) - size + 1):
            if not words[i][:1].isupper() and not words[i][:1].isdigit(): continue      # names start with a capital in a headline
            hit = names.get(" ".join(low[i:i + size]))
            if hit and hit[0].startswith("wg") == (s["sec"] == "games"): add(hit[0])      # game stories name games, the others films and series
    return found

def background(it):
    """A short note about a title, written by Nahoko from its own catalogue data (not taken from the story)."""
    import catalog
    k = it.get("k"); bits = []
    kind = {"movie": "film", "series": "series", "game": "game"}.get(k, "title")
    g = [x.lower() for x in (it.get("g") or [])[:2]]
    lead = "%s is a %s%s%s" % (it["n"], ("%s " % it["y"]) if it.get("y") else "", (" and ".join(g) + " ") if g else "", kind)
    who = None
    if k in ("movie", "series"):
        try: who = catalog.cast_for(it["id"]) or {}
        except Exception: who = {}
        d = (who.get("directors") if k == "movie" else who.get("writers")) or []
        if d: lead += " %s %s" % ("directed by" if k == "movie" else "created by", d[0])
        stars = [c["n"] for c in (who.get("cast") or [])[:3]]
        if stars: lead += ", with %s" % (", ".join(stars[:-1]) + " and " + stars[-1] if len(stars) > 1 else stars[0])
    elif k == "game":
        if it.get("plat"): lead += " for %s" % (", ".join(it["plat"][:3]))
        if it.get("dev"): lead += ", made by %s" % it["dev"]
    bits.append(lead + ".")
    if k in ("movie", "series") and it.get("r") and it.get("v"):
        v = it["v"]; vs = "%.1f million" % (v / 1e6) if v >= 1e6 else "%d,000" % round(v / 1000) if v >= 1000 else str(v)
        verdict = "a favourite" if it["r"] >= 8 else "well liked" if it["r"] >= 7 else "mixed" if it["r"] >= 6 else "divisive"
        bits.append("Audiences rate it %.1f out of 10 on IMDb (%s votes): %s." % (it["r"], vs, verdict))
    elif k == "game" and it.get("mc"):
        bits.append("Critics give it %d out of 100 on Metacritic." % it["mc"])
    return " ".join(bits)

# ---------- pages ----------
def _ago(t):
    d = max(0, time.time() - t)
    if d < 3600: return "%d min ago" % max(1, d // 60)
    if d < 86400: return "%d h ago" % (d // 3600)
    if d < 86400 * 7: return "%d d ago" % (d // 86400)
    return time.strftime("%d %b %Y", time.localtime(t)).lstrip("0")

def _slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:70] or "story"

def _e(s): return html.escape(s or "", quote=True)

def _ph(s, cls=""):
    return '<div class="%s noimg ph-%s"><span>%s</span></div>' % (cls, s.get("sec", ""), _e(s["src"]))

def _img(s, cls=""):
    if s.get("img"):
        return ('<img class="%s" src="/_meta/nimg/%s" alt="%s" loading="lazy" decoding="async" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\'">'
                '<div class="%s noimg ph-%s" style="display:none"><span>%s</span></div>') % (cls, s["id"], _e(s["title"]), cls, s.get("sec", ""), _e(s["src"]))
    try: m = next((x for x in mentions(s, 1) if x.get("img")), None)
    except Exception: m = None
    if m: return '<img class="%s poster" src="/_meta/rimg?w=500&amp;u=%s" alt="%s" loading="lazy" decoding="async">' % (cls, urllib.parse.quote(m["img"], safe=""), _e(m["n"]))
    return _ph(s, cls)

def _card(s, big=False):
    url = "/news/a/%s-%s" % (s["id"], _slug(s["title"]))
    return ('<a class="card%s" href="%s"><div class="ci">%s</div><div class="cb"><div class="meta"><span class="sec sec-%s">%s</span>'
            '<span>%s · %s</span></div><h3>%s</h3>%s</div></a>') % (
        " big" if big else "", url, _img(s), s["sec"], SNAME.get(s["sec"], ""), _e(s["src"]), _ago(s["t"]), _e(s["title"]),
        ('<p>%s</p>' % _e(s["excerpt"])) if s.get("excerpt") else "")

def _post_card(p):
    return ('<a class="card" href="/news/p/%d-%s"><div class="ci"><div class="noimg brand"><span>N</span></div></div><div class="cb"><div class="meta">'
            '<span class="sec sec-nahoko">Nahoko</span><span>%s</span></div><h3>%s</h3><p>%s</p></div></a>') % (
        p["id"], _slug(p["title"]), _ago(p["t"]), _e(p["title"]), _e(_cut(p["body"].replace("\n", " "), 200)))

_sidec = {"t": 0, "html": ""}

def _poster(it, w=185):
    return ('<img src="/_meta/rimg?w=%d&amp;u=%s" alt="" loading="lazy" decoding="async">' % (w, urllib.parse.quote(it["img"], safe=""))) if it.get("img") else "<i></i>"

def _side():
    """The column next to the news: new in the catalogue (with posters), new games, our own updates. Kept 2 minutes."""
    if _sidec["html"] and time.time() - _sidec["t"] < 120: return _sidec["html"]
    import catalog, extras, games
    out = []
    def shelf(title, items, more):
        li = "".join('<li><a href="%s">%s<span class="st"><b>%s</b><span>%s%s</span></span></a></li>' % (
            _tp(i), _poster(i), _e(i["n"]), i.get("y") or "", (" · ★ %.1f" % i["r"]) if i.get("r") else "") for i in items)
        out.append('<section class="box"><h4>%s</h4><ul class="thumbs">%s</ul><a class="more" href="%s">See all</a></section>' % (title, li, more))
    try:
        row = next((r for r in catalog.view("movie").get("rows", []) if r["id"] == "new"), None)
        if row: shelf("New films", row["items"][:5], "/movies?row=new")
    except Exception:
        pass
    try:
        row = next((r for r in catalog.view("series").get("rows", []) if r["id"] == "new"), None)
        if row: shelf("New series", row["items"][:4], "/series?row=new")
    except Exception:
        pass
    try:
        row = next((r for r in games.view().get("rows", []) if r["id"] == "new"), None)
        if row: shelf("New games", row["items"][:4], "/games?row=new")
    except Exception:
        pass
    try:
        posts = extras.news()[:3]
        li = "".join('<li><a href="/news/p/%d-%s"><span class="st"><b>%s</b><span>%s</span></span></a></li>' % (p["id"], _slug(p["title"]), _e(p["title"]), _ago(p["t"])) for p in posts)
        out.append('<section class="box"><h4>Nahoko updates</h4><ul class="thumbs plain">%s</ul><a class="more" href="/news/nahoko">All updates</a></section>' % li)
    except Exception:
        pass
    _sidec["html"] = "".join(out); _sidec["t"] = time.time()
    return _sidec["html"]

def _shell(title, desc, body, sec="", canonical="", extra=""):
    tabs = "".join('<a href="/news%s"%s>%s</a>' % (("/" + k) if k else "", ' class="on"' if k == sec else "", n) for k, n in SECTIONS)
    crumbs = [("News", "/news")] + ([(SNAME.get(sec, ""), "/news/" + sec)] if sec else [])
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": SITE + u} for i, (n, u) in enumerate(crumbs)]}
    extra += '<script type="application/ld+json">%s</script>' % json.dumps(ld).replace("</", "<\\/")
    if "og:image" not in extra: extra += '<meta property="og:image" content="%s/pwa/icon-512.png">' % SITE
    return PAGE.replace("{{TITLE}}", _e(title)).replace("{{DESC}}", _e(_cut(desc, 200))).replace("{{TABS}}", tabs).replace("{{BODY}}", body) \
               .replace("{{CANON}}", _e(SITE + (canonical or "/news"))).replace("<!--EXTRA-->", extra)

def _pager(base, page, pages):
    if pages <= 1: return ""
    nums = sorted({x for x in (1, page - 2, page - 1, page, page + 1, page + 2, pages) if 1 <= x <= pages})
    out, last = [], 0
    out.append('<a class="pg%s" href="%s?page=%d" aria-label="Previous page">‹</a>' % (" off" if page == 1 else "", base, max(1, page - 1)))
    for n in nums:
        if n - last > 1: out.append('<span class="dots">…</span>')
        out.append('<a class="pg%s" href="%s?page=%d"%s>%d</a>' % (" on" if n == page else "", base, n, ' aria-current="page"' if n == page else "", n)); last = n
    out.append('<a class="pg%s" href="%s?page=%d" aria-label="Next page">›</a>' % (" off" if page == pages else "", base, min(pages, page + 1)))
    return '<nav class="pager">%s</nav>' % "".join(out)

def hub(sec="", page=1):
    import extras
    if sec == "nahoko":
        posts = extras.news()
        cards = "".join(_post_card(p) for p in posts) or '<p class="empty">Nothing yet.</p>'
        body = '<div class="layout"><main><h1>Nahoko updates</h1><p class="lead">What is new on the site.</p><div class="grid">%s</div></main><aside>%s</aside></div>' % (cards, _side())
        return _shell("Nahoko updates", "What is new on Nahoko.", body, "nahoko", "/news/nahoko")
    page = max(1, page)
    rows, total = _rows(sec, (page - 1) * PER_PAGE, PER_PAGE)
    pages = max(1, math.ceil(total / PER_PAGE))
    if not rows:
        main = '<h1>%s</h1><p class="empty">The first stories are being collected. Come back in a few minutes.</p>' % (_e(SNAME.get(sec) + " news") if sec else "News")
    else:
        top = next((r for r in rows if r.get("img")), rows[0]) if page == 1 else None
        rest = [r for r in rows if r is not top]
        main = "<h1>%s</h1>" % (_e(SNAME.get(sec)) + " news" if sec else "News")
        if top: main += '<div class="topstory">%s</div>' % _card(top, True)
        main += '<div class="grid">%s</div>' % "".join(_card(r) for r in rest)
        main += _pager("/news" + ("/" + sec if sec else ""), page, pages)
    body = '<div class="layout"><main>%s</main><aside>%s</aside></div>' % (main, _side())
    name = (SNAME.get(sec) + " news") if sec else "News"
    return _shell(name + (" · page %d" % page if page > 1 else ""), "The latest %s news, from the publishers' own feeds." % (SNAME.get(sec, "").lower() or "movie, series, anime and game"),
                  body, sec, "/news" + ("/" + sec if sec else ""))

_artc = {}

def article(iid):
    """One story: its picture and summary, then Nahoko's own background on every title it mentions (poster, facts,
    plot from the catalogue), related stories and the side column. Kept 5 minutes."""
    hit = _artc.get(iid)
    if hit and time.time() - hit[0] < 300: return hit[1]
    s = story(iid)
    if not s: return None
    rel = related(s); men = mentions(s)
    when = time.strftime("%d %B %Y, %H:%M", time.localtime(s["t"])).lstrip("0")
    picks = _picks(s, {m["id"] for m in men})
    bg = ""
    if men:
        cards = []
        for m in men:
            facts = [str(m["y"])] if m.get("y") else []
            facts += [{"movie": "Film", "series": "Series", "game": "Game"}.get(m.get("k"), "")] + (m.get("g") or [])[:3]
            score = ('<span class="score">★ %.1f</span>' % m["r"]) if m.get("r") else ""
            plot = _cut(m.get("o") or "", 420)
            cards.append('<div class="bgcard"><a class="bgp" href="%s">%s</a><div class="bgt"><h3><a href="%s">%s</a>%s</h3>'
                         '<div class="facts">%s</div><p class="note">%s</p>%s<a class="open" href="%s">Open in Nahoko</a></div></div>' % (
                _tp(m), _poster(m, 342), _tp(m), _e(m["n"]), score, " · ".join(_e(x) for x in facts if x), _e(background(m)),
                ('<p class="plot">%s</p>' % _e(plot)) if plot else "", _tp(m)))
        bg = '<section class="bg"><h2>Background</h2>%s</section>' % "".join(cards)
    body = ('<div class="layout"><main><article class="story"><div class="meta"><a class="sec sec-%s" href="/news/%s">%s</a><span>%s · %s</span></div>'
            '<h1>%s</h1>%s%s<p class="credit">Story from <a href="%s" rel="noopener nofollow" target="_blank">%s</a>, who wrote the headline, summary and picture. '
            'The background below is Nahoko\'s own.</p></article>%s%s</main><aside>%s</aside></div>') % (
        s["sec"], s["sec"], SNAME.get(s["sec"], ""), _e(s["src"]), when, _e(s["title"]),
        ('<figure>%s</figure>' % _img(s, "hero")) if s.get("img") else "", ('<p class="lede">%s</p>' % _e(s["excerpt"])) if s.get("excerpt") else "",
        _e(s["link"]), _e(s["src"]), bg + picks,
        ('<section class="rel"><h2>Related stories</h2><div class="grid">%s</div></section>' % "".join(_card(r) for r in rel)) if rel else "", _side())
    page = _shell(s["title"], s.get("excerpt") or s["title"], body, s["sec"], "/news/a/%s-%s" % (s["id"], _slug(s["title"])), _story_ld(s, men))
    if len(_artc) > 400: _artc.clear()
    _artc[iid] = (time.time(), page)
    return page

def post(pid):
    import extras
    p = next((x for x in extras.news() if x["id"] == pid), None)
    if not p: return None
    paras = "".join("<p>%s</p>" % _e(x).replace("\n", "<br>") for x in re.split(r"\n\s*\n", p["body"]) if x.strip())
    others = [x for x in extras.news() if x["id"] != pid][:4]
    body = ('<div class="layout"><main><article class="story"><div class="meta"><a class="sec sec-nahoko" href="/news/nahoko">Nahoko</a><span>%s%s</span></div>'
            '<h1>%s</h1><div class="postbody">%s</div></article>%s</main><aside>%s</aside></div>') % (
        time.strftime("%d %B %Y", time.localtime(p["t"])).lstrip("0"), (" · " + _e(p["tag"])) if p.get("tag") else "", _e(p["title"]), paras,
        ('<section class="rel"><h2>More updates</h2><div class="grid">%s</div></section>' % "".join(_post_card(x) for x in others)) if others else "", _side())
    return _shell(p["title"], _cut(p["body"].replace("\n", " "), 160), body, "nahoko", "/news/p/%d-%s" % (pid, _slug(p["title"])))

def status():
    c = _db(); n = c.execute("SELECT count(*), sum(img!='') FROM items").fetchone(); c.close()
    return {"stories": n[0], "with_picture": n[1] or 0, "running": _st["running"], "error": _st["error"], "last": int(_st["t"])}

PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.html"), encoding="utf-8").read() \
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.html")) else "{{BODY}}"

# ---------- picks under every story, and what search engines and AI assistants read ----------
TOPICS = [(r"horror|scary|terrif|exorcis|haunt|slasher|zombie", "Horror"), (r"comed|funny|laugh|sitcom", "Comedy"),
          (r"sci-?fi|space|alien|star wars|star trek|cyber", "Sci-Fi"), (r"romance|romantic|love stor", "Romance"),
          (r"thriller|crime|murder|detective|heist|mafia", "Crime"), (r"documentar", "Documentary"), (r"war\b|soldier|battle", "War"),
          (r"animat|pixar|disney|dreamworks", "Animation"), (r"fantasy|dragon|wizard|magic", "Fantasy"), (r"superhero|marvel|dc studios|batman|superman|spider-man", "Action")]

def _picks(s, skip=()):
    """Six catalogue titles that fit the story's subject (its genre words, else its section), different for each story."""
    import catalog, games
    text = (s["title"] + " " + (s.get("excerpt") or "")).lower()
    try:
        if s["sec"] == "games":
            rows = games.view_filter({}, "best", (int(s["id"][:3], 16) % 8) * 6, 12)["rows"][0]["items"]
            label, more = "Games to play", "/games"
        else:
            kind = "series" if s["sec"] == "series" else "movie"
            genre = next((g for pat, g in TOPICS if re.search(pat, text)), None)
            if s["sec"] == "anime":
                v = catalog.view("series", "anime"); rows = v["rows"][0]["items"] if v.get("rows") else []
                label, more = "Anime to watch", "/series?row=anime"
            else:
                f = {"genre": genre} if genre else {}
                d = catalog.view_filter(kind, f, "best", (int(s["id"][:3], 16) % 6) * 6, 12) if f else catalog.view(kind, "popular", None, (int(s["id"][:3], 16) % 6) * 6, 12)
                rows = d["rows"][0]["items"] if d.get("rows") else []
                label = ("%s %s on Nahoko" % (genre, "series" if kind == "series" else "films")) if genre else ("Popular %s on Nahoko" % ("series" if kind == "series" else "films"))
                more = "/%s%s" % ("series" if kind == "series" else "movies", ("?genre=" + urllib.parse.quote(genre)) if genre else "?row=popular")
    except Exception:
        return ""
    rows = [r for r in rows if r["id"] not in skip and r.get("img")][:6]
    if not rows: return ""
    cards = "".join('<a class="pick" href="%s">%s<b>%s</b><span>%s%s</span></a>' % (
        _tp(r), _poster(r, 342), _e(r["n"]), r.get("y") or "", (" · ★ %.1f" % r["r"]) if r.get("r") else "") for r in rows)
    return '<section class="picks"><div class="ph2"><h2>%s</h2><a href="%s">See all</a></div><div class="pickgrid">%s</div></section>' % (_e(label), more, cards)

def _abs_img(s, men):
    if s.get("img"): return SITE + "/_meta/nimg/" + s["id"]
    m = next((x for x in men if x.get("img")), None)
    return (SITE + "/_meta/rimg?w=500&u=" + urllib.parse.quote(m["img"], safe="")) if m else SITE + "/pwa/icon-512.png"

def _story_ld(s, men):
    """NewsArticle data: what the story is, when, who wrote it (the publisher), what it is about (our title pages)."""
    url = SITE + "/news/a/%s-%s" % (s["id"], _slug(s["title"]))
    when = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(s["t"]))
    about = [{"@type": {"movie": "Movie", "series": "TVSeries", "game": "VideoGame"}.get(m.get("k"), "CreativeWork"), "name": m["n"],
              "url": SITE + _tp(m)} for m in men]
    ld = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": s["title"][:110], "description": s.get("excerpt") or s["title"],
          "image": [_abs_img(s, men)], "datePublished": when, "dateModified": when, "mainEntityOfPage": url, "url": url,
          "author": {"@type": "Organization", "name": s["src"]}, "isBasedOn": s["link"],
          "publisher": {"@type": "Organization", "name": "Nahoko", "logo": {"@type": "ImageObject", "url": SITE + "/pwa/icon-512.png"}},
          "articleSection": SNAME.get(s["sec"], "")}
    if about: ld["about"] = about
    img = _abs_img(s, men)
    return ('<meta property="og:type" content="article"><meta property="og:image" content="%s"><meta name="twitter:card" content="summary_large_image">'
            '<meta property="article:published_time" content="%s"><script type="application/ld+json">%s</script>') % (_e(img), when, json.dumps(ld).replace("</", "<\\/"))

def _xml(urls):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, lm in urls:
        out.append("<url><loc>%s</loc>%s</url>" % (html.escape(SITE + u), ("<lastmod>%s</lastmod>" % lm) if lm else ""))
    out.append("</urlset>")
    return "\n".join(out)

TITLES_PER_MAP = 10000

def _tp(x):
    """A title's address, the same rule as the app: /movies/<id>-<name>, /series/..., /games/..."""
    i = x["id"]; sec = "games" if i.startswith("wg") else "series" if x.get("k") == "series" else "movies"
    return "/%s/%s-%s" % (sec, i, _slug(x.get("n") or i))

def _title_ids():
    import catalog, games
    cat = catalog.load() or {"items": {}}
    ids = [(i["id"], i["n"], i.get("k")) for i in sorted(cat["items"].values(), key=lambda i: -i["v"])[:50000]]
    ids += [(g["id"], g["n"], "game") for g in sorted((games._items() or {}).values(), key=lambda g: -g["pop"])[:10000]]
    return ids

_ppl = {"t": 0, "v": []}
def _people():
    """The people with a page in the sitemaps (computed at most every 6 hours: it walks the whole cast list)."""
    import catalog
    if time.time() - _ppl["t"] > 6 * 3600 or not _ppl["v"]:
        _ppl["t"] = time.time(); _ppl["v"] = catalog.people_for_sitemap()
    return _ppl["v"]

def robots():
    return "\n".join(["User-agent: *", "Allow: /", "Allow: /_meta/rimg", "Allow: /_meta/nimg", "Disallow: /_meta/", "Disallow: /auth/",
                      "Disallow: /login", "Disallow: /settings", "Disallow: /profiles", "", "Sitemap: %s/sitemap.xml" % SITE, ""])

def sitemap_index():
    n = max(1, math.ceil(len(_title_ids()) / TITLES_PER_MAP))
    parts = ["sitemap-pages.xml", "sitemap-news.xml"] + ["sitemap-titles-%d.xml" % (i + 1) for i in range(n)]
    parts += ["sitemap-people-%d.xml" % (i + 1) for i in range(max(1, math.ceil(len(_people()) / TITLES_PER_MAP)))]
    today = time.strftime("%Y-%m-%d")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</sitemapindex>'
            % "".join("<sitemap><loc>%s/%s</loc><lastmod>%s</lastmod></sitemap>" % (SITE, p, today) for p in parts))

def sitemap(name):
    """sitemap-pages.xml, sitemap-news.xml, sitemap-titles-N.xml -> XML, or None."""
    import extras
    if name == "pages":
        import catalog, games
        urls = ["/", "/movies", "/series", "/games", "/news", "/news/movies", "/news/series", "/news/anime", "/news/games", "/news/nahoko",
                "/about", "/methodology", "/contact", "/privacy"]
        for kind, sec in (("movie", "movies"), ("series", "series")):      # every shelf, and every platform, as a page of its own
            try:
                urls += ["/%s/%s" % (sec, _slug(c["name"])) for c in catalog.view(kind).get("chips") or []]
                urls += ["/%s?on=%s" % (sec, p["id"]) for p in catalog.filters_info(kind).get("platforms") or []]
            except Exception:
                pass
        try: urls += ["/games/%s" % _slug(c["name"]) for c in games.view(None, "", 0, 1, None).get("chips") or []]
        except Exception: pass
        return _xml([(u, None) for u in dict.fromkeys(urls)])
    if name == "news":
        c = _db(); rows = c.execute("SELECT id, title, t FROM items ORDER BY t DESC LIMIT 2000").fetchall(); c.close()
        urls = [("/news/a/%s-%s" % (r["id"], _slug(r["title"])), time.strftime("%Y-%m-%d", time.gmtime(r["t"]))) for r in rows]
        urls += [("/news/p/%d-%s" % (p["id"], _slug(p["title"])), time.strftime("%Y-%m-%d", time.gmtime(p["t"]))) for p in extras.news()]
        return _xml(urls)
    m = re.fullmatch(r"people-(\d{1,3})", name)
    if m:
        k = int(m.group(1)) - 1; ps = _people()[k * TITLES_PER_MAP:(k + 1) * TITLES_PER_MAP]
        return _xml([("/people/%s-%s" % (i, _slug(n)), None) for i, n in ps]) if ps else None
    m = re.fullmatch(r"titles-(\d{1,3})", name)
    if m:
        k = int(m.group(1)) - 1; ids = _title_ids()[k * TITLES_PER_MAP:(k + 1) * TITLES_PER_MAP]
        return _xml([(_tp({"id": i, "n": n, "k": k}), None) for i, n, k in ids]) if ids else None
    return None

def llms():
    """For AI assistants and answer engines: what this site is and where things are (llmstxt.org format)."""
    return """# Nahoko

> Nahoko is a free catalogue of films, series, anime and games, with ratings, cast, episodes, trailers and news. No ads, no tracking.

Every title has its own page with its year, genres, rating, plot, cast or creator, related titles and episode ratings for series.
The news section gathers film, TV, anime and game news from the publishers' own feeds, credited to them, with background on the
titles each story is about.

## Main pages
- [Films](%(s)s/movies): shelves of new, top-rated, popular and hidden-gem films; filters by country, genre, decade and rating
- [Series](%(s)s/series): the same for TV series, with ratings for every episode
- [Games](%(s)s/games): PC, PlayStation, Xbox and Switch games with critics' scores
- [News](%(s)s/news): film, series, anime and game news ([movies](%(s)s/news/movies), [series](%(s)s/news/series), [anime](%(s)s/news/anime), [games](%(s)s/news/games))
- [About](%(s)s/about), [Contact](%(s)s/contact), [Privacy](%(s)s/privacy)

## Title pages
Addresses look like %(s)s/movies/tt0816692-interstellar or %(s)s/series/tt0903747-breaking-bad (section, IMDb identifier, then the name). Each page carries schema.org data
(Movie, TVSeries or VideoGame) with the rating.
People (actors, directors, writers) have pages too, like %(s)s/people/nm0000138-leonardo-dicaprio: a short bio, their best-known
titles and their filmography in the catalogue (schema.org Person). Ratings and titles: information courtesy of IMDb (imdb.com), used with permission.

## Sitemaps
- %(s)s/sitemap.xml
""" % {"s": SITE}

"""The news section (/news): movie, series, anime and game news from the publishers' own RSS feeds, plus Nahoko's posts.

Like a news aggregator: for each story only the headline, the short summary the publisher puts in its feed, the picture
and a link back are kept; every card and page names the source and sends readers to the full article there.
Feeds are read every 30 minutes in the background; stories older than 45 days are dropped.
Pictures come from the feed or, when it has none, from the article's own preview picture (og:image), and are served
through /_meta/nimg/<story id> (only pictures of stories we stored can be fetched)."""
import hashlib, html, json, math, os, re, sqlite3, threading, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

DBDIR = os.environ.get("DB_DIR", "/db")
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

def mentions(s):
    """Titles named in the headline (between quotes, as the trade press writes them) that the catalogue has."""
    import catalog, games
    found, seen = [], set()
    for q in re.findall(r"[‘'\"“]([^’'\"”]{2,70})[’'\"”]", s["title"]):
        q = q.strip()
        kinds = ("game",) if s["sec"] == "games" else ("series", "movie") if s["sec"] in ("series", "anime") else ("movie", "series")
        for k in kinds:
            try: hits = games.find(q, 3) if k == "game" else catalog.find(k, q, 3)
            except Exception: hits = []
            hit = next((h for h in hits if h["n"].lower() == q.lower()), None)
            if hit and hit["id"] not in seen:
                seen.add(hit["id"]); found.append(hit); break
    return found[:4]

# ---------- pages ----------
def _ago(t):
    d = max(0, time.time() - t)
    if d < 3600: return "%d min ago" % max(1, d // 60)
    if d < 86400: return "%d h ago" % (d // 3600)
    if d < 86400 * 7: return "%d d ago" % (d // 86400)
    return time.strftime("%d %b %Y", time.localtime(t)).lstrip("0")

def _slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:70] or "story"

def _e(s): return html.escape(s or "", quote=True)

def _img(s, cls=""):
    if s.get("img"): return '<img class="%s" src="/_meta/nimg/%s" alt="" loading="lazy" decoding="async">' % (cls, s["id"])
    return '<div class="%s noimg"><span>%s</span></div>' % (cls, _e(s["src"]))

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

def _side():
    """The column next to the news: what is new in the catalogue, what the family wants, our own updates."""
    import catalog, extras
    out = []
    try:
        v = catalog.view("movie")
        row = next((r for r in v.get("rows", []) if r["id"] == "new"), None)
        if row:
            li = "".join('<li><a href="/title/%s">%s</a><span>%s%s</span></li>' % (i["id"], _e(i["n"]), i.get("y") or "", (" · ★ %.1f" % i["r"]) if i.get("r") else "")
                         for i in row["items"][:6])
            out.append('<section class="box"><h4>New in the catalogue</h4><ol>%s</ol><a class="more" href="/catalogue/movies?row=new">See all</a></section>' % li)
    except Exception:
        pass
    try:
        posts = extras.news()[:3]
        li = "".join('<li><a href="/news/p/%d-%s">%s</a><span>%s</span></li>' % (p["id"], _slug(p["title"]), _e(p["title"]), _ago(p["t"])) for p in posts)
        out.append('<section class="box"><h4>Nahoko updates</h4><ol>%s</ol><a class="more" href="/news/nahoko">All updates</a></section>' % li)
    except Exception:
        pass
    out.append('<section class="box note"><h4>About these stories</h4><p>Headlines and short summaries come from each publisher\'s own news feed. '
               'Tap a story to read it in full on their site.</p></section>')
    return "".join(out)

def _shell(title, desc, body, sec="", canonical=""):
    tabs = "".join('<a href="/news%s"%s>%s</a>' % (("/" + k) if k else "", ' class="on"' if k == sec else "", n) for k, n in SECTIONS)
    return PAGE.replace("{{TITLE}}", _e(title)).replace("{{DESC}}", _e(desc)).replace("{{TABS}}", tabs).replace("{{BODY}}", body) \
               .replace("{{CANON}}", _e(canonical or "/news"))

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

def article(iid):
    s = story(iid)
    if not s: return None
    rel = related(s); men = mentions(s)
    when = time.strftime("%d %B %Y, %H:%M", time.localtime(s["t"])).lstrip("0")
    men_html = ""
    if men:
        men_html = '<section class="incat"><h2>In the catalogue</h2><div class="minis">%s</div></section>' % "".join(
            '<a class="mini" href="/title/%s">%s<b>%s</b><span>%s</span></a>' % (
                m["id"], ('<img src="/_meta/rimg?w=185&amp;u=%s" alt="" loading="lazy">' % urllib.parse.quote(m["img"], safe="")) if m.get("img") else '<i></i>',
                _e(m["n"]), m.get("y") or "") for m in men)
    body = ('<div class="layout"><main><article class="story"><div class="meta"><a class="sec sec-%s" href="/news/%s">%s</a><span>%s · %s</span></div>'
            '<h1>%s</h1>%s%s<p class="credit">Source: <a href="%s" rel="noopener nofollow" target="_blank">%s</a>. Headline, summary and picture belong to them.</p></article>%s'
            '%s</main><aside>%s</aside></div>') % (
        s["sec"], s["sec"], SNAME.get(s["sec"], ""), _e(s["src"]), when, _e(s["title"]),
        ('<figure>%s</figure>' % _img(s, "hero")) if s.get("img") else "", ('<p class="lede">%s</p>' % _e(s["excerpt"])) if s.get("excerpt") else "",
        _e(s["link"]), _e(s["src"]), men_html,
        ('<section class="rel"><h2>Related stories</h2><div class="grid">%s</div></section>' % "".join(_card(r) for r in rel)) if rel else "", _side())
    return _shell(s["title"], s.get("excerpt") or s["title"], body, s["sec"], "/news/a/%s-%s" % (s["id"], _slug(s["title"])))

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

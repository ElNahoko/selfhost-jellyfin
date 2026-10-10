"""News posts and contact messages for the site (small, so plain files: news.json and messages.db in /db)."""
import hashlib, html, json, os, re, sqlite3, threading, time

DBDIR = os.environ.get("DB_DIR", "/db")
NEWS = os.path.join(DBDIR, "news.json")
MSGS = os.path.join(DBDIR, "messages.db")
_lock = threading.Lock()

# the first posts, written when the news page does not exist yet (the admin adds the next ones from Settings)
SEED = [
    {"t": "2026-10-10", "tag": "New", "title": "Games are here",
     "body": "A new Games tab: about 19,000 games for PC, PlayStation (1 to 5), Xbox, Switch, Wii, GameCube, N64 and DS, with covers, "
             "descriptions and critics' scores.\n\nShelves for new releases, games coming soon, the best on each console, hidden gems "
             "and classics. Filter by platform, genre or decade, and save the ones you like to your favorites."},
    {"t": "2026-10-10", "tag": "Catalogue", "title": "Films from everywhere",
     "body": "The catalogue now has about 56,000 films and series, with many more from France, Spain, Italy, Germany, Japan, Korea, "
             "India, Belgium, Brazil, Scandinavia, China, Iran and Turkey.\n\nOpen Filters and pick a country: every shelf (top rated, "
             "popular, hidden gems...) is then made of that country's titles. The silent era, the golden age, film noir and every "
             "decade have their own shelves too."},
    {"t": "2026-10-10", "tag": "Family", "title": "The wishlist",
     "body": "Requests are now called the wishlist, and everyone who signs in can see it. Tap the blue button on a wish to say "
             "\"me too\": the most wanted ones are added first. Ask the owner to allow your account to add wishes yourself."},
    {"t": "2026-10-09", "tag": "Phone", "title": "Install it like an app",
     "body": "On a phone, open the browser menu and choose Add to Home screen (Android) or Share, then Add to Home Screen (iPhone). "
             "Nahoko then opens full screen with its own icon, like an app."},
    {"t": "2026-10-09", "tag": "Fun", "title": "Feeling lucky?",
     "body": "Can't choose? Tap the dice next to the search box: Nahoko picks a good film or series for you, with a little sound "
             "and a reel of posters. Roll again as many times as you like."},
]

def _load():
    try:
        with open(NEWS) as f: return json.load(f)
    except (OSError, ValueError):
        posts = [dict(p, id=i + 1, t=int(time.mktime(time.strptime(p["t"], "%Y-%m-%d"))) + 43200 - i) for i, p in enumerate(SEED)]
        _save(posts)
        return posts

def _save(posts):
    tmp = NEWS + ".tmp"
    with open(tmp, "w") as f: json.dump(posts, f, separators=(",", ":"))
    os.replace(tmp, NEWS)

def news():
    with _lock: return sorted(_load(), key=lambda p: -p["t"])

def add_news(title, body, tag=""):
    title, body, tag = (title or "").strip()[:140], (body or "").strip()[:6000], (tag or "").strip()[:24]
    if len(title) < 3 or len(body) < 3: return None
    with _lock:
        posts = _load()
        p = {"id": max([x["id"] for x in posts] or [0]) + 1, "t": int(time.time()), "title": title, "body": body, "tag": tag}
        posts.append(p); _save(posts)
        return p

def delete_news(pid):
    with _lock:
        posts = _load(); left = [p for p in posts if p["id"] != pid]
        _save(left); return len(left) != len(posts)

def news_html():
    """The posts as HTML for the news page (paragraphs from blank lines, everything escaped)."""
    out = []
    for p in news():
        paras = "".join("<p>%s</p>" % html.escape(x).replace("\n", "<br>") for x in re.split(r"\n\s*\n", p["body"]) if x.strip())
        when = time.strftime("%d %B %Y", time.localtime(p["t"])).lstrip("0")
        out.append('<article class="post" id="post-%d"><div class="pmeta">%s<time>%s</time></div><h2>%s</h2>%s</article>'
                   % (p["id"], ('<span class="ptag">%s</span>' % html.escape(p["tag"])) if p.get("tag") else "", when, html.escape(p["title"]), paras))
    return "\n".join(out) or '<p class="lead">Nothing here yet.</p>'

# ---------- contact messages ----------
def _mdb():
    c = sqlite3.connect(MSGS, timeout=10); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY, t INTEGER, name TEXT, email TEXT, body TEXT, who TEXT)")
    return c

def add_message(name, email, body, ip):
    """-> (ok, error). Short, plain text only; at most 5 messages an hour from one address."""
    name, email, body = (name or "").strip()[:80], (email or "").strip()[:120], (body or "").strip()[:4000]
    if len(body) < 5: return False, "Write a few words first."
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): return False, "That email address does not look right."
    who = hashlib.sha256(("nahoko:" + (ip or "")).encode()).hexdigest()[:16]      # never the address itself
    with _mdb() as c:
        if c.execute("SELECT count(*) FROM msgs WHERE who=? AND t>?", (who, int(time.time()) - 3600)).fetchone()[0] >= 5:
            return False, "Thanks! You have sent several messages already; please wait a little."
        c.execute("INSERT INTO msgs(t, name, email, body, who) VALUES(?,?,?,?,?)", (int(time.time()), name, email, body, who))
    return True, ""

def messages():
    with _mdb() as c: return [dict(r) for r in c.execute("SELECT id, t, name, email, body FROM msgs ORDER BY id DESC LIMIT 300")]

def delete_message(mid):
    with _mdb() as c: return c.execute("DELETE FROM msgs WHERE id=?", (mid,)).rowcount

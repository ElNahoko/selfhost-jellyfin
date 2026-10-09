"""Sign-in for the upload/catalogue site.

Roles:
  admin     the owner. Password is the existing upload password (verified against its crypt hash, never stored again).
  uploader  profiles the admin creates (name + password): they upload and browse the library, nothing else.
  member    a visitor who signed in with an email code. Keeps favorites; can request titles once the admin approves them.
  (no session = public: the catalogue only)

Sessions are random tokens in an HttpOnly, Secure, SameSite=Strict cookie; only a hash of the token is stored.
Failed sign-ins are throttled per address and per account."""
import hashlib, hmac, os, re, secrets, sqlite3, threading, time
try:
    import crypt
except Exception:      # pragma: no cover
    crypt = None

DB = os.environ.get("USERS_DB", "/db/users.db")
ADMIN_USER = os.environ.get("ADMIN_USER", "uploader")
ADMIN_HASH = os.environ.get("ADMIN_HASH", "")
COOKIE = "lm_s"
SESSION_DAYS = 30
ITER = 240000
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,23}$")
_fails = {}
_lock = threading.Lock()

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, hash TEXT, active INTEGER DEFAULT 1, created INTEGER, last_login INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS sessions(th TEXT PRIMARY KEY, username TEXT, role TEXT, exp INTEGER)")
    try:
        c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'uploader'")
    except sqlite3.OperationalError:
        pass
    c.execute("UPDATE users SET role='uploader' WHERE role IS NULL OR role='guest'")       # the old "guest" profiles are uploaders now
    c.execute("CREATE TABLE IF NOT EXISTS members(email TEXT PRIMARY KEY, approved INTEGER DEFAULT 0, created INTEGER, last_login INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS codes(email TEXT PRIMARY KEY, hash TEXT, exp INTEGER, tries INTEGER DEFAULT 0, sent INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS favorites(email TEXT, tid TEXT, added INTEGER, PRIMARY KEY(email, tid))")
    return c

def hash_pw(pw):
    salt = secrets.token_bytes(16)
    return "pbkdf2$%d$%s$%s" % (ITER, salt.hex(), hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER).hex())

def check_pw(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(got, h)
    except Exception:
        return False

def _admin_ok(user, pw):
    if user.lower() != ADMIN_USER.lower() or not ADMIN_HASH or crypt is None:
        return False
    try:
        return hmac.compare_digest(crypt.crypt(pw, ADMIN_HASH), ADMIN_HASH)
    except Exception:
        return False

WORDS_A = "amber azure bold brave bright calm clever cosmic crisp daring eager fancy gentle golden happy jolly keen lucky mellow noble proud quick quiet royal shiny silver smooth snowy solar sunny swift tidy vivid warm wild witty zesty".split()
WORDS_B = "otter falcon maple river tiger comet harbor meadow lantern pebble sparrow canyon willow ember glacier panda orchid summit breeze cactus dolphin forest island jungle koala lagoon marble nebula oasis prairie rocket savanna tundra valley walrus".split()
def new_password():
    r = secrets.SystemRandom()
    return "%s-%s-%s-%02d" % (r.choice(WORDS_A), r.choice(WORDS_B), r.choice(WORDS_A), r.randrange(100))

# ---------- throttling ----------
def _throttled(ip, user):
    now = time.time()
    with _lock:
        for k in [k for k, v in _fails.items() if now - v[-1] > 900]:
            del _fails[k]
        return len([t for t in _fails.get("ip:" + ip, []) if now - t < 600]) >= 8 or \
               len([t for t in _fails.get("u:" + user, []) if now - t < 900]) >= 10

def _fail(ip, user):
    now = time.time()
    with _lock:
        _fails.setdefault("ip:" + ip, []).append(now)
        _fails.setdefault("u:" + user, []).append(now)

# ---------- sign in / sessions ----------
def login(user, pw, ip):
    """-> (token, role) or (None, reason)"""
    user = (user or "").strip().lower()[:40]
    if not user or not pw or len(pw) > 200:
        return None, "invalid"
    if _throttled(ip, user):
        return None, "locked"
    role = None
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (user,)).fetchone()
        if r:
            if r["active"] and check_pw(pw, r["hash"]):
                role = r["role"] or "uploader"
                c.execute("UPDATE users SET last_login=? WHERE username=?", (int(time.time()), user))
        elif _admin_ok(user, pw):
            role = "admin"
        else:
            check_pw(pw, "pbkdf2$%d$%s$%s" % (ITER, "00" * 16, "00" * 32))     # same work whether or not the name exists
    if not role:
        _fail(ip, user)
        return None, "invalid"
    tok = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("DELETE FROM sessions WHERE exp<?", (int(time.time()),))
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), user, role, int(time.time()) + SESSION_DAYS * 86400))
    return tok, role

def session(cookie_header):
    """-> {"user","role"} or None"""
    m = re.search(r"(?:^|;\s*)%s=([A-Za-z0-9_-]{20,80})" % COOKIE, cookie_header or "")
    if not m:
        return None
    with db() as c:
        r = c.execute("SELECT username, exp, role FROM sessions WHERE th=?", (hashlib.sha256(m.group(1).encode()).hexdigest(),)).fetchone()
        if not r or r["exp"] < time.time():
            return None
        if r["role"] == "member":
            mem = c.execute("SELECT approved FROM members WHERE email=?", (r["username"],)).fetchone()
            return {"user": r["username"], "role": "member", "approved": bool(mem and mem["approved"])} if mem else None
        u = c.execute("SELECT active, role FROM users WHERE username=?", (r["username"],)).fetchone()
        if u:
            if not u["active"]:
                return None
            role = u["role"] or "uploader"
        elif r["username"] == ADMIN_USER.lower():
            role = "admin"
        else:
            return None
    return {"user": r["username"], "role": role}

def logout(cookie_header):
    m = re.search(r"(?:^|;\s*)%s=([A-Za-z0-9_-]{20,80})" % COOKIE, cookie_header or "")
    if m:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE th=?", (hashlib.sha256(m.group(1).encode()).hexdigest(),))

def cookie_value(tok, clear=False):
    base = "%s=%s; Path=/; HttpOnly; Secure; SameSite=Strict" % (COOKIE, "" if clear else tok)
    return base + ("; Max-Age=0" if clear else "; Max-Age=%d" % (SESSION_DAYS * 86400))

# ---------- profiles ----------
def list_users():
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT username, role, active, created, last_login FROM users ORDER BY created DESC")]
    for r in rows:
        r["role"] = r["role"] or "uploader"
        r["builtin"] = r["username"] == ADMIN_USER.lower()
    if not any(r["builtin"] for r in rows):
        rows.append({"username": ADMIN_USER.lower(), "role": "admin", "active": 1, "created": None, "last_login": None, "builtin": True})
    return rows

def admin_count():
    return len([u for u in list_users() if u["role"] == "admin" and u["active"]])

def get_role(name):
    for u in list_users():
        if u["username"] == name:
            return u["role"]
    return None

def create_user(name, role="uploader"):
    name = (name or "").strip().lower()
    role = "admin" if role == "admin" else "uploader"
    if not USER_RE.match(name) or name == ADMIN_USER.lower():
        return None, "Use 3-24 letters, numbers, dots, dashes."
    pw = new_password()
    try:
        with db() as c:
            c.execute("INSERT INTO users(username, hash, active, created, last_login, role) VALUES(?,?,1,?,NULL,?)", (name, hash_pw(pw), int(time.time()), role))
    except sqlite3.IntegrityError:
        return None, "That name is taken."
    return pw, None

def reset_user(name):
    pw = new_password()
    with db() as c:
        n = c.execute("UPDATE users SET hash=? WHERE username=?", (hash_pw(pw), name)).rowcount
        c.execute("DELETE FROM sessions WHERE username=?", (name,))
    return pw if n else None

def set_active(name, on):
    with db() as c:
        c.execute("UPDATE users SET active=? WHERE username=?", (1 if on else 0, name))
        if not on:
            c.execute("DELETE FROM sessions WHERE username=?", (name,))

def delete_user(name):
    with db() as c:
        c.execute("DELETE FROM users WHERE username=?", (name,))
        c.execute("DELETE FROM sessions WHERE username=?", (name,))

def change_password(user, current, new, cookie_header):
    """Own password. -> None on success, else an error message. Other sessions of this account are signed out."""
    if not new or len(new) < 8 or len(new) > 100 or new.lower() == user:
        return "Use at least 8 characters."
    if _throttled("pw:" + user, user):
        return "Too many tries. Wait a few minutes."
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE username=?", (user,)).fetchone()
        ok = check_pw(current or "", r["hash"]) if r else _admin_ok(user, current or "")
        if not ok:
            _fail("pw:" + user, user)
            return "Current password is wrong."
        h = hash_pw(new)
        if r:
            c.execute("UPDATE users SET hash=? WHERE username=?", (h, user))
        else:
            c.execute("INSERT INTO users(username, hash, active, created, last_login, role) VALUES(?,?,1,?,?,'admin')", (user, h, int(time.time()), int(time.time())))
        m = re.search(r"(?:^|;\s*)%s=([A-Za-z0-9_-]{20,80})" % COOKIE, cookie_header or "")
        keep = hashlib.sha256(m.group(1).encode()).hexdigest() if m else ""
        c.execute("DELETE FROM sessions WHERE username=? AND th<>?", (user, keep))
    return None

# ---------- members: email + one-time code (no password) ----------
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
CODE_MINUTES = 15

def request_code(email, ip):
    """-> (email, code) to send, or (None, reason). One code per address at a time, at most one every 60 s."""
    email = (email or "").strip().lower()[:254]
    if not EMAIL_RE.match(email): return None, "That email address does not look right."
    if _throttled(ip, "code:" + email): return None, "Too many tries. Wait a few minutes."
    now = int(time.time())
    with db() as c:
        r = c.execute("SELECT sent FROM codes WHERE email=?", (email,)).fetchone()
        if r and r["sent"] and now - r["sent"] < 60: return None, "A code was just sent. Check your inbox (and spam), or wait a minute."
        code = "%06d" % secrets.randbelow(1000000)
        c.execute("INSERT OR REPLACE INTO codes VALUES(?,?,?,0,?)", (email, hashlib.sha256((email + code).encode()).hexdigest(), now + CODE_MINUTES * 60, now))
    _fail(ip, "code:" + email)          # counts towards the throttle: requesting codes in a loop gets blocked
    return email, code

def verify_code(email, code, ip):
    """-> (token, None) or (None, reason). A code works once, for 15 minutes, 5 tries."""
    email = (email or "").strip().lower()[:254]; code = re.sub(r"\D", "", str(code or ""))[:6]
    if _throttled(ip, "verify:" + email): return None, "Too many tries. Wait a few minutes."
    now = int(time.time())
    with db() as c:
        r = c.execute("SELECT * FROM codes WHERE email=?", (email,)).fetchone()
        if not r or r["exp"] < now or r["tries"] >= 5:
            return None, "That code has expired. Ask for a new one."
        if not hmac.compare_digest(r["hash"], hashlib.sha256((email + code).encode()).hexdigest()):
            c.execute("UPDATE codes SET tries=tries+1 WHERE email=?", (email,)); _fail(ip, "verify:" + email)
            return None, "That code is not right."
        c.execute("DELETE FROM codes WHERE email=?", (email,))
        c.execute("INSERT OR IGNORE INTO members(email, approved, created) VALUES(?,0,?)", (email, now))
        c.execute("UPDATE members SET last_login=? WHERE email=?", (now, email))
        tok = secrets.token_urlsafe(32)
        c.execute("DELETE FROM sessions WHERE exp<?", (now,))
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), email, "member", now + SESSION_DAYS * 86400))
    return tok, None

def list_members():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT m.email, m.approved, m.created, m.last_login, (SELECT count(*) FROM favorites f WHERE f.email=m.email) AS favorites FROM members m ORDER BY m.approved, m.created DESC")]

def set_member(email, approved):
    with db() as c:
        return c.execute("UPDATE members SET approved=? WHERE email=?", (1 if approved else 0, email)).rowcount

def delete_member(email):
    with db() as c:
        c.execute("DELETE FROM members WHERE email=?", (email,)); c.execute("DELETE FROM favorites WHERE email=?", (email,))
        c.execute("DELETE FROM sessions WHERE username=?", (email,))

def favorites(email):
    with db() as c:
        return [r[0] for r in c.execute("SELECT tid FROM favorites WHERE email=? ORDER BY added DESC", (email,))]

def set_favorites(email, add=(), remove=()):
    now = int(time.time())
    with db() as c:
        c.executemany("INSERT OR IGNORE INTO favorites VALUES(?,?,?)", [(email, t, now) for t in add])
        c.executemany("DELETE FROM favorites WHERE email=? AND tid=?", [(email, t) for t in remove])

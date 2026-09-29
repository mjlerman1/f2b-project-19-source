"""Fund2Build app kit v1: a standard-library WSGI + SQLite skeleton for apps.

Copy this file verbatim to ``src/appkit.py``. It supplies password sign-in,
database-backed sessions with CSRF, groups with membership-scoped data helpers,
numbered forward-only migrations, security headers and ``/health``. Read
``app_kit/README.md`` for the rules an app built on the kit must follow.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import time
from contextlib import contextmanager
from http import cookies
from http.client import responses
from pathlib import Path
from urllib.parse import parse_qsl
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

KIT_VERSION = 1
DB_FILENAME = "app.db"
SESSION_COOKIE = "appkit_session"
SESSION_TTL = 14 * 86400
MAX_BODY = 1024 * 1024
PBKDF2_ITERATIONS = 240_000
MAX_FAILED_SIGNINS, LOCKOUT_SECONDS = 10, 900
USERNAME = re.compile(r"[a-z0-9_]{3,32}\Z")
IDENT = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
MIGRATION_FILE = re.compile(r"(\d{4})_([a-z0-9_]{1,60})\.sql\Z")
FORBIDDEN_SQL = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "ATTACH", "DETACH", "VACUUM", "PRAGMA"}
ROLES = ("owner", "admin", "member")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
KIT_TABLES = {"schema_version", "users", "sessions", "user_groups", "memberships"}
KIT_MIGRATIONS = ((1, "core", """
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL, failed_signins INTEGER NOT NULL DEFAULT 0, locked_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sessions (token_sha256 TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE user_groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL);
CREATE TABLE memberships (group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')), created_at INTEGER NOT NULL,
  PRIMARY KEY (group_id, user_id));
CREATE INDEX memberships_user ON memberships(user_id);
"""),)


class MigrationError(RuntimeError):
    """Migrations are missing, edited, out of order or unsafe; refuse to start."""


class HTTPError(Exception):
    def __init__(self, status, message=None):
        super().__init__(message or responses.get(status, "Error"))
        self.status, self.message = status, message or responses.get(status, "Error")


class Response:
    def __init__(self, body=b"", status=200, content_type="text/plain; charset=utf-8", headers=()):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status, self.headers = status, [("Content-Type", content_type), *headers]


def json_response(value, status=200):
    return Response(json.dumps(value, separators=(",", ":")), status, "application/json")


def html_response(text, status=200):
    return Response(text, status, "text/html; charset=utf-8")


def redirect(location, status=303):
    if not location.startswith("/") or location.startswith("//") or "\\" in location:
        raise ValueError("Redirects must stay on this app")
    return Response(b"", status, headers=[("Location", location)])


h = html.escape


def page(title, body_html):
    """Minimal HTML shell; escape every dynamic value with ``h`` before passing it."""
    return html_response(f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
                         f"<title>{h(title)}</title></head><body>{body_html}</body></html>")


# ---- database and migrations -------------------------------------------------

def connect(path):
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    for pragma in ("foreign_keys=ON", "journal_mode=WAL", "synchronous=FULL"):
        conn.execute("PRAGMA " + pragma)
    return conn


@contextmanager
def transaction(conn, mode="IMMEDIATE"):
    conn.execute("BEGIN " + mode)
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _first_word(statement):
    text = statement
    while True:
        text = text.lstrip()
        if text.startswith("--"):
            text = text.partition("\n")[2]
        elif text.startswith("/*"):
            text = text.partition("*/")[2]
        else:
            return re.match(r"[A-Za-z]*", text)[0].upper()


def split_sql(sql):
    """Split a migration into single statements; transaction control is refused."""
    buffer, result, *pieces, tail = "", [], *sql.split(";")
    for piece in pieces:
        buffer += piece + ";"
        if sqlite3.complete_statement(buffer):
            if _first_word(buffer):
                if _first_word(buffer) in FORBIDDEN_SQL:
                    raise MigrationError(f"Migrations may not use {_first_word(buffer)}; the kit owns transactions")
                result.append(buffer)
            buffer = ""
    if _first_word(buffer + tail):
        raise MigrationError("Migration ends with an incomplete SQL statement; end each with ;")
    return result


def load_migrations(directory):
    """Return ``[(version, name, sql)]`` from ``NNNN_name.sql`` files numbered 1..n."""
    directory, items = Path(directory), []
    for path in sorted(directory.iterdir()) if directory.is_dir() else ():
        match = MIGRATION_FILE.fullmatch(path.name)
        if not match or not path.is_file():
            raise MigrationError(f"Unexpected entry in migrations directory: {path.name}")
        items.append((int(match[1]), match[2], path.read_text("utf-8")))
    if [v for v, _, _ in items] != list(range(1, len(items) + 1)):
        raise MigrationError("Migrations must be numbered 0001, 0002, ... with no gaps or duplicates")
    return items


def _digest(sql):
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def apply_migrations(conn, component, items):
    """Apply pending migrations in one transaction; applied files must never change."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (component TEXT NOT NULL, version INTEGER NOT NULL, "
                 "name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at INTEGER NOT NULL, PRIMARY KEY (component, version))")
    applied = 0
    with transaction(conn):
        done = {r["version"]: (r["name"], r["sha256"]) for r in
                conn.execute("SELECT version, name, sha256 FROM schema_version WHERE component=?", (component,))}
        if set(done) - {v for v, _, _ in items}:
            raise MigrationError(f"Database has newer {component} migrations than this code; refusing to downgrade")
        for version, name, sql in items:
            if version in done:
                if done[version] != (name, _digest(sql)):
                    raise MigrationError(f"Applied {component} migration {version:04d} was edited; add a new one instead")
                continue
            for statement in split_sql(sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version VALUES (?, ?, ?, ?, ?)",
                         (component, version, name, _digest(sql), int(time.time())))
            applied += 1
    return applied


def check_group_tables(conn, global_tables=()):
    """Every app table must carry ``group_id NOT NULL`` unless declared global."""
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if table in KIT_TABLES or table in global_tables:
            continue
        columns = {r["name"]: r for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if "group_id" not in columns or not columns["group_id"]["notnull"]:
            raise MigrationError(f"Table {table} needs group_id INTEGER NOT NULL or must be declared global")


def schema_dump(conn):
    rows = conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                        "ORDER BY type DESC, name").fetchall()
    return "".join(r["sql"].strip() + ";\n" for r in rows)


# ---- passwords, sessions and requests ------------------------------------------

def hash_password(password, *, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, expected = stored.split("$")
        actual = hash_password(password, salt=bytes.fromhex(salt), iterations=int(iterations))
    except ValueError:
        return False
    return scheme == "pbkdf2_sha256" and hmac.compare_digest(actual, stored)


_DUMMY_HASH = hash_password("appkit-timing-equaliser", salt=b"\0" * 16)


def _token_hash(token):
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


class Request:
    def __init__(self, app, environ, db):
        self.app, self.environ, self.db = app, environ, db
        self.method = environ["REQUEST_METHOD"].upper()
        self.path = environ.get("PATH_INFO") or "/"
        self.query = dict(parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True))
        self.content_type = (environ.get("CONTENT_TYPE") or "").split(";")[0].strip().lower()
        self.session = self.user = None
        self.new_cookie = None
        self._body = None

    @property
    def body(self):
        if self._body is None:
            try:
                length = int(self.environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                raise HTTPError(400, "Invalid Content-Length") from None
            if length < 0 or length > self.app.max_body:
                raise HTTPError(413, "Request body too large")
            self._body = self.environ["wsgi.input"].read(length) if length else b""
        return self._body

    @property
    def is_form(self):
        return self.content_type == "application/x-www-form-urlencoded"

    def json(self):
        try:
            value = json.loads(self.body or b"{}")
        except ValueError:
            raise HTTPError(400, "Invalid JSON body") from None
        if not isinstance(value, dict):
            raise HTTPError(400, "JSON body must be an object")
        return value

    def form(self):
        try:
            return dict(parse_qsl(self.body.decode("utf-8"), keep_blank_values=True, max_num_fields=200))
        except (UnicodeDecodeError, ValueError):
            raise HTTPError(400, "Invalid form body") from None

    def data(self):
        """JSON or form fields, by content type."""
        return self.form() if self.is_form else self.json()

    def require_user(self):
        if not self.user:
            raise HTTPError(401, "Sign in required")
        return self.user

    def group(self, group_id, roles=ROLES):
        """Membership-checked data scope; non-members get 404 so ids do not leak."""
        user = self.require_user()
        row = self.db.execute("SELECT role FROM memberships WHERE group_id=? AND user_id=?",
                              (group_id, user["id"])).fetchone()
        if not row:
            raise HTTPError(404, "Not found")
        if row["role"] not in roles:
            raise HTTPError(403, "Your role in this group cannot do that")
        return GroupScope(self.db, group_id, user["id"], row["role"])

    def start_session(self, user_id):
        """Rotate to a fresh session (fixation defence) for ``user_id`` or anonymous."""
        if self.session:
            self.db.execute("DELETE FROM sessions WHERE token_sha256=?", (self.session["token_sha256"],))
        token, now = secrets.token_urlsafe(32), int(time.time())
        self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                        (_token_hash(token), user_id, secrets.token_urlsafe(32), now, now + SESSION_TTL))
        self.session = self.db.execute("SELECT * FROM sessions WHERE token_sha256=?", (_token_hash(token),)).fetchone()
        self.user = self.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
        self.new_cookie = token
        return self.session

    def end_session(self):
        if self.session:
            self.db.execute("DELETE FROM sessions WHERE token_sha256=?", (self.session["token_sha256"],))
        self.session = self.user = None
        self.new_cookie = ""

    def csrf_token(self):
        return (self.session or self.start_session(None))["csrf_token"]


class GroupScope:
    """Reads and writes that always filter on ``group_id``. Use for all group data."""

    def __init__(self, db, group_id, user_id, role):
        self.db, self.group_id, self.user_id, self.role = db, group_id, user_id, role

    def _columns(self, table, names=()):
        if not IDENT.fullmatch(table) or table in KIT_TABLES:
            raise ValueError(f"Not a group-owned table: {table}")
        columns = {r["name"] for r in self.db.execute(f'PRAGMA table_info("{table}")')}
        if "group_id" not in columns:
            raise ValueError(f"Table {table} has no group_id column")
        for name in names:
            if not IDENT.fullmatch(name) or name not in columns:
                raise ValueError(f"Unknown column {table}.{name}")
            if name in ("id", "group_id"):
                raise ValueError("id and group_id are managed by the kit")
        return columns

    def require_role(self, *roles):
        if self.role not in roles:
            raise HTTPError(403, "Your role in this group cannot do that")

    def find(self, table, row_id):
        self._columns(table)
        return self.db.execute(f'SELECT * FROM "{table}" WHERE id=? AND group_id=?', (row_id, self.group_id)).fetchone()

    def get(self, table, row_id):
        row = self.find(table, row_id)
        if row is None:
            raise HTTPError(404, "Not found")
        return row

    def list(self, table, *, order_by="id", **equals):
        self._columns(table, [*equals, *([order_by] if order_by != "id" else [])])
        where = "".join(f' AND "{k}"=?' for k in equals)
        return self.db.execute(f'SELECT * FROM "{table}" WHERE group_id=?{where} ORDER BY "{order_by}", id',
                               (self.group_id, *equals.values())).fetchall()

    def insert(self, table, **values):
        self._columns(table, values)
        names = ", ".join(f'"{k}"' for k in ("group_id", *values))
        marks = ", ".join("?" for _ in range(len(values) + 1))
        return self.db.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                               (self.group_id, *values.values())).lastrowid

    def update(self, table, row_id, **values):
        self._columns(table, values)
        if not values:
            return self.get(table, row_id)
        sets = ", ".join(f'"{k}"=?' for k in values)
        if not self.db.execute(f'UPDATE "{table}" SET {sets} WHERE id=? AND group_id=?',
                               (*values.values(), row_id, self.group_id)).rowcount:
            raise HTTPError(404, "Not found")
        return self.get(table, row_id)

    def delete(self, table, row_id):
        self._columns(table)
        if not self.db.execute(f'DELETE FROM "{table}" WHERE id=? AND group_id=?', (row_id, self.group_id)).rowcount:
            raise HTTPError(404, "Not found")


# ---- application -----------------------------------------------------------------

def _pattern(route):
    parts = re.split(r"(<(?:int:)?[a-z_][a-z0-9_]*>)", route)
    regex = "".join((f"(?P<{p[5:-1]}>[1-9][0-9]{{0,17}})" if p.startswith("<int:") else f"(?P<{p[1:-1]}>[^/]+)")
                    if p.startswith("<") else re.escape(p) for p in parts)
    return re.compile(regex + r"\Z"), {p[5:-1] for p in parts if p.startswith("<int:")}


def _user_json(user):
    return {"id": user["id"], "username": user["username"]} if user else None


class App:
    """WSGI app. ``App(name, migrations_dir)``; register routes with ``@app.route``."""

    def __init__(self, name, migrations_dir, *, global_tables=(), max_body=MAX_BODY):
        self.name, self.migrations_dir = name, Path(migrations_dir)
        self.global_tables, self.max_body = set(global_tables), max_body
        self.routes, self.db_path = [], None
        for method, route, handler in _BUILTIN_ROUTES:
            self.route(method, route)(handler)

    def route(self, method, route):
        regex, ints = _pattern(route)

        def register(handler):
            self.routes.append((method.upper(), regex, ints, handler))
            return handler
        return register

    def open(self, data_dir):
        """Create/migrate ``<data_dir>/app.db``; refuses to start on migration problems."""
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / DB_FILENAME
        conn = connect(self.db_path)
        try:
            apply_migrations(conn, "appkit", KIT_MIGRATIONS)
            apply_migrations(conn, "app", load_migrations(self.migrations_dir))
            check_group_tables(conn, self.global_tables)
        finally:
            conn.close()
        return self

    def _match(self, method, path):
        allowed = False
        for route_method, regex, ints, handler in self.routes:
            match = regex.fullmatch(path)
            if match:
                if route_method == method or (method == "HEAD" and route_method == "GET"):
                    return handler, {k: int(v) if k in ints else v for k, v in match.groupdict().items()}
                allowed = True
        raise HTTPError(405 if allowed else 404)

    def _authenticate(self, req):
        jar = cookies.SimpleCookie()
        try:
            jar.load(req.environ.get("HTTP_COOKIE", ""))
        except cookies.CookieError:
            return
        if SESSION_COOKIE in jar:
            row = req.db.execute("SELECT * FROM sessions WHERE token_sha256=? AND expires_at>?",
                                 (_token_hash(jar[SESSION_COOKIE].value), int(time.time()))).fetchone()
            if row:
                req.session = row
                if row["user_id"]:
                    req.user = req.db.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()

    def _check_csrf(self, req):
        origin = req.environ.get("HTTP_ORIGIN")
        expected = (os.environ.get("APPKIT_PUBLIC_ORIGIN") or
                    f"{req.environ.get('wsgi.url_scheme', 'http')}://{req.environ.get('HTTP_HOST', '')}")
        if origin and origin != expected:
            raise HTTPError(403, "Cross-origin request refused")
        sent = req.environ.get("HTTP_X_CSRF_TOKEN") or (req.form().get("csrf_token") if req.is_form else None)
        if (not req.session or not isinstance(sent, str) or
                not hmac.compare_digest(sent.encode("utf-8"), req.session["csrf_token"].encode("utf-8"))):
            raise HTTPError(403, "Missing or invalid CSRF token")

    def handle(self, environ):
        if self.db_path is None:
            raise RuntimeError("Call app.open(data_dir) before serving")
        db = connect(self.db_path)
        req = Request(self, environ, db)
        try:
            handler, params = self._match(req.method, req.path)
            with transaction(db, "IMMEDIATE" if req.method in UNSAFE else "DEFERRED"):
                self._authenticate(req)
                if req.method in UNSAFE:
                    self._check_csrf(req)
                response = handler(req, **params)
        except HTTPError as exc:
            req.new_cookie = None  # any session change was rolled back with the transaction
            response = (json_response({"error": exc.message}, exc.status) if req.path.startswith("/api/")
                        else Response(exc.message, exc.status))
            if exc.status == 401 and req.method == "GET" and not req.path.startswith("/api/"):
                response = redirect("/signin")
        except Exception:
            print("appkit: unhandled error", req.method, req.path, file=sys.stderr)
            import traceback
            traceback.print_exc()
            response = Response("Internal error", 500)
        finally:
            db.close()
        if req.new_cookie is not None:
            jar = cookies.SimpleCookie()
            jar[SESSION_COOKIE] = req.new_cookie
            morsel = jar[SESSION_COOKIE]
            morsel.update({"path": "/", "httponly": True, "samesite": "Lax",
                           "max-age": SESSION_TTL if req.new_cookie else 0})
            if os.environ.get("APPKIT_SECURE_COOKIES") == "1":
                morsel["secure"] = True
            response.headers.append(("Set-Cookie", morsel.OutputString()))
        return response, req.method

    def __call__(self, environ, start_response):
        response, method = self.handle(environ)
        headers = [*response.headers, ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                   ("Referrer-Policy", "same-origin"), ("Cache-Control", "no-store"),
                   ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"),
                   ("Content-Length", str(len(response.body)))]
        start_response(f"{response.status} {responses.get(response.status, 'Status')}", headers)
        return [b"" if method == "HEAD" else response.body]


# ---- built-in routes: health, accounts, groups -----------------------------------

def _health(req):
    req.db.execute("SELECT 1 FROM schema_version LIMIT 1").fetchall()
    return json_response({"status": "ok"})


def _me(req):
    return json_response({"user": _user_json(req.user), "csrf_token": req.csrf_token()})


def _credentials(req):
    data = req.data()
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise HTTPError(400, "Username must be 3-32 lowercase letters, digits or underscores")
    if not isinstance(password, str) or not 10 <= len(password) <= 256:
        raise HTTPError(400, "Password must be 10-256 characters")
    return username, password


def _signed_in(req, status):
    if req.is_form:
        return redirect("/")
    return json_response({"user": _user_json(req.user), "csrf_token": req.session["csrf_token"]}, status)


def _signup(req):
    username, password = _credentials(req)
    if req.db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise HTTPError(409, "That username is taken")
    user_id = req.db.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                             (username, hash_password(password), int(time.time()))).lastrowid
    req.start_session(user_id)
    return _signed_in(req, 201)


def _signin(req):
    username, password = _credentials(req)
    now = int(time.time())
    user = req.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if user and user["locked_until"] > now:
        raise HTTPError(429, "Too many failed sign-ins; try again later")
    if not verify_password(password, user["password_hash"] if user else _DUMMY_HASH) or not user:
        if user:
            failed = user["failed_signins"] + 1
            req.db.execute("UPDATE users SET failed_signins=?, locked_until=? WHERE id=?",
                           (0 if failed >= MAX_FAILED_SIGNINS else failed,
                            now + LOCKOUT_SECONDS if failed >= MAX_FAILED_SIGNINS else 0, user["id"]))
        # Commit the failure count: returning (not raising) keeps the transaction.
        return json_response({"error": "Wrong username or password"}, 401)
    req.db.execute("UPDATE users SET failed_signins=0, locked_until=0 WHERE id=?", (user["id"],))
    req.db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
    req.start_session(user["id"])
    return _signed_in(req, 200)


def _signout(req):
    req.end_session()
    return redirect("/signin") if req.is_form else json_response({"user": None})


def _signin_page(req):
    token = h(req.csrf_token())
    form = ('<form method="post" action="/api/{0}"><h2>{1}</h2><input type="hidden" name="csrf_token" value="{2}">'
            '<label>Username <input name="username" autocomplete="username" required></label> '
            '<label>Password <input name="password" type="password" minlength="10" required></label> '
            '<button>{1}</button></form>')
    return page("Sign in", f"<h1>{h(req.app.name)}</h1>" + form.format("signin", "Sign in", token) +
                form.format("signup", "Create account", token))


def _group_json(row):
    return {"id": row["id"], "name": row["name"], "role": row["role"]}


def _groups(req):
    user = req.require_user()
    rows = req.db.execute("SELECT g.id, g.name, m.role FROM user_groups g JOIN memberships m ON m.group_id=g.id "
                          "WHERE m.user_id=? ORDER BY g.id", (user["id"],)).fetchall()
    return json_response({"groups": [_group_json(r) for r in rows]})


def _create_group(req):
    user = req.require_user()
    name = req.data().get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise HTTPError(400, "Group name must be 1-80 characters")
    now = int(time.time())
    group_id = req.db.execute("INSERT INTO user_groups (name, created_by, created_at) VALUES (?, ?, ?)",
                              (name.strip(), user["id"], now)).lastrowid
    req.db.execute("INSERT INTO memberships VALUES (?, ?, 'owner', ?)", (group_id, user["id"], now))
    if req.is_form:
        return redirect("/")
    return json_response({"group": {"id": group_id, "name": name.strip(), "role": "owner"}}, 201)


def _members(req, gid):
    req.group(gid)
    rows = req.db.execute("SELECT u.id, u.username, m.role FROM memberships m JOIN users u ON u.id=m.user_id "
                          "WHERE m.group_id=? ORDER BY u.username", (gid,)).fetchall()
    return json_response({"members": [{"user_id": r["id"], "username": r["username"], "role": r["role"]} for r in rows]})


def _add_member(req, gid):
    scope = req.group(gid, roles=("owner", "admin"))
    data = req.data()
    role = data.get("role", "member")
    if role not in ROLES:
        raise HTTPError(400, "Unknown role")
    if role != "member":
        scope.require_role("owner")
    username = data.get("username")
    user = req.db.execute("SELECT id, username FROM users WHERE username=?",
                          (username,)).fetchone() if isinstance(username, str) else None
    if not user:
        raise HTTPError(404, "No such user")
    if req.db.execute("SELECT 1 FROM memberships WHERE group_id=? AND user_id=?", (gid, user["id"])).fetchone():
        raise HTTPError(409, "Already a member")
    req.db.execute("INSERT INTO memberships VALUES (?, ?, ?, ?)", (gid, user["id"], role, int(time.time())))
    return json_response({"member": {"user_id": user["id"], "username": user["username"], "role": role}}, 201)


def _remove_member(req, gid, uid):
    scope = req.group(gid, roles=("owner", "admin"))
    row = req.db.execute("SELECT role FROM memberships WHERE group_id=? AND user_id=?", (gid, uid)).fetchone()
    if not row:
        raise HTTPError(404, "Not found")
    if row["role"] != "member":
        scope.require_role("owner")
        if row["role"] == "owner" and req.db.execute(
                "SELECT COUNT(*) FROM memberships WHERE group_id=? AND role='owner'", (gid,)).fetchone()[0] == 1:
            raise HTTPError(409, "A group needs at least one owner")
    req.db.execute("DELETE FROM memberships WHERE group_id=? AND user_id=?", (gid, uid))
    return json_response({"removed": uid})


_BUILTIN_ROUTES = (
    ("GET", "/health", _health), ("GET", "/api/me", _me), ("POST", "/api/signup", _signup),
    ("POST", "/api/signin", _signin), ("POST", "/api/signout", _signout), ("GET", "/signin", _signin_page),
    ("GET", "/api/groups", _groups), ("POST", "/api/groups", _create_group),
    ("GET", "/api/groups/<int:gid>/members", _members), ("POST", "/api/groups/<int:gid>/members", _add_member),
    ("DELETE", "/api/groups/<int:gid>/members/<int:uid>", _remove_member),
)


# ---- serving and testing ---------------------------------------------------------

class _Handler(WSGIRequestHandler):
    timeout = 30

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))


def serve(app):
    """Run from ``APP_DATA_DIR`` (required), ``PORT`` (default 8080) and ``HOST``."""
    data_dir = os.environ.get("APP_DATA_DIR")
    if not data_dir:
        raise SystemExit("APP_DATA_DIR must name the durable data directory")
    app.open(data_dir)
    server = make_server(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8080")), app,
                         server_class=WSGIServer, handler_class=_Handler)

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class Client:
    """In-process test client with a cookie jar and automatic CSRF header."""

    def __init__(self, app):
        self.app, self.cookie, self.csrf = app, None, None

    def request(self, method, path, *, json_body=None, form=None, csrf=True, headers=None):
        import io
        body, content_type = b"", ""
        if json_body is not None:
            body, content_type = json.dumps(json_body).encode(), "application/json"
        elif form is not None:
            from urllib.parse import urlencode
            body, content_type = urlencode(form).encode(), "application/x-www-form-urlencoded"
        if method.upper() in UNSAFE and csrf and self.csrf is None:
            self.csrf = self.request("GET", "/api/me").json()["csrf_token"]
        environ = {"REQUEST_METHOD": method.upper(), "PATH_INFO": path.split("?")[0],
                   "QUERY_STRING": path.partition("?")[2], "CONTENT_TYPE": content_type,
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body), "wsgi.url_scheme": "http",
                   "HTTP_HOST": "testserver", "SERVER_NAME": "testserver", "SERVER_PORT": "80"}
        if self.cookie:
            environ["HTTP_COOKIE"] = f"{SESSION_COOKIE}={self.cookie}"
        if method.upper() in UNSAFE and csrf:
            environ["HTTP_X_CSRF_TOKEN"] = self.csrf
        for key, value in (headers or {}).items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value
        captured = {}
        body = b"".join(self.app(environ, lambda status, hdrs: captured.update(status=status, headers=hdrs)))
        result = ClientResponse(int(captured["status"].split()[0]), captured["headers"], body)
        for name, value in result.headers:
            if name == "Set-Cookie" and value.startswith(SESSION_COOKIE + "="):
                self.cookie = value.split(";")[0].split("=", 1)[1] or None
                self.csrf = None
        if (result.status < 400 and result.content_type == "application/json" and
                isinstance(result.json(), dict) and "csrf_token" in result.json()):
            self.csrf = result.json()["csrf_token"]
        return result

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, json_body=body, **kw)

    def signup(self, username, password="correct horse battery"):
        return self.post("/api/signup", {"username": username, "password": password})


class ClientResponse:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body
        self.content_type = dict(headers).get("Content-Type", "").split(";")[0]

    @property
    def text(self):
        return self.body.decode("utf-8")

    def json(self):
        return json.loads(self.body)

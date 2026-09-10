"""WSGI application: sign-in gateway, role-checked API and the app's own files.

The same code serves both deployments:
  Vercel     entrypoint server.wsgi:app (see pyproject.toml); data in Neon Postgres
  your PC    python server/app.py; data in a local SQLite file
"""
import email.utils
import http
import json
import mimetypes
import os
import posixpath
import sys
import traceback
import urllib.parse
from http import cookies

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api        # noqa: E402
import config     # noqa: E402
import db as dbm  # noqa: E402
import security   # noqa: E402

COOKIE = "wfs_session"
MAX_BODY = 64 * 1024
# Served without a session: just enough to draw the sign-in page.
PUBLIC_FILES = {"/login.html", "/src/css/app.css", "/src/css/fonts.css", "/src/js/login.js"}
# What the signed-in app needs. Anything not matched - data/, server/, tools/,
# README - is never served, signed in or not.
APP_PREFIXES = ("/src/js/", "/src/css/")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class App:
    def __init__(self, db_target, seed_path, secure=False, trust_proxy=False):
        """secure: the site is only ever reached over HTTPS.
        trust_proxy: read client IP / scheme / host from forwarding headers - only
        safe behind a proxy that sets them itself (Vercel does)."""
        self.db = dbm.DB(db_target, seed_path)
        self.seed_path, self.secure, self.trust_proxy = seed_path, secure, trust_proxy
        self.limiter = security.LoginLimiter(self.db)

    def cookie(self, token, ctx, expire=False):
        c = "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (COOKIE, token)
        c += "; Max-Age=0" if expire else "; Max-Age=%d" % (security.SESSION_HOURS * 3600)
        return c + ("; Secure" if (self.secure or ctx.get("https")) else "")

    def __call__(self, environ, start_response):
        try:
            return _Request(self, environ, start_response).dispatch()
        except Exception:
            traceback.print_exc()          # goes to the host's logs, never to the browser
            return _server_error(start_response)


def _server_error(start_response):
    body = b'{"error": "Something went wrong on the server"}'
    start_response("500 Internal Server Error", [("Content-Type", "application/json"),
                                                 ("Content-Length", str(len(body)))])
    return [body]


class _Request:
    def __init__(self, app, environ, start_response):
        self.app, self.env, self.start = app, environ, start_response
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        proxied = app.trust_proxy
        scheme = (self.header("X-Forwarded-Proto") or "").split(",")[0].strip() if proxied else ""
        self.https = app.secure or environ.get("wsgi.url_scheme") == "https" or scheme == "https"
        fwd = ""
        if proxied:
            fwd = (self.header("X-Vercel-Forwarded-For") or self.header("X-Real-IP")
                   or self.header("X-Forwarded-For") or "").split(",")[0].strip()
        self.ip = fwd or environ.get("REMOTE_ADDR", "")
        self.host = (self.header("X-Forwarded-Host") if proxied else None) or self.header("Host") or ""
        self._user, self._looked_up = None, False

    # ------------------------------------------------------------ helpers
    def header(self, name):
        return self.env.get("HTTP_" + name.upper().replace("-", "_"))

    def token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.header("Cookie") or "")
        except cookies.CookieError:
            return None
        return jar[COOKIE].value if COOKIE in jar else None

    def user(self):
        if not self._looked_up:
            self._looked_up = True
            self._user = self.app.db.session_user(self.token())
        return self._user

    def ctx(self):
        return {"token": self.token(), "user": self.user(), "ip": self.ip, "https": self.https}

    def send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=(), cache="no-store"):
        headers = [("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", cache),
                   ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                   ("Referrer-Policy", "no-referrer"), ("Content-Security-Policy", CSP)]
        if self.https:
            headers.append(("Strict-Transport-Security", "max-age=31536000"))
        headers.extend(extra)
        self.start("%d %s" % (status, http.HTTPStatus(status).phrase), headers)
        return [b"" if self.method == "HEAD" else body]

    def json(self, status, payload, extra=()):
        return self.send(status, json.dumps(payload).encode("utf-8"), extra=extra)

    def redirect(self, where):
        return self.send(302, b"", "text/plain", extra=[("Location", where)])

    def static(self, path):
        root = os.path.realpath(config.PROJECT_ROOT)
        full = os.path.realpath(os.path.join(root, path.lstrip("/")))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return self.send(404, b"Not found", "text/plain")
        st = os.stat(full)
        etag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        if self.header("If-None-Match") == etag:
            return self.send(304, cache="no-cache", extra=[("ETag", etag)])
        with open(full, "rb") as f:
            body = f.read()
        ctype = TYPES.get(os.path.splitext(full)[1]) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        # no-cache = revalidate every time, so a new version reaches browsers on the next load
        return self.send(200, body, ctype, cache="no-cache", extra=[
            ("ETag", etag), ("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))])

    # ------------------------------------------------------------ routing
    def dispatch(self):
        raw = self.env.get("PATH_INFO") or "/"
        if ".." in raw.split("/"):
            return self.send(404, b"Not found", "text/plain")
        path = posixpath.normpath(raw)

        if path.startswith("/api/"):
            if self.method == "POST":
                return self.post(path)
            if self.method not in ("GET", "HEAD"):
                return self.json(405, {"error": "Method not allowed"})
            status, payload, extra = api.handle(self.app, "GET", path, {}, self.ctx())
            if path == "/api/export" and status == 200:
                extra = extra + [("Content-Disposition", 'attachment; filename="leave-planner-export.json"')]
            return self.json(status, payload, extra)

        if self.method not in ("GET", "HEAD"):
            return self.send(405, b"Method not allowed", "text/plain")
        if path == "/healthz":
            return self.send(200, b"ok", "text/plain")
        if path in ("/", "/index.html"):
            return self.static("/index.html") if self.user() else self.redirect("/login.html")
        if path == "/login.html" and self.user():
            return self.redirect("/")
        if path in PUBLIC_FILES:
            return self.static(path)
        if path.startswith(APP_PREFIXES) and path.endswith((".js", ".css")):
            return self.static(path) if self.user() else self.send(401, b"Sign in required", "text/plain")
        return self.send(404, b"Not found", "text/plain")

    def post(self, path):
        # CSRF: same-site cookie + a custom header a cross-site form can't send +
        # an Origin that matches this host.
        origin = self.header("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.host:
            return self.json(403, {"error": "Cross-site request refused"})
        if self.header("X-WFS-Request") != "1":
            return self.json(403, {"error": "Missing request header"})
        try:
            length = int(self.env.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self.json(413, {"error": "Request too large"})
        try:
            body = json.loads(self.env["wsgi.input"].read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.json(400, {"error": "Invalid JSON"})
        status, payload, extra = api.handle(self.app, "POST", path, body, self.ctx())
        return self.json(status, payload, extra)


# ---------------------------------------------------------------- Vercel entrypoint
_hosted_app = None


class StartupError(Exception):
    """Why the hosted app couldn't start, in words that are safe to show publicly."""

    def __init__(self, reason, hint):
        super().__init__(hint)
        self.reason, self.hint = reason, hint


def database_url_from_environment(env=None):
    """The hosted database URL. Vercel's Neon integration sets DATABASE_URL or
    POSTGRES_URL, or prefixed versions (e.g. STORAGE_DATABASE_URL) when a prefix
    was chosen while connecting. Unpooled/direct variants are skipped in favour
    of the pooled connection, which suits many short-lived function instances."""
    env = os.environ if env is None else env
    for key in ("DATABASE_URL", "POSTGRES_URL"):
        if env.get(key):
            return env[key]
    for key in sorted(env):
        if (key.endswith(("_DATABASE_URL", "_POSTGRES_URL")) and env[key]
                and not any(x in key for x in ("UNPOOLED", "NON_POOLING", "NO_SSL"))):
            return env[key]
    return None


def from_environment(env=None):
    env = os.environ if env is None else env
    url = database_url_from_environment(env)
    hosted = bool(env.get("VERCEL"))
    if hosted and not url:
        raise StartupError("database-not-configured",
                           "No database connection found. Connect the Neon database to this "
                           "Vercel project, then redeploy.")
    if url and dbm.is_postgres(url):
        try:
            import psycopg  # noqa: F401
        except ImportError:
            raise StartupError("database-driver-missing",
                               "The Postgres driver (psycopg) is not installed in this deployment.")
    try:
        return App(url or config.default_db_path(), config.SEED_PATH, secure=hosted, trust_proxy=hosted)
    except Exception as e:
        traceback.print_exc()                      # full detail goes to the host's logs only
        raise StartupError("database-unreachable",
                           "Could not connect to the database (%s)." % type(e).__name__)


def _startup_error(start_response, err):
    body = json.dumps({"error": "The site isn't ready yet", "reason": err.reason, "hint": err.hint}).encode("utf-8")
    start_response("503 Service Unavailable", [("Content-Type", "application/json"),
                                               ("Content-Length", str(len(body))),
                                               ("Cache-Control", "no-store")])
    return [body]


def application(environ, start_response):
    """Built on the first request, so importing this module never touches a
    database. A failed start is retried on the next request."""
    global _hosted_app
    if _hosted_app is None:
        try:
            _hosted_app = from_environment()
        except StartupError as err:
            print("Startup failed: %s - %s" % (err.reason, err.hint), file=sys.stderr)
            return _startup_error(start_response, err)
        except Exception:
            traceback.print_exc()
            return _server_error(start_response)
    return _hosted_app(environ, start_response)


app = application

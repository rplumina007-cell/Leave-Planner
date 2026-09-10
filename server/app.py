"""WFS Tracker server: login gateway, role-checked API, and the app's static files.

    python server/app.py                        # this computer only
    python server/app.py --host 0.0.0.0         # reachable by others on your network
    python server/app.py --host 0.0.0.0 --cert cert.pem --key key.pem   # with HTTPS

Standard library only - nothing to install.
"""
import argparse
import email.utils
import http.server
import json
import mimetypes
import os
import posixpath
import ssl
import sys
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
# Everything the signed-in app needs. Anything not matched here - data/, server/,
# tools/, dist/, README - is never served, signed in or not.
APP_PREFIXES = ("/src/js/", "/src/css/")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class App:
    def __init__(self, db_path, seed_path, secure):
        self.db = dbm.DB(db_path, seed_path)
        self.seed_path, self.secure = seed_path, secure
        self.limiter = security.LoginLimiter()

    def cookie(self, token, ctx, expire=False):
        c = "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (COOKIE, token)
        c += "; Max-Age=0" if expire else "; Max-Age=%d" % (security.SESSION_HOURS * 3600)
        return c + ("; Secure" if self.secure else "")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "WFS-Tracker"
    sys_version = ""
    app = None  # set in main()

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))

    def _token(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar[COOKIE].value if COOKIE in jar else None

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=(), cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        if self.app.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload, headers=()):
        self._send(status, json.dumps(payload).encode("utf-8"), headers=headers)

    def _redirect(self, where):
        self._send(302, headers=[("Location", where)], ctype="text/plain")

    def _ctx(self):
        token = self._token()
        return {"token": token, "user": self.app.db.session_user(token), "ip": self.client_address[0]}

    # ------------------------------------------------------------ static
    def _static(self, url_path):
        path = posixpath.normpath(urllib.parse.unquote(url_path))
        if ".." in path.split("/"):
            return self._send(404, b"Not found", "text/plain")
        full = os.path.realpath(os.path.join(config.PROJECT_ROOT, path.lstrip("/")))
        if not full.startswith(os.path.realpath(config.PROJECT_ROOT) + os.sep) or not os.path.isfile(full):
            return self._send(404, b"Not found", "text/plain")
        st = os.stat(full)
        etag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        if self.headers.get("If-None-Match") == etag:
            return self._send(304, cache="no-cache", headers=[("ETag", etag)])
        with open(full, "rb") as f:
            body = f.read()
        ctype = TYPES.get(os.path.splitext(full)[1]) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        # no-cache = always revalidate, so a code change reaches browsers on the next load
        self._send(200, body, ctype, headers=[("ETag", etag),
                                               ("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))],
                   cache="no-cache")

    # ------------------------------------------------------------ verbs
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path.startswith("/api/"):
            ctx = self._ctx()
            status, payload, headers = api.handle(self.app, "GET", path, {}, ctx)
            if path == "/api/export" and status == 200:
                headers = headers + [("Content-Disposition", 'attachment; filename="wfs-tracker-export.json"')]
            return self._json(status, payload, headers)
        user = self._ctx()["user"]
        if path in ("/", "/index.html"):
            return self._static("/index.html") if user else self._redirect("/login.html")
        if path == "/login.html" and user:
            return self._redirect("/")
        if path in PUBLIC_FILES:
            return self._static(path)
        if path.startswith(APP_PREFIXES) and path.endswith((".js", ".css")):
            return self._static(path) if user else self._send(401, b"Sign in required", "text/plain")
        return self._send(404, b"Not found", "text/plain")

    do_HEAD = do_GET

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not path.startswith("/api/"):
            return self._json(404, {"error": "Not found"})
        # CSRF: same-site cookie + a custom header a cross-site form can't set +
        # an Origin that matches this host.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self._json(403, {"error": "Cross-site request refused"})
        if self.headers.get("X-WFS-Request") != "1":
            return self._json(403, {"error": "Missing request header"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            return self._json(413, {"error": "Request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "Invalid JSON"})
        status, payload, headers = api.handle(self.app, "POST", path, body, self._ctx())
        self._json(status, payload, headers)


def main(argv=None):
    ap = argparse.ArgumentParser(description="WFS Tracker server")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only; 0.0.0.0 = your network")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--db", default=None, help="SQLite file (default: %%LOCALAPPDATA%%\\WFSTracker\\wfs.sqlite3)")
    ap.add_argument("--seed", default=config.SEED_PATH)
    ap.add_argument("--cert", help="TLS certificate (PEM) to serve HTTPS")
    ap.add_argument("--key", help="TLS private key (PEM)")
    args = ap.parse_args(argv)

    secure = bool(args.cert and args.key)
    db_path = args.db or config.default_db_path()
    Handler.app = App(db_path, args.seed, secure)
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    if secure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.cert, args.key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    scheme = "https" if secure else "http"
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print("WFS Tracker running at %s://%s:%d" % (scheme, shown, args.port))
    print("Database: %s" % db_path)
    if not os.path.exists(args.seed):
        print("No sheet data imported yet (%s missing). Load it with:" % os.path.relpath(args.seed, config.PROJECT_ROOT))
        print('  python tools/import-sheet.py "<Google Sheet link>"')
    if not Handler.app.db.users():
        print("\nNo logins exist yet. Create the first admin:\n"
              '  python server/manage.py create-admin --emp-id <ID> --name "<Full Name>"\n')
    if args.host not in ("127.0.0.1", "localhost") and not secure:
        print("WARNING: reachable from the network over plain HTTP - passwords travel unencrypted.\n"
              "         Use --cert/--key (HTTPS) before letting other people sign in.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

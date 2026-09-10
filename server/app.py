"""Run Leave Planner on your own computer.

    python server/app.py                        # this computer only
    python server/app.py --host 0.0.0.0 --cert cert.pem --key key.pem   # your network, over HTTPS

Uses a local SQLite database and the standard library only. The hosted version
on Vercel runs the very same application (server/wsgi.py) against Neon Postgres.
"""
import argparse
import os
import ssl
import sys
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import wsgi    # noqa: E402


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Handler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))


def serve(application, host="127.0.0.1", port=8777, tls=None):
    """Bind a threaded server for a WSGI app and return it; call serve_forever()."""
    httpd = ThreadingWSGIServer((host, port), _Handler)
    httpd.set_app(application)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(*tls)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description="Leave Planner - local server")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only; 0.0.0.0 = your network")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--db", default=None, help="SQLite file (default: LOCALAPPDATA/WFSTracker/wfs.sqlite3)")
    ap.add_argument("--seed", default=config.SEED_PATH)
    ap.add_argument("--cert", help="TLS certificate (PEM) to serve HTTPS")
    ap.add_argument("--key", help="TLS private key (PEM)")
    args = ap.parse_args(argv)

    secure = bool(args.cert and args.key)
    db_path = args.db or config.default_db_path()
    application = wsgi.App(db_path, args.seed, secure=secure)
    httpd = serve(application, args.host, args.port, (args.cert, args.key) if secure else None)

    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print("Leave Planner running at %s://%s:%d" % ("https" if secure else "http", shown, args.port))
    print("Database: %s" % db_path)
    if not os.path.exists(args.seed):
        print("No sheet data imported yet (%s missing). Load it with:" % os.path.relpath(args.seed, config.PROJECT_ROOT))
        print('  python tools/import-sheet.py "<Google Sheet link>"')
    if not application.db.users():
        print("")
        print("No logins exist yet. Create the first admin:")
        print('  python server/manage.py create-admin --emp-id <ID> --name "<Full Name>"')
        print("")
    if args.host not in ("127.0.0.1", "localhost") and not secure:
        print("WARNING: reachable from the network over plain HTTP - passwords travel unencrypted.")
        print("         Use --cert/--key (HTTPS) before letting other people sign in.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()

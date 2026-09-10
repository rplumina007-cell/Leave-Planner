#!/usr/bin/env python3
"""Copy the local database to the hosted one: every login (roles, managers,
password hashes) and the whole tracker - requests, approvals, dropdowns, audit.

    pip install "psycopg[binary]"
    python tools/migrate-to-postgres.py          # dry run: shows what would be copied
    python tools/migrate-to-postgres.py --yes    # copy it

The hosted connection string comes from DATABASE_URL, or from a git-ignored
.env.local file (`vercel env pull` writes one). It is never printed.
Passwords are copied as hashes, so everyone signs in with the same password as
before. Existing sessions are not copied: everyone signs in once on the live site.
"""
import argparse
import os
import sys
import urllib.parse
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import config     # noqa: E402
import db as dbm  # noqa: E402


def describe(users, state):
    roles = Counter(u["role"] for u in users)
    return "%d logins (%s), %d leave and %d WFH records" % (
        len(users), ", ".join("%d %s" % (n, r) for r, n in sorted(roles.items())) or "none",
        len(state.get("leave", [])), len(state.get("wfh", [])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy the local Leave Planner database to the hosted one")
    ap.add_argument("--sqlite", default=None, help="local database (default: LOCALAPPDATA/WFSTracker/wfs.sqlite3)")
    ap.add_argument("--yes", action="store_true", help="actually copy (without it, only a dry run)")
    ap.add_argument("--replace", action="store_true", help="overwrite a hosted database that already has logins")
    args = ap.parse_args(argv)

    src_path = args.sqlite or config.default_db_path()
    if not os.path.exists(src_path):
        sys.exit("Local database not found: %s" % src_path)
    url = config.hosted_database_url()
    if not url:
        sys.exit("No hosted database configured. Put DATABASE_URL=<Neon connection string> in .env.local "
                 "(git-ignored) or set it as an environment variable.")

    src = dbm.DB(src_path)
    users = list(src.users(include_hash=True).values())
    state = src.read_state()
    print("Local:  %s" % describe(users, state))

    host = urllib.parse.urlsplit(url).hostname or "(unknown host)"
    dst = dbm.DB(url)
    existing = dst.users()
    print("Hosted: %s  (%s)" % (describe(list(existing.values()), dst.read_state()), host))

    if existing and not args.replace:
        sys.exit("The hosted database already has logins. Re-run with --replace to overwrite it.")
    if not args.yes:
        print("Dry run - nothing copied. Re-run with --yes to copy.")
        return

    dst.restore(users, state)
    after_users, after_state = list(dst.users(include_hash=True).values()), dst.read_state()
    ok = (sorted((u["emp_id"], u["pw_hash"], u["role"], u["manager_emp_id"]) for u in after_users)
          == sorted((u["emp_id"], u["pw_hash"], u["role"], u["manager_emp_id"]) for u in users)
          and after_state == state)
    print("Copied: %s" % describe(after_users, after_state))
    print("Verified: hosted data matches local exactly." if ok else "WARNING: hosted data does not match local - check before going live.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

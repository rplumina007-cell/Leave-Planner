"""Admin command line - for the first admin login and for lock-out recovery.
Everything else (logins, roles, managers) is done in the app: Settings -> Users.

    python server/manage.py create-admin --emp-id <employee ID> --name "<Full Name>"
    python server/manage.py reset-password --emp-id <employee ID>
    python server/manage.py list-users

Add --live to act on the hosted (Neon) database instead of the local one. Its
connection string comes from DATABASE_URL or a git-ignored .env.local file.
"""
import argparse
import os
import secrets
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api        # noqa: E402
import config     # noqa: E402
import db as dbm  # noqa: E402
import security   # noqa: E402


def temp_password(emp_id, name):
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(14))
        if not security.password_problem(pw, emp_id, name):
            return pw


def main(argv=None):
    ap = argparse.ArgumentParser(description="Leave Planner admin tools")
    ap.add_argument("--db", default=None, help="local SQLite file (default: LOCALAPPDATA/WFSTracker/wfs.sqlite3)")
    ap.add_argument("--live", action="store_true", help="use the hosted database (DATABASE_URL / .env.local)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="create an admin login with a one-time password")
    c.add_argument("--emp-id", required=True)
    c.add_argument("--name", required=True)
    r = sub.add_parser("reset-password", help="issue a new one-time password (signs the user out everywhere)")
    r.add_argument("--emp-id", required=True)
    sub.add_parser("list-users")
    args = ap.parse_args(argv)

    if args.live:
        target = config.hosted_database_url()
        if not target:
            sys.exit("No hosted database configured: set DATABASE_URL or add it to .env.local")
        where = "hosted database"
    else:
        target = args.db or config.default_db_path()
        where = target
    db = dbm.DB(target, None if args.live else config.SEED_PATH)

    if args.cmd == "create-admin":
        emp, name = args.emp_id.strip(), " ".join(args.name.split())
        if not api.EMP_ID_RE.match(emp):
            sys.exit("Invalid employee ID")
        if db.user(emp):
            sys.exit("A login for %s already exists - use reset-password instead." % emp)
        pw = temp_password(emp, name)
        db.create_user(emp, name, "admin", pw, must_change=True)
        print("Admin login created for %s (%s)." % (name, emp))
        print("One-time password: %s" % pw)
        print("You will be asked to choose your own password at first sign-in.")
    elif args.cmd == "reset-password":
        u = db.user(args.emp_id)
        if not u:
            sys.exit("No login for %s" % args.emp_id)
        pw = temp_password(u["emp_id"], u["name"])
        db.update_user(u["emp_id"], password=pw, must_change=1, active=1)
        db.end_all_sessions(u["emp_id"])
        print("New one-time password for %s: %s" % (u["name"], pw))
    else:
        users = db.users()
        if not users:
            print("No logins yet.")
        for u in users.values():
            print("%-10s %-24s %-9s manager=%-10s %s%s" % (
                u["emp_id"], u["name"], "senior mgr" if u.get("senior") else u["role"], u["manager_emp_id"] or "-",
                "active" if u["active"] else "INACTIVE", "  (must change password)" if u["must_change"] else ""))
    print("Database: %s" % where)


if __name__ == "__main__":
    main()

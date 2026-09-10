"""Storage: accounts, sessions, login failures and the shared tracker state.

One class, two backends:
  SQLite    a local file - the default when running on your own computer
  Postgres  used when given a postgres:// URL (Neon, when hosted on Vercel)

The tracker state (requests, dropdowns, audit) is a single JSON document. Every
change locks that row, so simultaneous requests - even on different server
instances - can't overwrite each other.
"""
import contextlib
import json
import os
import sqlite3
import threading
import time

import domain
import security

USER_FIELDS = ("emp_id", "name", "role", "manager_emp_id", "must_change", "active", "created_at", "updated_at")
SCHEMA_LOCK_ID = 7300451   # arbitrary; serialises schema setup when several instances cold-start at once


def is_postgres(target):
    return isinstance(target, str) and target.startswith(("postgres://", "postgresql://"))


def schema(pg):
    real = "DOUBLE PRECISION" if pg else "REAL"   # Postgres REAL is too imprecise for epoch seconds
    return [
        """CREATE TABLE IF NOT EXISTS users (
             emp_id         TEXT PRIMARY KEY,
             name           TEXT NOT NULL,
             role           TEXT NOT NULL CHECK (role IN ('employee','manager','admin')),
             manager_emp_id TEXT REFERENCES users(emp_id) ON DELETE SET NULL,
             pw_hash        TEXT NOT NULL,
             must_change    INTEGER NOT NULL DEFAULT 1,
             active         INTEGER NOT NULL DEFAULT 1,
             created_at     TEXT NOT NULL,
             updated_at     TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS sessions (
             token_hash TEXT PRIMARY KEY,
             emp_id     TEXT NOT NULL REFERENCES users(emp_id) ON DELETE CASCADE,
             expires_at %s NOT NULL)""" % real,
        """CREATE TABLE IF NOT EXISTS state (
             id   INTEGER PRIMARY KEY CHECK (id = 1),
             json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS login_failures (
             k  TEXT NOT NULL,
             at %s NOT NULL)""" % real,
        "CREATE INDEX IF NOT EXISTS login_failures_k_at ON login_failures (k, at)",
    ]


def _user(row, include_hash):
    u = {f: row[f] for f in USER_FIELDS}
    u["must_change"], u["active"] = bool(u["must_change"]), bool(u["active"])
    if include_hash:
        u["pw_hash"] = row["pw_hash"]
    return u


class _Cursor:
    """Runs SQL written with ? placeholders on either backend."""

    def __init__(self, conn, pg):
        self.conn, self.pg = conn, pg

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("?", "%s") if self.pg else sql, params)

    def one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.execute(sql, params).fetchall()


class DB:
    def __init__(self, target, seed_path=None):
        self.target, self.seed_path = target, seed_path
        self.pg = is_postgres(target)
        self._local = threading.local()
        self._lock = threading.RLock()
        with self.tx() as c:
            if self.pg:
                c.execute("SELECT pg_advisory_xact_lock(?)", (SCHEMA_LOCK_ID,))
            for stmt in schema(self.pg):
                c.execute(stmt)
            if not c.one("SELECT 1 AS x FROM state WHERE id = 1"):
                if seed_path and os.path.exists(seed_path):
                    seed, h = domain.load_seed(seed_path)
                else:                      # no sheet imported: start empty
                    seed, h = domain.empty_seed(), None
                c.execute("INSERT INTO state (id, json) VALUES (1, ?)", (json.dumps(domain.fresh_state(seed, h)),))

    # ------------------------------------------------------------ connections
    def _connect(self):
        if self.pg:
            import psycopg                          # only needed for Postgres
            from psycopg.rows import dict_row
            # prepare_threshold=None: Neon's pooled endpoint can't keep server-side
            # prepared statements between transactions.
            return psycopg.connect(self.target, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
        c = sqlite3.connect(self.target, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    def _pg_conn(self):
        """One connection per thread, reused between requests, and replaced if
        the database closed it while idle (Neon suspends after a few minutes)."""
        c = getattr(self._local, "conn", None)
        if c is not None and not c.closed:
            if time.time() - getattr(self._local, "used", 0) < 30:
                return c
            try:
                c.execute("SELECT 1")
                c.rollback()
                return c
            except Exception:
                with contextlib.suppress(Exception):
                    c.close()
        self._local.conn = self._connect()
        return self._local.conn

    @contextlib.contextmanager
    def tx(self):
        """One transaction: committed on success, rolled back on any error."""
        conn = self._pg_conn() if self.pg else self._connect()
        try:
            yield _Cursor(conn, self.pg)
            conn.commit()
        except BaseException:
            with contextlib.suppress(Exception):
                conn.rollback()
            raise
        finally:
            if self.pg:
                self._local.used = time.time()
            else:
                conn.close()

    # ------------------------------------------------------------ tracker state
    def read_state(self):
        with self.tx() as c:
            return json.loads(c.one("SELECT json FROM state WHERE id = 1")["json"])

    def mutate(self, fn):
        """Load state, apply fn(state) -> result, save - all under a row lock."""
        with self._lock, self.tx() as c:
            row = c.one("SELECT json FROM state WHERE id = 1" + (" FOR UPDATE" if self.pg else ""))
            st = json.loads(row["json"])
            result = fn(st)
            c.execute("UPDATE state SET json = ? WHERE id = 1", (json.dumps(st),))
            return result

    # ------------------------------------------------------------ users
    def users(self, include_hash=False):
        with self.tx() as c:
            rows = c.all("SELECT * FROM users ORDER BY name")
        return {r["emp_id"]: _user(r, include_hash) for r in rows}

    def user(self, emp_id, include_hash=False):
        with self.tx() as c:
            r = c.one("SELECT * FROM users WHERE emp_id = ?", (emp_id,))
        return _user(r, include_hash) if r else None

    def create_user(self, emp_id, name, role, password, manager_emp_id=None, must_change=True):
        now = domain.now_iso()
        with self._lock, self.tx() as c:
            c.execute(
                "INSERT INTO users (emp_id, name, role, manager_emp_id, pw_hash, must_change, active, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (emp_id, name, role, manager_emp_id or None, security.hash_password(password), int(bool(must_change)), now, now))

    def update_user(self, emp_id, **fields):
        allowed = {"name", "role", "manager_emp_id", "must_change", "active"}
        sets = {k: (int(v) if isinstance(v, bool) else v) for k, v in fields.items() if k in allowed}
        if "password" in fields:
            sets["pw_hash"] = security.hash_password(fields["password"])
        if not sets:
            return
        sets["updated_at"] = domain.now_iso()
        cols = ", ".join("%s = ?" % k for k in sets)      # column names come from the allow-list, never input
        with self._lock, self.tx() as c:
            c.execute("UPDATE users SET %s WHERE emp_id = ?" % cols, list(sets.values()) + [emp_id])

    def delete_user(self, emp_id):
        with self._lock, self.tx() as c:
            c.execute("DELETE FROM users WHERE emp_id = ?", (emp_id,))

    def count_admins(self, exclude=None):
        with self.tx() as c:
            return c.one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND emp_id != ?",
                         (exclude or "",))["n"]

    # ------------------------------------------------------------ sessions
    def create_session(self, emp_id):
        token, now = security.new_token(), time.time()
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            c.execute("INSERT INTO sessions (token_hash, emp_id, expires_at) VALUES (?, ?, ?)",
                      (security.token_hash(token), emp_id, now + security.SESSION_HOURS * 3600))
        return token

    def session_user(self, token):
        if not token:
            return None
        with self.tx() as c:
            r = c.one("SELECT u.* FROM sessions s JOIN users u ON u.emp_id = s.emp_id"
                      " WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
                      (security.token_hash(token), time.time()))
        return _user(r, False) if r else None

    def end_session(self, token):
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (security.token_hash(token or ""),))

    def end_all_sessions(self, emp_id, except_token=None):
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE emp_id = ? AND token_hash != ?",
                      (emp_id, security.token_hash(except_token) if except_token else ""))

    # ------------------------------------------------------------ login throttling
    def failure_count(self, key, since):
        with self.tx() as c:
            return c.one("SELECT COUNT(*) AS n FROM login_failures WHERE k = ? AND at > ?", (key, since))["n"]

    def add_failures(self, keys, at, prune_before):
        with self.tx() as c:
            c.execute("DELETE FROM login_failures WHERE at < ?", (prune_before,))
            for k in keys:
                c.execute("INSERT INTO login_failures (k, at) VALUES (?, ?)", (k, at))

    def clear_failures(self, keys):
        with self.tx() as c:
            for k in keys:
                c.execute("DELETE FROM login_failures WHERE k = ?", (k,))

    # ------------------------------------------------------------ migration
    def restore(self, users, state):
        """Replace every account and the tracker state in one transaction.
        `users` are rows from users(include_hash=True). Used to move a local
        database to a hosted one; sessions and failure counts start fresh."""
        with self._lock, self.tx() as c:
            for table in ("sessions", "login_failures"):
                c.execute("DELETE FROM %s" % table)
            c.execute("UPDATE users SET manager_emp_id = NULL")
            c.execute("DELETE FROM users")
            for u in users:                     # managers are linked after everyone exists
                c.execute(
                    "INSERT INTO users (emp_id, name, role, manager_emp_id, pw_hash, must_change, active, created_at, updated_at)"
                    " VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                    (u["emp_id"], u["name"], u["role"], u["pw_hash"], int(bool(u["must_change"])),
                     int(bool(u["active"])), u["created_at"], u["updated_at"]))
            for u in users:
                if u.get("manager_emp_id"):
                    c.execute("UPDATE users SET manager_emp_id = ? WHERE emp_id = ?", (u["manager_emp_id"], u["emp_id"]))
            c.execute("UPDATE state SET json = ? WHERE id = 1", (json.dumps(state),))

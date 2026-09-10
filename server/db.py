"""SQLite storage: user accounts, sessions, and the shared tracker state.

The tracker state (requests, dropdowns, audit) is one JSON document mutated under
a process-wide lock - simple, and plenty for a team-sized app.
"""
import json
import os
import sqlite3
import threading
import time

import domain
import security

WRITE_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  emp_id         TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('employee','manager','admin')),
  manager_emp_id TEXT REFERENCES users(emp_id) ON DELETE SET NULL,
  pw_hash        TEXT NOT NULL,
  must_change    INTEGER NOT NULL DEFAULT 1,
  active         INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  emp_id     TEXT NOT NULL REFERENCES users(emp_id) ON DELETE CASCADE,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
  id   INTEGER PRIMARY KEY CHECK (id = 1),
  json TEXT NOT NULL
);
"""

USER_FIELDS = ("emp_id", "name", "role", "manager_emp_id", "must_change", "active", "created_at", "updated_at")


class DB:
    def __init__(self, path, seed_path):
        self.path, self.seed_path = path, seed_path
        with self.conn() as c:
            c.executescript(SCHEMA)
            if not c.execute("SELECT 1 FROM state WHERE id = 1").fetchone():
                if os.path.exists(seed_path):
                    seed, h = domain.load_seed(seed_path)
                else:              # no sheet imported yet: start empty
                    seed, h = domain.empty_seed(), None
                c.execute("INSERT INTO state (id, json) VALUES (1, ?)", (json.dumps(domain.fresh_state(seed, h)),))

    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    # ------------------------------------------------------------ state
    def read_state(self):
        with self.conn() as c:
            return json.loads(c.execute("SELECT json FROM state WHERE id = 1").fetchone()[0])

    def mutate(self, fn):
        """Load state, apply fn(state) -> result, save. Serialised across threads."""
        with WRITE_LOCK:
            st = self.read_state()
            result = fn(st)
            with self.conn() as c:
                c.execute("UPDATE state SET json = ? WHERE id = 1", (json.dumps(st),))
            return result

    # ------------------------------------------------------------ users
    def users(self, include_hash=False):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM users ORDER BY name").fetchall()
        return {r["emp_id"]: self._user(r, include_hash) for r in rows}

    def user(self, emp_id, include_hash=False):
        with self.conn() as c:
            r = c.execute("SELECT * FROM users WHERE emp_id = ?", (emp_id,)).fetchone()
        return self._user(r, include_hash) if r else None

    @staticmethod
    def _user(row, include_hash):
        u = {f: row[f] for f in USER_FIELDS}
        u["must_change"], u["active"] = bool(u["must_change"]), bool(u["active"])
        if include_hash:
            u["pw_hash"] = row["pw_hash"]
        return u

    def create_user(self, emp_id, name, role, password, manager_emp_id=None, must_change=True):
        now = domain.now_iso()
        with WRITE_LOCK, self.conn() as c:
            c.execute(
                "INSERT INTO users (emp_id, name, role, manager_emp_id, pw_hash, must_change, active, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (emp_id, name, role, manager_emp_id or None, security.hash_password(password), int(must_change), now, now))

    def update_user(self, emp_id, **fields):
        allowed = {"name", "role", "manager_emp_id", "must_change", "active"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "password" in fields:
            sets["pw_hash"] = security.hash_password(fields["password"])
        if not sets:
            return
        sets["updated_at"] = domain.now_iso()
        cols = ", ".join("%s = ?" % k for k in sets)
        with WRITE_LOCK, self.conn() as c:
            c.execute("UPDATE users SET %s WHERE emp_id = ?" % cols, list(sets.values()) + [emp_id])

    def delete_user(self, emp_id):
        with WRITE_LOCK, self.conn() as c:
            c.execute("DELETE FROM users WHERE emp_id = ?", (emp_id,))

    def count_admins(self, exclude=None):
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND emp_id != ?",
                             (exclude or "",)).fetchone()[0]

    # ------------------------------------------------------------ sessions
    def create_session(self, emp_id):
        token = security.new_token()
        with self.conn() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            c.execute("INSERT INTO sessions (token_hash, emp_id, expires_at) VALUES (?, ?, ?)",
                      (security.token_hash(token), emp_id, time.time() + security.SESSION_HOURS * 3600))
        return token

    def session_user(self, token):
        if not token:
            return None
        with self.conn() as c:
            r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.emp_id = s.emp_id"
                          " WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
                          (security.token_hash(token), time.time())).fetchone()
        return self._user(r, False) if r else None

    def end_session(self, token):
        with self.conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (security.token_hash(token or ""),))

    def end_all_sessions(self, emp_id, except_token=None):
        with self.conn() as c:
            c.execute("DELETE FROM sessions WHERE emp_id = ? AND token_hash != ?",
                      (emp_id, security.token_hash(except_token) if except_token else ""))

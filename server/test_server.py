"""End-to-end checks of the login gateway and every role rule.

    python server/test_server.py

Runs the real application (server/wsgi.py - the same code Vercel runs) on a
spare port against a throwaway SQLite database and generated sample data, then
drives it over HTTP exactly as a browser would. Needs no imported sheet data,
and your real database is never touched.

To run the same checks against Postgres, point TEST_DATABASE_URL at a THROWAWAY
database (e.g. a separate Neon branch) - its tables are dropped first.
"""
import datetime
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as runner  # noqa: E402
import db as dbm      # noqa: E402
import domain         # noqa: E402
import wsgi           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  -> %s" % detail))


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None, headers=None, raw=False):
        h = {"X-WFS-Request": "1", "Content-Type": "application/json"}
        h.update(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                self.cookie = v.split(";")[0] if "Max-Age=0" not in v else None
        c.close()
        if raw:
            return r.status, data, dict(r.getheaders())
        try:
            return r.status, json.loads(data or b"{}")
        except ValueError:
            return r.status, {"_text": data[:80]}

    def login(self, emp, pw):
        return self.req("POST", "/api/login", {"empId": emp, "password": pw})

    def ready(self, emp, temp, new):
        """Sign in with a one-time password and replace it."""
        self.login(emp, temp)
        return self.req("POST", "/api/me/password", {"current": temp, "next": new})


def synthetic_seed(path):
    """12 sample people with 120 leave and 40 WFH records (some pending), in the
    same shape tools/import-sheet.py writes."""
    people = [{"empId": "P%03d" % i, "name": "Sample Person %s" % chr(65 + i)} for i in range(12)]
    day = datetime.date(2026, 1, 5)
    out = {"leave": [], "wfh": []}
    for i in range(160):
        while day.weekday() > 4:
            day += datetime.timedelta(days=1)
        p = people[i % 12]
        kind = "leave" if i < 120 else "wfh"
        half = i % 5 == 0
        out[kind].append({
            "id": "%s%04d" % ("L" if kind == "leave" else "W", len(out[kind]) + 1),
            "empId": p["empId"], "name": p["name"], "from": day.isoformat(), "to": day.isoformat(),
            "duration": "Half Day - 1st Half" if half else "Full Day", "days": 0.5 if half else 1,
            "reason": ("Planned Leave" if i % 2 else "Unplanned") if kind == "leave" else "Personal Work",
            "status": "Pending" if i % 10 == 3 else "Approved", "comment": "", "autoLogged": False})
        day += datetime.timedelta(days=1)
    seed = dict(domain.empty_seed(), people=people, generatedAt="2026-09-01", **out)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f)
    return path


def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.sqlite3")
    seed_path = synthetic_seed(os.path.join(tmp, "seed.json"))
    pg_url = os.environ.get("TEST_DATABASE_URL")
    target = pg_url or db_path
    if pg_url:
        print("Using Postgres from TEST_DATABASE_URL (tables will be dropped)")
        with dbm.DB(pg_url).tx() as c:
            c.execute("DROP TABLE IF EXISTS login_failures, sessions, state, users")
    application = wsgi.App(target, seed_path)
    srv = runner.serve(application, "127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    db = application.db
    db.create_user("A1", "Ada Admin", "admin", "TempAdmin#2026", must_change=True)

    print("\n-- gateway & static files")
    anon = Client(port)
    s, _, h = anon.req("GET", "/", raw=True)
    check("signed-out / redirects to login", s == 302 and h.get("Location") == "/login.html", s)
    check("login page is public", anon.req("GET", "/login.html", raw=True)[0] == 200)
    for p, ctype in (("/favicon.svg", "image/svg+xml"), ("/favicon.ico", "image/x-icon"), ("/apple-touch-icon.png", "image/png")):
        s, _, h = anon.req("GET", p, raw=True)
        check("icon is public: " + p, s == 200 and h.get("Content-Type", "").startswith(ctype), (s, h.get("Content-Type")))
    check("app script needs sign-in", anon.req("GET", "/src/js/app.js", raw=True)[0] == 401)
    for p in ("/data/seed.json", "/data/seed.js", "/server/app.py", "/README.md", "/src/../data/seed.json", "/%2e%2e/server/db.py"):
        check("never served: " + p, anon.req("GET", p, raw=True)[0] == 404)
    s, _, h = anon.req("GET", "/login.html", raw=True)
    check("security headers set", "default-src 'self'" in h.get("Content-Security-Policy", "") and h.get("X-Frame-Options") == "DENY")
    check("health check answers", anon.req("GET", "/healthz", raw=True)[0] == 200)
    check("API refuses anonymous", anon.req("GET", "/api/state")[0] == 401)
    check("POST without custom header refused", anon.req("POST", "/api/login", {"empId": "A1", "password": "x"}, {"X-WFS-Request": ""})[0] == 403)
    check("cross-site Origin refused", anon.req("POST", "/api/login", {"empId": "A1", "password": "x"}, {"Origin": "http://evil.example"})[0] == 403)
    s, b = anon.login("A1", "wrong-password")
    check("wrong password: generic message", s == 401 and b["error"] == "Invalid employee ID or password", b)
    s, b = anon.login("NOPE", "wrong-password")
    check("unknown ID: same generic message", s == 401 and b["error"] == "Invalid employee ID or password", b)

    print("\n-- admin first sign-in")
    admin = Client(port)
    s, b = admin.login("A1", "TempAdmin#2026")
    check("admin signs in with one-time password", s == 200 and b["user"]["mustChange"], b)
    s, b = admin.req("GET", "/api/state")
    check("app blocked until password changed", s == 403 and b.get("mustChange"), b)
    check("weak new password rejected", admin.req("POST", "/api/me/password", {"current": "TempAdmin#2026", "next": "short"})[0] == 400)
    check("password containing name rejected", admin.req("POST", "/api/me/password", {"current": "TempAdmin#2026", "next": "adapassword99"})[0] == 400)
    s, b = admin.req("POST", "/api/me/password", {"current": "TempAdmin#2026", "next": "Blue-Harbor-71"})
    check("admin sets own password", s == 200, b)
    s, st = admin.req("GET", "/api/state")
    check("admin sees full state + audit + team", s == 200 and "audit" in st and "team" in st and len(st["leave"]) > 100, s)
    check("admin sees people without logins", len(st.get("unregistered", [])) >= 12, len(st.get("unregistered", [])))
    check("admin can file for everyone listed", len(st["people"]) >= 13 and all(p["full"] and p["canApply"] for p in st["people"]))
    s, b = admin.req("POST", "/api/requests", {"kind": "wfh", "forEmpId": "P001", "from": "2026-10-19", "to": "2026-10-19",
                                               "duration": "Full Day", "reason": "Personal Work"})
    filed = [r for r in b.get("state", {}).get("wfh", []) if r["id"] == b.get("id")]
    check("admin files WFH for someone with no login", s == 200 and filed and filed[0]["name"] == "Sample Person B", b)
    check("admin can't file for an unknown ID", admin.req("POST", "/api/requests", {"kind": "wfh", "forEmpId": "ZZZ", "from": "2026-10-19",
                                                                                   "duration": "Full Day", "reason": "Personal Work"})[0] == 400)

    print("\n-- admin creates teams, logins and managers")
    new_team = lambda name: admin.req("POST", "/api/teams/add", {"name": name})
    for t in ("Alpha", "Beta", "Gamma"):
        check("create team " + t, new_team(t)[0] == 200)
    check("duplicate team name refused (any case)", new_team("alpha")[0] == 400)
    check("All Teams is built in and can't be created", new_team("all teams")[0] == 400)
    mk = lambda emp, name, role, mgr=None, teams=None: admin.req("POST", "/api/users", {
        "empId": emp, "name": name, "role": role, "password": "Start-Here-2026", "managerEmpId": mgr, "teams": teams or []})
    check("create manager M1 in Alpha + Beta", mk("M1", "Mona Manager", "manager", None, ["Alpha", "Beta"])[0] == 200)
    check("create manager M2 reporting to M1, in Beta", mk("M2", "Max Manager", "manager", "M1", ["Beta"])[0] == 200)
    check("create employee E1 under M1, in Alpha", mk("E1", "Eve Employee", "employee", "M1", ["Alpha"])[0] == 200)
    check("create employee E2 under M2, in Beta", mk("E2", "Eli Employee", "employee", "M2", ["Beta"])[0] == 200)
    check("create employee E5 under M1 but in Gamma", mk("E5", "Gia Gamma", "employee", "M1", ["Gamma"])[0] == 200)
    check("create employee G1 in All Teams", mk("G1", "Gus Global", "employee", None, ["All Teams", "Alpha"])[0] == 200)
    check("unknown team refused", mk("E6", "Uma Unknown", "employee", None, ["Nope"])[0] == 400)
    check("duplicate employee ID refused", mk("E1", "Someone Else", "employee")[0] == 409)
    check("employee can't be assigned as a manager", mk("E3", "Ezra Employee", "employee", "E1")[0] == 400)
    check("create needs a valid password", admin.req("POST", "/api/users", {"empId": "E4", "name": "Emma Employee", "role": "employee", "password": "123"})[0] == 400)
    _, ast = admin.req("GET", "/api/state")
    check("admin sees the team list", ast.get("teams") == ["Alpha", "Beta", "Gamma"], ast.get("teams"))
    check("All Teams is stored on its own", [u["teams"] for u in ast["team"] if u["empId"] == "G1"] == [["All Teams"]])

    e1, e2, m1, m2, e5, g1 = (Client(port) for _ in range(6))
    for c, emp, pw in ((e1, "E1", "Harbor-Lamp-11"), (e2, "E2", "Copper-Gate-22"), (m1, "M1", "Silver-Pine-33"),
                       (m2, "M2", "Amber-Rail-44"), (e5, "E5", "Maple-Stone-55"), (g1, "G1", "Cedar-Wind-66")):
        check(emp + " replaces one-time password", c.ready(emp, "Start-Here-2026", pw)[0] == 200)

    print("\n-- employee (E1, in Alpha)")
    s, st = e1.req("GET", "/api/state")
    seen = sorted(p["empId"] for p in st["people"])
    check("employee sees only their team (M1 in Alpha, G1 in All Teams)", s == 200 and seen == ["E1", "G1", "M1"], seen)
    check("employee gets no records for anyone outside their teams",
          not [r for k in ("leave", "wfh") for r in st[k] if r["empId"] not in seen])
    check("no audit, team or manager data for employee", "audit" not in st and "team" not in st and "teams" not in st)
    check("employee can only file for themselves", [p["empId"] for p in st["people"] if p["canApply"]] == ["E1"])
    req = {"kind": "leave", "from": "2026-10-12", "to": "2026-10-13", "duration": "Full Day", "reason": "Planned Leave"}
    s, b = e1.req("POST", "/api/requests", req)
    check("employee submits own leave", s == 200, b)
    e1_id = b.get("id")
    mine = [r for r in b["state"]["leave"] if r["id"] == e1_id]
    check("own request shows full detail, days computed server-side", mine and mine[0]["reason"] == "Planned Leave" and mine[0]["days"] == 2, mine)
    check("employee can't file for someone else", e1.req("POST", "/api/requests", dict(req, forEmpId="E2"))[0] == 403)
    check("server rejects a reason not in the list", e1.req("POST", "/api/requests", dict(req, reason="Beach day"))[0] == 400)
    check("server rejects a weekend-only range", e1.req("POST", "/api/requests", dict(req, **{"from": "2026-10-10", "to": "2026-10-11"}))[0] == 400)
    for path, body in (("/api/requests/decide", {"kind": "leave", "id": e1_id, "status": "Approved"}),
                       ("/api/users", {"empId": "X", "name": "Xx Yy", "role": "admin", "password": "Whatever-99"}),
                       ("/api/options/add", {"key": "wfhReasons", "value": "Hack"}),
                       ("/api/purge", {"amount": 1, "unit": "days", "leave": True, "wfh": True}),
                       ("/api/sync", {})):
        check("employee blocked: " + path, e1.req("POST", path, body)[0] == 403)
    check("employee blocked: export", e1.req("GET", "/api/export")[0] == 403)
    legacy = [r for r in ast["leave"] if r["empId"].startswith("P")][0]
    check("employee can't delete a record outside their team", e1.req("POST", "/api/requests/delete", {"kind": "leave", "id": legacy["id"]})[0] == 404)
    s, b = e1.req("POST", "/api/requests", dict(req, **{"from": "2026-10-20", "to": "2026-10-20"}))
    check("employee withdraws own pending request", e1.req("POST", "/api/requests/delete", {"kind": "leave", "id": b["id"]})[0] == 200)

    print("\n-- managers: teams decide who they see, the reporting chain who they act for")
    print("   M1: Alpha+Beta | M2 -> M1, Beta | E1 -> M1, Alpha | E2 -> M2, Beta | E5 -> M1, Gamma | G1: All Teams")
    s, b = m2.req("POST", "/api/requests", dict(req, **{"from": "2026-10-14", "to": "2026-10-14"}))
    m2_id = b["id"]
    s, b = e2.req("POST", "/api/requests", dict(req, **{"from": "2026-10-15", "to": "2026-10-15"}))
    e2_id = b["id"]
    s, b = e5.req("POST", "/api/requests", dict(req, **{"from": "2026-10-16", "to": "2026-10-16"}))
    e5_id = b["id"]
    decide = lambda c, rid, st="Approved": c.req("POST", "/api/requests/decide", {"kind": "leave", "id": rid, "status": st})
    check("manager can't approve own request", decide(m2, m2_id)[0] == 403)
    check("M1 approves M2 (below M1, shares Beta)", decide(m1, m2_id)[0] == 200)
    s, b = decide(m2, e1_id)
    check("M2 can't decide E1 (not in M2's teams) or learn whose it is", s == 404 and "Eve" not in json.dumps(b), (s, b))
    check("M2 approves E2", decide(m2, e2_id)[0] == 200)
    check("M1 can decide E2 too (E2 -> M2 -> M1, shares Beta)", decide(m1, e2_id)[0] == 200)
    s, b = decide(m1, e5_id)
    check("M1 can't see or decide E5: reports to M1 but in another team", s == 404 and "Gia" not in json.dumps(b), (s, b))
    check("M1 declines E1 with note", m1.req("POST", "/api/requests/decide", {"kind": "leave", "id": e1_id, "status": "Declined", "note": "Clash"})[0] == 200)
    s, st = m1.req("GET", "/api/state")
    seen = sorted(p["empId"] for p in st["people"])
    owners = {r["empId"] for k in ("leave", "wfh") for r in st[k]}
    check("manager sees everyone sharing a team, incl. G1 (All Teams)", s == 200 and seen == ["E1", "E2", "G1", "M1", "M2"], seen)
    check("manager gets no records for anyone outside their teams (E5, sheet-only people)", owners <= set(seen), owners)
    check("manager sees their teams in full", owners and not any(r.get("redacted") for k in ("leave", "wfh") for r in st[k]))
    check("manager's team list matches who they can see", sorted(u["empId"] for u in st.get("team", [])) == seen, st.get("team"))
    check("manager acts only for people below them AND in their teams",
          sorted(p["empId"] for p in st["people"] if p["canApply"]) == ["E1", "E2", "M1", "M2"])
    _, st2 = m2.req("GET", "/api/state")
    check("junior manager sees their own team (Beta: M1, E2) and G1", sorted(p["empId"] for p in st2["people"]) == ["E2", "G1", "M1", "M2"],
          [p["empId"] for p in st2["people"]])
    check("junior manager acts only for E2", sorted(p["empId"] for p in st2["people"] if p["canApply"]) == ["E2", "M2"])
    s, b = m1.req("POST", "/api/requests", dict(req, forEmpId="E1", **{"from": "2026-10-21", "to": "2026-10-21"}))
    check("manager files leave for someone they manage", s == 200, b)
    check("manager deletes a record of someone they manage", m1.req("POST", "/api/requests/delete", {"kind": "leave", "id": b.get("id")})[0] == 200)
    check("senior manager files for someone in a sub-team (E2)", m1.req("POST", "/api/requests", dict(req, forEmpId="E2", **{"from": "2026-10-22", "to": "2026-10-22"}))[0] == 200)
    check("manager can't file for a report in another team (E5)", m1.req("POST", "/api/requests", dict(req, forEmpId="E5"))[0] == 403)
    check("manager can't file for a teammate who isn't below them (G1)", m1.req("POST", "/api/requests", dict(req, forEmpId="G1"))[0] == 403)
    check("junior manager can't file for someone outside their chain (E1)", m2.req("POST", "/api/requests", dict(req, forEmpId="E1"))[0] == 403)
    check("manager can't file for someone with no login", m1.req("POST", "/api/requests", dict(req, forEmpId="P001"))[0] == 403)
    _, ast = admin.req("GET", "/api/state")
    pend = [r for r in ast["leave"] if r["status"] == "Pending" and r["empId"].startswith("P")]
    check("manager can't decide someone outside their teams", pend and decide(m1, pend[0]["id"])[0] == 404)
    check("manager can't delete records outside their teams", m1.req("POST", "/api/requests/delete", {"kind": "leave", "id": pend[0]["id"]})[0] == 404)
    check("manager can't manage users", m1.req("POST", "/api/users/update", {"empId": "E1", "role": "admin"})[0] == 403)
    check("manager can't create teams", m1.req("POST", "/api/teams/add", {"name": "Delta"})[0] == 403)

    print("\n-- All Teams (G1, an employee)")
    s, stg = g1.req("GET", "/api/state")
    g_seen = {p["empId"] for p in stg["people"]}
    check("All Teams sees everyone, sheet-only people included", {"E1", "E2", "E5", "M1", "M2", "P001"} <= g_seen, sorted(g_seen))
    others = [r for k in ("leave", "wfh") for r in stg[k] if r["empId"] != "G1"]
    check("...but as an employee still without teammates' reasons", others and all(r.get("redacted") and r["reason"] is None for r in others))
    check("admin decides anyone's request", decide(admin, pend[0]["id"])[0] == 200)
    check("admin can revoke a decision", decide(admin, m2_id, "Declined")[0] == 200)

    print("\n-- admin controls")
    s, b = admin.req("POST", "/api/users/update", {"empId": "M2", "role": "employee"})
    check("demoting a manager unassigns their reports", s == 200 and b.get("unassigned") == 1, b)
    check("demoted user signed out", m2.req("GET", "/api/state")[0] == 401)
    check("reassign E2 to M1", admin.req("POST", "/api/users/update", {"empId": "E2", "managerEmpId": "M1"})[0] == 200)
    check("can't be own manager", admin.req("POST", "/api/users/update", {"empId": "M1", "managerEmpId": "M1"})[0] == 400)
    check("deactivate E1", admin.req("POST", "/api/users/update", {"empId": "E1", "active": False})[0] == 200)
    check("deactivated user signed out", e1.req("GET", "/api/state")[0] == 401)
    check("deactivated user can't sign in", Client(port).login("E1", "Harbor-Lamp-11")[0] == 401)
    check("admin can't deactivate self", admin.req("POST", "/api/users/update", {"empId": "A1", "active": False})[0] == 400)
    check("admin can't delete self", admin.req("POST", "/api/users/delete", {"empId": "A1"})[0] == 400)
    check("last admin can't be demoted", admin.req("POST", "/api/users/update", {"empId": "A1", "role": "manager"})[0] == 400)
    check("admin resets E2's password", admin.req("POST", "/api/users/reset-password", {"empId": "E2", "password": "Reset-Temp-2026"})[0] == 200)
    check("reset signs E2 out", e2.req("GET", "/api/state")[0] == 401)
    s, b = Client(port).login("E2", "Reset-Temp-2026")
    check("E2 must change password after reset", s == 200 and b["user"]["mustChange"])
    check("admin adds a dropdown value", admin.req("POST", "/api/options/add", {"key": "wfhReasons", "value": "Client Visit"})[0] == 200)
    check("duration shape enforced server-side", admin.req("POST", "/api/options/add", {"key": "durations", "value": "Quarter Day"})[0] == 400)
    s, b = admin.req("POST", "/api/purge", {"amount": 1, "unit": "days", "leave": False, "wfh": False})
    check("purge runs (nothing selected -> nothing removed)", s == 200 and b["removed"] == {"leave": 0, "wfh": 0}, b)
    s, b = admin.req("POST", "/api/sync", {})
    check("sheet sync keeps app-submitted requests", s == 200 and b["summary"]["keptLocal"] >= 3, b.get("summary"))
    check("admin export works", admin.req("GET", "/api/export")[0] == 200)
    s, b = admin.req("POST", "/api/teams/remove", {"name": "Gamma"})
    check("removing a team takes its members out of it",
          s == 200 and b.get("removed") == 1 and [u["teams"] for u in b["state"]["team"] if u["empId"] == "E5"] == [[]], b.get("removed"))
    check("removing an unknown team refused", admin.req("POST", "/api/teams/remove", {"name": "Gamma"})[0] == 400)
    check("someone with no team sees only themselves", sorted(p["empId"] for p in e5.req("GET", "/api/state")[1]["people"]) == ["E5"])
    check("admin changes someone's teams", admin.req("POST", "/api/users/update", {"empId": "E5", "teams": ["Alpha"]})[0] == 200)
    check("new team takes effect straight away", "E1" in {p["empId"] for p in e5.req("GET", "/api/state")[1]["people"]})
    s, b = admin.req("POST", "/api/logout", {})
    check("logout ends the session", s == 200 and admin.req("GET", "/api/state")[0] == 401)

    print("\n-- reporting chain")
    loop = {"X": {"emp_id": "X", "name": "Xan Loop", "role": "manager", "manager_emp_id": "Y", "active": 1},
            "Y": {"emp_id": "Y", "name": "Yul Loop", "role": "manager", "manager_emp_id": "X", "active": 1}}
    check("a loop in manager assignments doesn't hang", domain.reporting_chain(loop["X"], loop) == {"Y"})

    print("\n-- storage & brute force")
    if not pg_url:
        raw = open(db_path, "rb").read()
        check("no plaintext passwords in the database", not any(p in raw for p in (b"Blue-Harbor-71", b"Harbor-Lamp-11", b"Start-Here-2026")))
    check("passwords stored as scrypt hashes", all(u["pw_hash"].startswith("scrypt$") for u in db.users(include_hash=True).values()))
    atk = Client(port)
    for _ in range(5):
        atk.login("M1", "guess-guess")
    check("6th attempt locked out even with right password", atk.login("M1", "Silver-Pine-33")[0] == 429)

    print()
    print("-- hosted mode (behind a proxy, several instances sharing one database)")
    # Two more instances on the same database, trusting forwarding headers the
    # way they do on Vercel - so each check gets its own client address.
    hosted = [runner.serve(wsgi.App(target, seed_path, trust_proxy=True), "127.0.0.1", 0) for _ in range(2)]
    for h_srv in hosted:
        threading.Thread(target=h_srv.serve_forever, daemon=True).start()
    one, two = Client(hosted[0].server_address[1]), Client(hosted[1].server_address[1])
    far = {"X-Forwarded-For": "203.0.113.9"}
    for _ in range(5):
        one.req("POST", "/api/login", {"empId": "M2", "password": "guess-guess"}, far)
    s, _ = two.req("POST", "/api/login", {"empId": "M2", "password": "Amber-Rail-44"}, far)
    check("lockout shared across server instances", s == 429, s)
    s, _, h = one.req("POST", "/api/login", {"empId": "A1", "password": "Blue-Harbor-71"},
                      {"X-Forwarded-For": "198.51.100.4", "X-Forwarded-Proto": "https"}, raw=True)
    check("session cookie marked Secure behind HTTPS proxy", s == 200 and "Secure" in h.get("Set-Cookie", ""), (s, h.get("Set-Cookie")))
    for h_srv in hosted:
        h_srv.shutdown()

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

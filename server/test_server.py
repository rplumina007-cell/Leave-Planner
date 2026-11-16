"""End-to-end checks of the login gateway and every role rule.

    python server/test_server.py

Runs the real application (server/wsgi.py - the same code Vercel runs) on a
spare port against a throwaway SQLite database and generated sample data, then
drives it over HTTP exactly as a browser would. Needs no imported sheet data,
and your real database is never touched.

To run the same checks against Postgres, point TEST_DATABASE_URL at a THROWAWAY
database (e.g. a separate Neon branch) - its tables are dropped first.
"""
import base64
import datetime
import http.client
import io
import json
import zipfile
from xml.sax.saxutils import escape as xml_escape
import os
import re
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as runner  # noqa: E402
import bulk_users     # noqa: E402
import db as dbm      # noqa: E402
import domain         # noqa: E402
import mailer         # noqa: E402
import security       # noqa: E402
import wsgi           # noqa: E402


class FakeMail:
    """Stands in for the SMTP sender: keeps every message so tests can read the codes."""
    configured = available = True
    console = False

    def __init__(self):
        self.sent = []

    def describe(self):
        return "Test mail"

    def send(self, to, subject, text, html=None):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})

    def to(self, address):
        return [m for m in self.sent if m["to"] == address]

    def last_code(self, address):
        mine = self.to(address)
        found = re.search(r"\b(\d{6})\b", mine[-1]["text"]) if mine else None
        return found.group(1) if found else None

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


def xlsx_bytes(rows, doctype=False):
    """A minimal .xlsx like Excel writes: shared strings for text, plain numbers
    for digits, and no workbook relationships (so the reader's fallback is used)."""
    strings, xml_rows = [], []
    for r_i, row in enumerate(rows, 1):
        cells = []
        for c_i, v in enumerate(row):
            ref = "ABCDEF"[c_i] + str(r_i)
            if v == "":
                continue
            if v.isdigit():
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, v))
            else:
                strings.append(v)
                cells.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, len(strings) - 1))
        xml_rows.append('<row r="%d">%s</row>' % (r_i, "".join(cells)))
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (ns, "".join(xml_rows))
    if doctype:
        sheet = '<!DOCTYPE x [<!ENTITY a "b">]>' + sheet
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<workbook xmlns="%s"><sheets><sheet name="Users" sheetId="1"/></sheets></workbook>' % ns)
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="%s">%s</sst>' % (ns, "".join("<si><t>%s</t></si>" % xml_escape(s) for s in strings)))
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.sqlite3")
    seed_path = synthetic_seed(os.path.join(tmp, "seed.json"))
    pg_url = os.environ.get("TEST_DATABASE_URL")
    target = pg_url or db_path
    if pg_url:
        print("Using Postgres from TEST_DATABASE_URL (tables will be dropped)")
        with dbm.DB(pg_url).tx() as c:
            c.execute("DROP TABLE IF EXISTS password_resets, login_failures, sessions, state, users")
    mail = FakeMail()
    application = wsgi.App(target, seed_path, mail=mail)
    application.reset_pad = 0          # the anti-timing pause is checked on its own below
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
    check("create employee G1 in All Teams", mk("G1", "Gus Global", "employee", None, ["All Teams"])[0] == 200)
    check("unknown team refused", mk("E6", "Uma Unknown", "employee", None, ["Nope"])[0] == 400)
    check("duplicate employee ID refused", mk("E1", "Someone Else", "employee")[0] == 409)
    check("employee can't be assigned as a manager", mk("E3", "Ezra Employee", "employee", "E1")[0] == 400)
    check("create needs a valid password", admin.req("POST", "/api/users", {"empId": "E4", "name": "Emma Employee", "role": "employee", "password": "123"})[0] == 400)
    for weak, why in (("Plan-7x92", "under 10 characters"), ("Password2026!", "a common password"),
                      ("Welcome@12345", "a common password"), ("1234567890", "a run of digits")):
        check("password refused: " + why, admin.req("POST", "/api/users", {
            "empId": "E7", "name": "Ivy Wren", "role": "employee", "password": weak})[0] == 400)
    _, ast = admin.req("GET", "/api/state")
    check("admin sees the team list", ast.get("teams") == ["Alpha", "Beta", "Gamma"], ast.get("teams"))
    check("All Teams can be held on its own", [u["teams"] for u in ast["team"] if u["empId"] == "G1"] == [["All Teams"]])

    e1, e2, m1, m2, e5, g1 = (Client(port) for _ in range(6))
    for c, emp, pw in ((e1, "E1", "Harbor-Lamp-11"), (e2, "E2", "Copper-Gate-22"), (m1, "M1", "Silver-Pine-33"),
                       (m2, "M2", "Amber-Rail-44"), (e5, "E5", "Maple-Stone-55"), (g1, "G1", "Cedar-Wind-66")):
        check(emp + " replaces one-time password", c.ready(emp, "Start-Here-2026", pw)[0] == 200)

    print("\n-- employee (E1, in Alpha)")
    s, st = e1.req("GET", "/api/state")
    seen = sorted(p["empId"] for p in st["people"])
    check("employee sees fellow employees in their named teams only (not manager M1, not G1 who is only in All Teams)",
          s == 200 and seen == ["E1"], seen)
    check("employee gets no records of their manager or any admin",
          not [r for k in ("leave", "wfh") for r in st[k] if r["empId"] in ("M1", "M2", "A1")])
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
    s, b = decide(m1, m2_id, "Pending")
    undone = [r for r in b.get("state", {}).get("leave", []) if r["id"] == m2_id]
    check("a decision can be undone (back to pending)",
          s == 200 and undone and undone[0]["status"] == "Pending" and "decidedBy" not in undone[0], undone)
    check("undoing a request that is already pending is refused", decide(m1, m2_id, "Pending")[0] == 400)
    check("employee can't undo decisions", e1.req("POST", "/api/requests/decide", {"kind": "leave", "id": m2_id, "status": "Pending"})[0] == 403)
    check("re-approve after undo", decide(m1, m2_id)[0] == 200)
    s, b = decide(m2, e1_id)
    check("M2 can't decide E1 (not in M2's teams) or learn whose it is", s == 404 and "Eve" not in json.dumps(b), (s, b))
    check("M2 approves E2", decide(m2, e2_id)[0] == 200)
    check("M1 can decide E2 too (E2 -> M2 -> M1, shares Beta)", decide(m1, e2_id)[0] == 200)
    check("employee deletes their own approved request", e2.req("POST", "/api/requests/delete", {"kind": "leave", "id": e2_id})[0] == 200)
    check("the deletion is logged as a cancellation",
          any(a["action"] == "cancelled (was approved)" and a["id"] == e2_id for a in admin.req("GET", "/api/state")[1]["audit"]))
    s, b = decide(m1, e5_id)
    check("M1 can't see or decide E5: reports to M1 but in another team", s == 404 and "Gia" not in json.dumps(b), (s, b))
    check("M1 declines E1 with note", m1.req("POST", "/api/requests/decide", {"kind": "leave", "id": e1_id, "status": "Declined", "note": "Clash"})[0] == 200)
    s, b = decide(m1, e1_id, "Pending")
    back = [r for r in b.get("state", {}).get("leave", []) if r["id"] == e1_id]
    check("undoing a decline restores the requester's own comment", s == 200 and back and back[0]["comment"] == "", back)
    m1.req("POST", "/api/requests/decide", {"kind": "leave", "id": e1_id, "status": "Declined", "note": "Clash"})
    check("a declined request can't be deleted by its owner", e1.req("POST", "/api/requests/delete", {"kind": "leave", "id": e1_id})[0] == 403)
    s, st = m1.req("GET", "/api/state")
    seen = sorted(p["empId"] for p in st["people"])
    owners = {r["empId"] for k in ("leave", "wfh") for r in st[k]}
    check("manager sees everyone sharing a named team (not G1, whose only team is All Teams)", s == 200 and seen == ["E1", "E2", "M1", "M2"], seen)
    check("manager gets no records for anyone outside their teams (E5, sheet-only people)", owners <= set(seen), owners)
    check("manager sees their teams in full", owners and not any(r.get("redacted") for k in ("leave", "wfh") for r in st[k]))
    check("manager's team list matches who they can see", sorted(u["empId"] for u in st.get("team", [])) == seen, st.get("team"))
    check("manager acts only for people below them AND in their teams",
          sorted(p["empId"] for p in st["people"] if p["canApply"]) == ["E1", "E2", "M1", "M2"])
    _, st2 = m2.req("GET", "/api/state")
    check("junior manager sees their own team only (Beta: M1, E2)", sorted(p["empId"] for p in st2["people"]) == ["E2", "M1", "M2"],
          [p["empId"] for p in st2["people"]])
    admin.req("POST", "/api/users/update", {"empId": "A1", "teams": ["All Teams"]})
    check("being in All Teams doesn't expose someone to a manager who shares no named team with them",
          "A1" not in {p["empId"] for p in m1.req("GET", "/api/state")[1]["people"]})
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
    check("All Teams employee sees every employee, sheet-only people included", {"E1", "E2", "E5", "P001"} <= g_seen, sorted(g_seen))
    check("...but never managers or admins, even with All Teams",
          not g_seen & {"M1", "M2", "A1"} and not [r for k in ("leave", "wfh") for r in stg[k] if r["empId"] in ("M1", "M2", "A1")],
          sorted(g_seen & {"M1", "M2", "A1"}))
    others = [r for k in ("leave", "wfh") for r in stg[k] if r["empId"] != "G1"]
    check("...but as an employee still without teammates' reasons", others and all(r.get("redacted") and r["reason"] is None for r in others))
    print("\n-- All Teams together with named teams")
    teams_of = lambda emp: [u["teams"] for u in admin.req("GET", "/api/state")[1]["team"] if u["empId"] == emp][0]
    check("create employee G2 in All Teams and Alpha", mk("G2", "Gail Both", "employee", None, ["All Teams", "Alpha"])[0] == 200)
    check("both are kept - the named team is no longer dropped", teams_of("G2") == ["All Teams", "Alpha"], teams_of("G2"))
    g2 = Client(port)
    check("G2 replaces one-time password", g2.ready("G2", "Start-Here-2026", "Willow-Crane-77")[0] == 200)
    check("G2 still sees everyone, as All Teams gives",
          {"E1", "E2", "E5", "P001"} <= {p["empId"] for p in g2.req("GET", "/api/state")[1]["people"]})
    check("and is now visible to their own team: E1 in Alpha sees G2",
          "G2" in {p["empId"] for p in e1.req("GET", "/api/state")[1]["people"]})
    check("but not to a team they are not in: E2 in Beta doesn't see G2",
          "G2" not in {p["empId"] for p in e2.req("GET", "/api/state")[1]["people"]})
    check("an existing person can be given All Teams alongside their teams",
          admin.req("POST", "/api/users/update", {"empId": "E5", "teams": ["All Teams", "Gamma"]})[0] == 200
          and teams_of("E5") == ["All Teams", "Gamma"], teams_of("E5"))
    admin.req("POST", "/api/users/update", {"empId": "E5", "teams": ["Gamma"]})
    check("duplicates are folded away", admin.req("POST", "/api/users/update", {
        "empId": "G2", "teams": ["All Teams", "Alpha", "All Teams"]})[0] == 200
        and teams_of("G2") == ["All Teams", "Alpha"], teams_of("G2"))
    check("an unknown team is still refused alongside All Teams",
          admin.req("POST", "/api/users/update", {"empId": "G2", "teams": ["All Teams", "Nope"]})[0] == 400)
    check("removing the named team leaves All Teams in place",
          admin.req("POST", "/api/users/update", {"empId": "G2", "teams": ["All Teams"]})[0] == 200
          and teams_of("G2") == ["All Teams"])
    check("a manager in specific teams doesn't get the full team list", "teams" not in m1.req("GET", "/api/state")[1])
    admin.req("POST", "/api/users/update", {"empId": "M1", "teams": ["All Teams"]})
    check("a manager in All Teams gets the team list (team filter choices)",
          m1.req("GET", "/api/state")[1].get("teams") == ["Alpha", "Beta", "Gamma"])
    admin.req("POST", "/api/users/update", {"empId": "M1", "teams": ["Alpha", "Beta"]})
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
    add_hol = lambda date, name="Founders Day": admin.req("POST", "/api/holidays/add", {"date": date, "name": name})
    check("admin adds a public holiday", add_hol("2026-11-03")[0] == 200)
    check("duplicate holiday refused", add_hol("2026-11-03", "Again")[0] == 400)
    check("holiday needs a valid date", add_hol("2026-13-45")[0] == 400)
    check("only admins manage holidays", e5.req("POST", "/api/holidays/add", {"date": "2026-11-04", "name": "Nope"})[0] == 403)
    s, st5 = e5.req("GET", "/api/state")
    check("everyone receives the holiday list", s == 200 and st5.get("holidays") == [{"date": "2026-11-03", "name": "Founders Day"}], st5.get("holidays"))
    hol_req = {"kind": "leave", "forEmpId": "E5", "reason": "Planned Leave"}
    s, b = admin.req("POST", "/api/requests", dict(hol_req, **{"from": "2026-11-02", "to": "2026-11-04", "duration": "Full Day"}))
    filed = [r for r in b.get("state", {}).get("leave", []) if r["id"] == b.get("id")]
    check("a holiday inside a request isn't counted as a leave day", s == 200 and filed and filed[0]["days"] == 2, filed)
    check("a half day on a holiday is refused",
          admin.req("POST", "/api/requests", dict(hol_req, **{"from": "2026-11-03", "duration": "Half Day - 1st Half"}))[0] == 400)
    check("admin removes a holiday", admin.req("POST", "/api/holidays/remove", {"date": "2026-11-03"})[0] == 200)
    check("removing an unknown holiday refused", admin.req("POST", "/api/holidays/remove", {"date": "2026-11-03"})[0] == 400)
    check("admin creates a senior manager", admin.req("POST", "/api/users", {
        "empId": "S1", "name": "Sana Senior", "role": "senior_manager", "password": "Start-Here-2026", "teams": ["Alpha"]})[0] == 200)
    check("a manager can be mapped to a senior manager", admin.req("POST", "/api/users/update", {"empId": "M1", "managerEmpId": "S1"})[0] == 200)
    s1 = [u for u in admin.req("GET", "/api/state")[1]["team"] if u["empId"] == "S1"]
    check("senior manager is a manager with the senior designation", s1 and s1[0]["role"] == "manager" and s1[0]["senior"] is True, s1)
    sc = Client(port)
    check("S1 replaces one-time password", sc.ready("S1", "Start-Here-2026", "Pebble-Ridge-77")[0] == 200)
    s, b = m1.req("POST", "/api/requests", dict(req, **{"from": "2026-11-09", "to": "2026-11-09"}))
    check("senior manager approves a manager mapped to them", s == 200 and decide(sc, b["id"])[0] == 200, b)
    check("senior manager can be changed back to manager", admin.req("POST", "/api/users/update", {"empId": "S1", "role": "manager"})[0] == 200
          and not [u for u in admin.req("GET", "/api/state")[1]["team"] if u["empId"] == "S1"][0]["senior"])
    check("unknown role refused", admin.req("POST", "/api/users", {
        "empId": "S2", "name": "Sid Other", "role": "boss", "password": "Start-Here-2026"})[0] == 400)

    print("\n-- adding many logins from a spreadsheet")
    s, tpl, h = admin.req("GET", "/api/users/template", raw=True)
    tpl_ok = s == 200 and "spreadsheetml" in h.get("Content-Type", "")
    if tpl_ok:
        with zipfile.ZipFile(io.BytesIO(tpl)) as z:
            sheet1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            guide = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
        tpl_ok = all(t in sheet1 for t in ("Employee ID", "Full name", "Role", "Manager employee ID", "Teams")) and "Alpha" in guide
    check("admin downloads the Excel template (headings, and today's teams listed)", tpl_ok, s)
    check("non-admins can't download the template", e5.req("GET", "/api/users/template")[0] == 403)
    imp = lambda c, raw, name="people.xlsx", commit=False: c.req("POST", "/api/users/import", {
        "filename": name, "data": base64.b64encode(raw).decode(), "commit": commit})
    check("the untouched template is refused with a clear message", imp(admin, tpl)[0] == 400)
    sheet_rows = [["Employee ID", "Full name", "Role", "Manager employee ID", "Teams", "One-time password (optional)"],
                  ["9001", "Nina Newhire", "Employee", "M1", "Alpha", ""],
                  ["N2", "Omar Oneday", "Senior manager", "", "beta", "Harbor-Quartz-88"],
                  ["N3", "Pia Pending", "employee", "N2", "Beta", ""],
                  ["E1", "Dup Person", "Employee", "", "", ""],
                  ["N4", "Quinn Quiet", "Chief", "", "", ""],
                  ["N5", "Rae Remote", "Employee", "", "Nowhere", ""],
                  ["N6", "Sam Simple", "Employee", "", "", "password123"],
                  ["N7", "Tia Twin", "Employee", "", "nowhere", ""],
                  ["N7", "Tia Again", "Employee", "", "", ""],
                  ["N8", "Uma Unknown", "Employee", "ZZ9", "", ""],
                  ["N9", "Vic Verylong", "Employee", "", "X" * 41, ""]]
    book = xlsx_bytes(sheet_rows)
    s, b = imp(admin, book)
    errs = {r["empId"] + "@" + str(r["row"]): r["errors"] for r in b.get("rows", [])}
    check("preview marks exactly the good rows ready (IDs typed as numbers included)",
          s == 200 and b.get("ready") == 5 and not any(errs.get(k) for k in ("9001@2", "N2@3", "N3@4", "N5@7", "N7@9")), b)
    check("preview lists a new team once, with its first spelling", b.get("newTeams") == ["Nowhere"], b.get("newTeams"))
    check("preview explains every problem row (existing ID, bad role, weak password, duplicate, unknown manager, long team name)",
          all(errs.get(k) for k in ("E1@5", "N4@6", "N6@8", "N7@10", "N8@11", "N9@12")), errs)
    st_now = admin.req("GET", "/api/state")[1]
    check("preview creates nothing - no logins, no teams",
          "9001" not in {u["empId"] for u in st_now["team"]} and "Nowhere" not in st_now.get("teams", []))
    check("non-admins can't import", imp(e5, book)[0] == 403)
    s, b = imp(admin, book, commit=True)
    made = {r["empId"]: r for r in b.get("rows", []) if r.get("created")}
    check("import creates only the rows that passed", s == 200 and b.get("created") == 5 and sorted(made) == ["9001", "N2", "N3", "N5", "N7"],
          (s, b.get("created"), sorted(made)))
    check("import creates the new team, and both spellings land in it",
          b.get("createdTeams") == ["Nowhere"] and "Nowhere" in b["state"].get("teams", [])
          and [u["teams"] for u in b["state"]["team"] if u["empId"] in ("N5", "N7")] == [["Nowhere"], ["Nowhere"]], b.get("createdTeams"))
    people = {u["empId"]: u for u in b.get("state", {}).get("team", [])}
    check("imported people get their role, teams and manager (incl. a manager from the same file)",
          people.get("9001", {}).get("managerEmpId") == "M1" and people.get("9001", {}).get("teams") == ["Alpha"]
          and people.get("N2", {}).get("senior") is True and people.get("N2", {}).get("teams") == ["Beta"]
          and people.get("N3", {}).get("managerEmpId") == "N2", {k: people.get(k) for k in ("9001", "N2", "N3")})
    check("a one-time password given in the file is used", made.get("N2", {}).get("oneTimePassword") == "Harbor-Quartz-88")
    s, b = Client(port).login("9001", made.get("9001", {}).get("oneTimePassword") or "x")
    check("an imported person signs in with the generated password and must change it", s == 200 and b["user"]["mustChange"], b)
    s, b = imp(admin, b'Employee ID,Full name,Teams\r\nW1,Wide Access,All Teams; Alpha\r\n', "wide.csv")
    check("a file may give All Teams alongside a named team",
          s == 200 and b.get("ready") == 1 and b["rows"][0]["teams"] == ["All Teams", "Alpha"], b.get("rows"))
    s, b = imp(admin, b"Employee ID,Full name\r\nC1,Carl Csv\r\n", "people.csv")
    check("a .csv file works too", s == 200 and b.get("ready") == 1, b)
    many = ("Employee ID,Full name\n" + "".join("X%d,Xavier Person\n" % i for i in range(501))).encode()
    check("more than 500 people in a file refused", imp(admin, many, "big.csv")[0] == 400)
    check("an old .xls file gets a clear message", imp(admin, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "old.xls")[0] == 400)
    check("a workbook with XML entity tricks is refused", imp(admin, xlsx_bytes(sheet_rows, doctype=True))[0] == 400)
    check("random bytes are refused", imp(admin, bytes(range(256)) * 4, "junk.xlsx")[0] == 400)
    s, b = admin.req("POST", "/api/logout", {})
    check("logout ends the session", s == 200 and admin.req("GET", "/api/state")[0] == 401)

    print("\n-- reporting chain")
    loop = {"X": {"emp_id": "X", "name": "Xan Loop", "role": "manager", "manager_emp_id": "Y", "active": 1},
            "Y": {"emp_id": "Y", "name": "Yul Loop", "role": "manager", "manager_emp_id": "X", "active": 1}}
    check("a loop in manager assignments doesn't hang", domain.reporting_chain(loop["X"], loop) == {"Y"})

    print("\n-- password reset by email")
    here = "127.0.0.1"

    def fresh_limits():
        """Each check below starts from clean request counts for this address."""
        application.limiter.clear("reset-ip:" + here, "code-ip:" + here, "ip:" + here,
                                  *["reset-id:" + x for x in ("r1", "r2", "r3", "r4", "nobody-here")])

    admin.login("A1", "Blue-Harbor-71")
    upd = lambda emp_id, value: admin.req("POST", "/api/users/update", {"empId": emp_id, "email": value})
    for emp_id, nm in (("R1", "Rita Reset"), ("R2", "Ravi Reset"), ("R3", "Rhea Reset")):
        mk(emp_id, nm, "employee", None, ["Alpha"])
    r1, r2 = Client(port), Client(port)
    check("R1 and R2 set their own passwords", r1.ready("R1", "Start-Here-2026", "Quiet-Lake-81")[0] == 200
          and r2.ready("R2", "Start-Here-2026", "Quiet-Lake-82")[0] == 200)

    s, b = upd("R1", "  Rita.Reset@Example.COM ")
    row = [u for u in b.get("state", {}).get("team", []) if u["empId"] == "R1"]
    check("admin saves an email, trimmed and lower-cased", s == 200 and row and row[0].get("email") == "rita.reset@example.com", b)
    check("something that isn't an email address is refused", upd("R2", "not-an-email")[0] == 400)
    check("each address belongs to one login", upd("R2", "RITA.RESET@example.com")[0] == 409)
    s, b = admin.req("POST", "/api/users", {"empId": "R4", "name": "Remy Reset", "role": "employee",
                                            "password": "Start-Here-2026", "email": "remy@example.com"})
    check("a login can be created with an email", s == 200 and db.user("R4")["email"] == "remy@example.com", b)
    check("...but not with one already in use", admin.req("POST", "/api/users", {
        "empId": "R5", "name": "Rory Reset", "role": "employee", "password": "Start-Here-2026",
        "email": "remy@example.com"})[0] == 409)
    m1c = Client(port)
    m1c.login("M1", "Silver-Pine-33")
    _, mst = m1c.req("GET", "/api/state")
    check("managers never receive anyone's email", mst.get("team") and all("email" not in u for u in mst["team"]))
    check("people see their own email", r1.req("GET", "/api/me")[1].get("user", {}).get("email") == "rita.reset@example.com")
    _, ast = admin.req("GET", "/api/state")
    check("admins see whether email is set up", ast.get("mail", {}).get("ready") is True, ast.get("mail"))

    anon2 = Client(port)
    forgot = lambda emp_id: anon2.req("POST", "/api/password/forgot", {"empId": emp_id})
    reset = lambda emp_id, code, pw: anon2.req("POST", "/api/password/reset", {"empId": emp_id, "code": code, "password": pw})
    fresh_limits()
    before = len(mail.sent)
    s1, b1 = forgot("R1")
    code = mail.last_code("rita.reset@example.com")
    check("a 6-digit code is emailed to the address on file", s1 == 200 and len(mail.sent) == before + 1 and code, (s1, b1))
    check("the code is never in the reply", code and code not in json.dumps(b1))
    s2, b2 = forgot("NOBODY-HERE")
    s3, b3 = forgot("R2")                             # no email on file
    check("an unknown ID, or one without an email, gets the very same reply - and nothing is sent",
          (s2, b2) == (s1, b1) == (s3, b3) and len(mail.sent) == before + 1, (b2, b3))
    with db.tx() as c:
        stored = c.one("SELECT code_hash FROM password_resets WHERE emp_id = ?", ("R1",))
    check("only a hash of the code is stored", stored and code not in stored["code_hash"])
    check("a wrong code is refused", reset("R1", "%06d" % ((int(code) + 1) % 10 ** 6), "Fresh-Meadow-93")[0] == 400)
    check("the code must belong to that employee ID", reset("R2", code, "Fresh-Meadow-93")[0] == 400)
    s, b = reset("R1", code, "password123")
    check("a weak new password is refused, with the reason", s == 400 and "common" in b.get("error", ""), b)
    signed_in = r1.req("GET", "/api/state")[0] == 200
    s, b = reset("R1", code, "Fresh-Meadow-93")
    check("the right code sets the new password", s == 200, b)
    check("...signs them out everywhere", signed_in and r1.req("GET", "/api/state")[0] == 401)
    check("...and the new password works, the old one doesn't",
          Client(port).login("R1", "Fresh-Meadow-93")[0] == 200 and Client(port).login("R1", "Quiet-Lake-81")[0] == 401)
    check("a code works only once", reset("R1", code, "Other-Meadow-94")[0] == 400)
    check("the reset is in the activity log",
          any(a["action"] == "password reset by email code" and a["id"] == "R1" for a in admin.req("GET", "/api/state")[1]["audit"]))

    fresh_limits()
    forgot("R1")
    code = mail.last_code("rita.reset@example.com")
    for _ in range(security.RESET_TRIES):
        reset("R1", "%06d" % ((int(code) + 7) % 10 ** 6), "Fresh-Meadow-95")
    check("five wrong guesses use the code up - even the right one fails after", reset("R1", code, "Fresh-Meadow-95")[0] == 400)
    fresh_limits()
    forgot("R1")
    code = mail.last_code("rita.reset@example.com")
    with db.tx() as c:
        c.execute("UPDATE password_resets SET expires_at = ? WHERE emp_id = ?", (time.time() - 1, "R1"))
    check("an expired code is refused", reset("R1", code, "Fresh-Meadow-96")[0] == 400)
    fresh_limits()
    forgot("R1")
    first = mail.last_code("rita.reset@example.com")
    forgot("R1")
    second = mail.last_code("rita.reset@example.com")
    check("asking again replaces the earlier code", first == second or reset("R1", first, "Fresh-Meadow-97")[0] == 400)
    upd("R1", "rita.new@example.com")
    check("changing someone's email cancels a code sent to the old address", reset("R1", second, "Fresh-Meadow-97")[0] == 400)

    fresh_limits()
    statuses = [forgot("R2")[0] for _ in range(4)]
    check("at most 3 codes per employee ID in 15 minutes", statuses == [200, 200, 200, 429], statuses)
    fresh_limits()
    sprayed = [forgot("SPRAY%d" % i)[0] for i in range(11)]
    check("one address asking for codes for many IDs is stopped (10)", sprayed == [200] * 10 + [429], sprayed)
    fresh_limits()
    guessed = [reset("R1", "%06d" % i, "Fresh-Meadow-98")[0] for i in range(11)]
    check("one address entering many wrong codes is stopped (10)", guessed == [400] * 10 + [429], guessed)

    fresh_limits()
    upd("R2", "ravi.reset@example.com")
    for _ in range(5):
        Client(port).login("R2", "wrong-guess-1")
    check("R2 locked out on this device", Client(port).login("R2", "Quiet-Lake-82")[0] == 429)
    forgot("R2")
    check("...resets by email and can sign in straight away",
          reset("R2", mail.last_code("ravi.reset@example.com"), "Bright-River-62")[0] == 200
          and Client(port).login("R2", "Bright-River-62")[0] == 200)

    fresh_limits()
    application.reset_pad = 0.4
    t0 = time.monotonic()
    forgot("NOBODY-HERE")
    took = time.monotonic() - t0
    application.reset_pad = 0
    check("a code request takes the same minimum time whether or not an email is sent", took >= 0.4, took)

    s, b = admin.req("POST", "/api/mail/test", {})
    check("a test email needs the admin's own address first", s == 400, b)
    upd("A1", "ada.admin@example.com")
    s, b = admin.req("POST", "/api/mail/test", {})
    check("admin sends themselves a test email", s == 200 and b.get("to") == "ada.admin@example.com"
          and mail.to("ada.admin@example.com"), b)
    check("non-admins can't send test emails", m1c.req("POST", "/api/mail/test", {})[0] == 403)

    s, sheet, h = admin.req("GET", "/api/users/emails-sheet", raw=True)
    sheet_ok = s == 200 and "spreadsheetml" in h.get("Content-Type", "")
    if sheet_ok:
        with zipfile.ZipFile(io.BytesIO(sheet)) as z:
            body1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        sheet_ok = all(t in body1 for t in ("Employee ID", "Email", "R1", "rita.new@example.com"))
    check("admin downloads the email sheet: every login with its current address", sheet_ok, s)
    check("non-admins can't download it", m1c.req("GET", "/api/users/emails-sheet")[0] == 403)
    em = lambda raw, commit=False: admin.req("POST", "/api/users/emails", {
        "filename": "emails.xlsx", "data": base64.b64encode(raw).decode(), "commit": commit})
    book = xlsx_bytes([["Employee ID", "Full name (for reference)", "Email"],
                       ["R3", "Rhea Reset", "rhea@example.com"],          # new address
                       ["R1", "Rita Reset", "ravi.reset@example.com"],    # R1 and R2 swap
                       ["R2", "Ravi Reset", "rita.new@example.com"],
                       ["A1", "Ada Admin", ""],                           # blank: left as it is
                       ["R4", "Remy Reset", "remy@example.com"],          # unchanged
                       ["ZZ9", "Nobody", "zz@example.com"],               # no such login
                       ["R4", "Remy Again", "other@example.com"],         # same ID twice
                       ["M1", "Mona Manager", "nope"],                    # not an email
                       ["G1", "Gus Global", "remy@example.com"]])         # R4's address
    s, b = em(book)
    by = {(r["empId"], r["row"]): r for r in b.get("rows", [])}
    check("email sheet preview: 3 to save, two people may swap addresses", s == 200 and b.get("changes") == 3, b.get("changes"))
    check("...blank and unchanged rows are left alone",
          by.get(("A1", 5), {}).get("status") == "blank" and by.get(("R4", 6), {}).get("status") == "same")
    check("...unknown IDs, repeats, bad addresses and taken addresses are explained",
          all(by.get(k, {}).get("errors") for k in (("ZZ9", 7), ("R4", 8), ("M1", 9), ("G1", 10))),
          {k: v["errors"] for k, v in by.items()})
    check("...and nothing is saved by the preview", db.user("R3")["email"] == "")
    check("non-admins can't update emails", m1c.req("POST", "/api/users/emails", {
        "filename": "e.xlsx", "data": base64.b64encode(book).decode(), "commit": True})[0] == 403)
    s, b = em(book, commit=True)
    check("saving updates exactly the rows that passed", s == 200 and b.get("updated") == 3
          and (db.user("R1")["email"], db.user("R2")["email"], db.user("R3")["email"], db.user("G1")["email"])
          == ("ravi.reset@example.com", "rita.new@example.com", "rhea@example.com", ""), b.get("updated"))
    admin.req("POST", "/api/users/update", {"empId": "R3", "managerEmpId": "M1"})
    s, sheet, _ = admin.req("GET", "/api/users/emails-sheet", raw=True)
    back = {r["empId"]: r for r in bulk_users.read_email_rows("emails.xlsx", sheet)} if s == 200 else {}
    check("the email sheet shows each person's manager by name",
          back.get("R3", {}).get("manager") == "Mona Manager" and back.get("R1", {}).get("manager") == "",
          {k: back.get(k, {}).get("manager") for k in ("R1", "R3")})
    s, b = em(sheet)
    check("the downloaded sheet uploads back as it is: no problems, nothing to change",
          s == 200 and b.get("changes") == 0 and not any(r["errors"] for r in b.get("rows", [])),
          [r for r in b.get("rows", []) if r["errors"]][:3])
    edited = xlsx_bytes([["Employee ID", "Full name (for reference)", "Manager (for reference)", "Email"],
                         ["R3", "Rhea Reset", "Someone Else", "rhea.new@example.com"]])
    s, b = em(edited, commit=True)
    check("with the Manager column, the email updates - and the manager is left alone",
          s == 200 and b.get("updated") == 1 and db.user("R3")["email"] == "rhea.new@example.com"
          and db.user("R3")["manager_emp_id"] == "M1", b.get("updated"))

    csv_new = b"Employee ID,Full name,Email\r\nN20,Nia Newmail,Nia@Example.com\r\nN21,Ned Dupmail,remy@example.com\r\n"
    s, b = imp(admin, csv_new, "new.csv")
    new_rows = {r["empId"]: r for r in b.get("rows", [])}
    check("new logins can come with an email in the import",
          s == 200 and new_rows.get("N20", {}).get("email") == "nia@example.com" and not new_rows["N20"]["errors"], b)
    check("...and an address already in use is caught in the preview", new_rows.get("N21", {}).get("errors"))
    imp(admin, csv_new, "new.csv", True)
    check("the imported login keeps its email", (db.user("N20") or {}).get("email") == "nia@example.com")

    hosted_plain = runner.serve(wsgi.App(target, seed_path, trust_proxy=True, mail=mailer.Mailer(env={})), "127.0.0.1", 0)
    threading.Thread(target=hosted_plain.serve_forever, daemon=True).start()
    s, b = Client(hosted_plain.server_address[1]).req("POST", "/api/password/forgot", {"empId": "R1"})
    check("hosted without email settings, people are told to ask an admin", s == 503 and "admin" in b.get("error", ""), b)
    hosted_plain.shutdown()
    smtp = mailer.Mailer(env={"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "team@example.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop"})
    check("SMTP settings come from the environment, and the password is never shown",
          smtp.configured and smtp.port == 587 and smtp.sender == "team@example.com" and "abcd" not in smtp.describe())
    local = mailer.Mailer(env={}, console=True)
    check("on your own computer with nothing set, codes go to the console", local.available and not local.configured)

    print("\n-- storage & brute force")
    if not pg_url:
        raw = open(db_path, "rb").read()
        check("no plaintext passwords in the database", not any(p in raw for p in (b"Blue-Harbor-71", b"Harbor-Lamp-11", b"Start-Here-2026")))
    check("passwords stored as scrypt hashes", all(u["pw_hash"].startswith("scrypt$") for u in db.users(include_hash=True).values()))
    legacy = security.hash_password("Maple-Stone-55", n=2 ** 14, r=8, p=1)      # the settings used before
    with db.tx() as c:
        c.execute("UPDATE users SET pw_hash = ? WHERE emp_id = ?", (legacy, "E5"))
    check("an older, weaker hash still signs in", Client(port).login("E5", "Maple-Stone-55")[0] == 200)
    check("...and is upgraded to today's settings at that sign-in",
          db.user("E5", include_hash=True)["pw_hash"].startswith("scrypt$%d$%d$%d$" % (security.N, security.R, security.P)))
    atk = Client(port)
    for _ in range(5):
        atk.login("M1", "guess-guess")
    check("6th attempt from the same address locked out, even with the right password", atk.login("M1", "Silver-Pine-33")[0] == 429)

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
    s, _ = two.req("POST", "/api/login", {"empId": "M2", "password": "Amber-Rail-44"}, {"X-Forwarded-For": "198.51.100.23"})
    check("someone else's wrong guesses don't lock the real user out on their own device", s == 200, s)
    spray = {"X-Forwarded-For": "192.0.2.50"}
    for i in range(20):
        one.req("POST", "/api/login", {"empId": "NOBODY%d" % i, "password": "guess-guess"}, spray)
    s, _ = one.req("POST", "/api/login", {"empId": "A1", "password": "Blue-Harbor-71"}, spray)
    check("one address trying many IDs is locked out (20)", s == 429, s)
    for i in range(50):
        one.req("POST", "/api/login", {"empId": "G1", "password": "guess-guess"}, {"X-Forwarded-For": "10.1.0.%d" % i})
    s, _ = one.req("POST", "/api/login", {"empId": "G1", "password": "Cedar-Wind-66"}, {"X-Forwarded-For": "10.2.0.1"})
    check("one ID attacked from many addresses is locked out (50)", s == 429, s)
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

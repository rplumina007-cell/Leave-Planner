"""JSON API. Every permission is decided here - the browser only hides buttons.

Teams decide who sees whom. An admin gives each login one or more teams; people
see only those sharing one of their teams - nothing at all about anyone else -
and "All Teams" counts as every team. Someone with no team sees only themselves.

Roles:
  employee  apply/withdraw own requests; calendar of their teams, with teammates'
            reasons and comments hidden
  manager   everyone in their teams in full on the calendar, dashboards and
            register; apply for, decide (never their own) and delete for people
            below them in the reporting chain who are also in one of their teams
  admin     full access to everyone - including people who have records but no
            login yet - plus users, roles, teams, manager assignments and settings
"""
import os
import re

import domain
import security
from domain import RuleError

ROLES = ("employee", "manager", "admin")
EMP_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
# Reachable while a user still has to replace their initial password.
PASSWORD_GATE_OPEN = {"/api/me", "/api/me/password", "/api/logout"}


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.payload = status, dict(error=message, **extra)


def public_user(u):
    return {"empId": u["emp_id"], "name": u["name"], "role": u["role"],
            "managerEmpId": u["manager_emp_id"], "teams": u.get("teams") or [],
            "mustChange": u["must_change"], "active": u["active"]}


def need(user, *roles):
    if not user:
        raise ApiError(401, "Please sign in")
    if roles and user["role"] not in roles:
        raise ApiError(403, "You don't have permission to do that")


def state_payload(app, user):
    users = app.db.users()
    st = app.db.read_state()
    out = domain.view_for(st, user, users)
    out["me"] = public_user(user)
    if user["role"] in ("manager", "admin"):
        out["team"] = [public_user(u) for u in users.values() if domain.can_see(user, u["emp_id"], users)]
    if user["role"] == "admin":
        h = domain.load_seed(app.seed_path)[1] if os.path.exists(app.seed_path) else None
        out["seedIsNewer"] = bool(h) and h != st.get("seedHash")
        known = set(users)
        seen = {}
        for kind in domain.KINDS:
            for r in st[kind]:
                if r.get("empId") and r["empId"] not in known:
                    seen[r["empId"]] = r["name"]
        out["unregistered"] = [{"empId": k, "name": v} for k, v in sorted(seen.items(), key=lambda x: x[1])]
    return out


# ------------------------------------------------------------------ handlers
def login(app, body, ctx):
    emp = str(body.get("empId", "")).strip()
    keys = security.LoginLimiter.login_keys(emp, ctx["ip"])
    if app.limiter.blocked(*keys):
        raise ApiError(429, "Too many failed attempts. Try again in 15 minutes.")
    password = str(body.get("password", ""))
    u = app.db.user(emp, include_hash=True) if emp else None
    ok = security.verify_or_dummy(password, u["pw_hash"] if u else None)
    if not (ok and u and u["active"]):
        app.limiter.fail(*keys)
        raise ApiError(401, "Invalid employee ID or password")
    app.limiter.clear(keys[0], keys[2])          # this ID's counts; the address's own count stands
    if security.needs_rehash(u["pw_hash"]):
        app.db.update_user(u["emp_id"], password=password)     # upgrade an older hash to today's settings
    token = app.db.create_session(u["emp_id"])
    return {"user": public_user(u)}, [("Set-Cookie", app.cookie(token, ctx))]


def logout(app, body, ctx):
    app.db.end_session(ctx["token"])
    return {"ok": True}, [("Set-Cookie", app.cookie("", ctx, expire=True))]


def me(app, body, ctx):
    need(ctx["user"])
    return {"user": public_user(ctx["user"])}, []


def change_password(app, body, ctx):
    user = ctx["user"]
    need(user)
    key = "pw:" + user["emp_id"]
    if app.limiter.blocked(key):
        raise ApiError(429, "Too many attempts. Try again in 15 minutes.")
    full = app.db.user(user["emp_id"], include_hash=True)
    current, nxt = str(body.get("current", "")), body.get("next", "")
    if not security.verify_password(current, full["pw_hash"]):
        app.limiter.fail(key)
        raise ApiError(400, "Current password is incorrect")
    if nxt == current:
        raise ApiError(400, "New password must be different from the current one")
    problem = security.password_problem(nxt, user["emp_id"], user["name"])
    if problem:
        raise ApiError(400, problem)
    app.db.update_user(user["emp_id"], password=nxt, must_change=0)
    app.db.end_all_sessions(user["emp_id"], except_token=ctx["token"])
    app.limiter.clear(key)
    return {"ok": True}, []


def get_state(app, body, ctx):
    need(ctx["user"])
    return state_payload(app, ctx["user"]), []


def submit_request(app, body, ctx):
    user = ctx["user"]
    need(user)
    for_emp = str(body.get("forEmpId") or user["emp_id"])
    users = app.db.users()
    if not domain.can_file_for(user, for_emp, users):
        raise ApiError(403, "You can only submit requests for yourself" if user["role"] == "employee"
                       else "You can only submit requests for yourself or your team")
    kind = body.get("kind")

    def fn(st):
        # An admin may file for someone who only exists in the records (no login yet).
        name = (users.get(for_emp) or {}).get("name") or domain.roster(st, users).get(for_emp)
        if not name:
            raise ApiError(400, "Unknown team member")
        rec = domain.build_request(st, kind, body, {"emp_id": for_emp, "name": name})
        st[kind].insert(0, rec)
        domain.log(st, "submitted", kind, rec["id"], rec["name"], user["name"])
        return rec["id"]
    rid = app.db.mutate(fn)
    return {"id": rid, "state": state_payload(app, user)}, []


def decide_request(app, body, ctx):
    user = ctx["user"]
    need(user, "manager", "admin")
    status = body.get("status")
    if status not in ("Approved", "Declined", "Pending"):
        raise ApiError(400, "Status must be Approved, Declined, or Pending to undo a decision")
    users = app.db.users()

    def fn(st):
        rec = domain.find(st, body.get("kind"), body.get("id"))
        if not domain.can_see(user, rec.get("empId"), users):
            raise ApiError(404, "Request not found")     # don't reveal whose it is
        if not domain.can_decide(user, rec, users):
            raise ApiError(403, "Only %s's manager or an admin can decide this request" % rec["name"])
        if status == "Pending":                           # undo: back to the queue, as it was
            if rec.get("status") == "Pending":
                raise ApiError(400, "That request is already pending")
            rec["status"] = "Pending"
            rec.pop("decidedBy", None)
            rec.pop("decidedAt", None)
            if "requestComment" in rec:
                rec["comment"] = rec.pop("requestComment")
            domain.log(st, "decision undone", body["kind"], rec["id"], rec["name"], user["name"])
            return
        rec.update(status=status, decidedBy=user["name"], decidedAt=domain.now_iso())
        note = str(body.get("note") or "").strip()[:500]
        if note:
            rec.setdefault("requestComment", rec.get("comment", ""))   # so an undo can restore it
            rec["comment"] = note
        domain.log(st, status.lower(), body["kind"], rec["id"], rec["name"], user["name"])
    app.db.mutate(fn)
    return {"state": state_payload(app, user)}, []


def delete_request(app, body, ctx):
    user = ctx["user"]
    need(user)
    users = app.db.users()

    def fn(st):
        kind = body.get("kind")
        rec = domain.find(st, kind, body.get("id"))
        if not domain.can_see(user, rec.get("empId"), users):
            raise ApiError(404, "Request not found")
        if not domain.can_delete(user, rec, users):
            raise ApiError(403, "Only an admin or %s's manager can delete this record" % rec["name"])
        st[kind] = [r for r in st[kind] if r is not rec]
        own = rec.get("empId") == user["emp_id"]
        action = ("withdrawn" if rec.get("status") == "Pending" else "cancelled (was %s)" % rec.get("status", "").lower()) if own \
            else "deleted (was %s)" % rec.get("status", "").lower()
        domain.log(st, action, kind, rec["id"], rec["name"], user["name"])
    app.db.mutate(fn)
    return {"state": state_payload(app, user)}, []


# ------------------------------------------------------------------ users (admin)
def _check_manager(app, manager_emp_id, emp_id):
    if not manager_emp_id:
        return None
    if manager_emp_id == emp_id:
        raise ApiError(400, "Someone cannot be their own manager")
    m = app.db.user(manager_emp_id)
    if not m or not m["active"] or m["role"] not in ("manager", "admin"):
        raise ApiError(400, "The assigned manager must be an active Manager or Admin")
    return manager_emp_id


def _check_teams(app, teams):
    """A clean list of existing team names. All Teams on its own stands for every team."""
    if teams is None:
        return []
    if not isinstance(teams, list) or not all(isinstance(t, str) for t in teams):
        raise ApiError(400, "Teams must be a list of team names")
    known, out = set(app.db.read_state().get("teams", [])), []
    for t in teams:
        if t != domain.ALL_TEAMS and t not in known:
            raise ApiError(400, 'Unknown team "%s"' % t)
        if t not in out:
            out.append(t)
    return [domain.ALL_TEAMS] if domain.ALL_TEAMS in out else out


def create_user(app, body, ctx):
    need(ctx["user"], "admin")
    emp = str(body.get("empId", "")).strip()
    name = " ".join(str(body.get("name", "")).split())
    role = body.get("role", "employee")
    if not EMP_ID_RE.match(emp):
        raise ApiError(400, "Employee ID may use letters, digits, dot, dash or underscore (max 32)")
    if not 2 <= len(name) <= 80:
        raise ApiError(400, "Enter the employee's full name")
    if role not in ROLES:
        raise ApiError(400, "Unknown role")
    if app.db.user(emp):
        raise ApiError(409, "A login for employee ID %s already exists" % emp)
    problem = security.password_problem(body.get("password", ""), emp, name)
    if problem:
        raise ApiError(400, problem)
    manager = _check_manager(app, body.get("managerEmpId"), emp)
    teams = _check_teams(app, body.get("teams"))
    app.db.create_user(emp, name, role, body["password"], manager, must_change=True, teams=teams)
    app.db.mutate(lambda st: domain.log(st, "login created (%s)" % role, "users", emp, name, ctx["user"]["name"]))
    return {"state": state_payload(app, ctx["user"])}, []


def update_user(app, body, ctx):
    admin = ctx["user"]
    need(admin, "admin")
    emp = str(body.get("empId", ""))
    u = app.db.user(emp)
    if not u:
        raise ApiError(404, "User not found")
    changes = {}
    if "name" in body:
        name = " ".join(str(body["name"]).split())
        if not 2 <= len(name) <= 80:
            raise ApiError(400, "Enter the employee's full name")
        changes["name"] = name
    if "role" in body:
        if body["role"] not in ROLES:
            raise ApiError(400, "Unknown role")
        if u["role"] == "admin" and body["role"] != "admin" and app.db.count_admins(exclude=emp) == 0:
            raise ApiError(400, "There must always be at least one active admin")
        changes["role"] = body["role"]
    if "managerEmpId" in body:
        changes["manager_emp_id"] = _check_manager(app, body["managerEmpId"], emp)
    if "teams" in body:
        changes["teams"] = _check_teams(app, body["teams"])
    if "active" in body:
        if emp == admin["emp_id"] and not body["active"]:
            raise ApiError(400, "You cannot deactivate your own login")
        if u["role"] == "admin" and not body["active"] and app.db.count_admins(exclude=emp) == 0:
            raise ApiError(400, "There must always be at least one active admin")
        changes["active"] = 1 if body["active"] else 0
    app.db.update_user(emp, **changes)
    unassigned = 0
    if changes.get("role") == "employee" and u["role"] != "employee":
        # An employee can't approve anything, so their reports would be stranded.
        for r in app.db.users().values():
            if r["manager_emp_id"] == emp:
                app.db.update_user(r["emp_id"], manager_emp_id=None)
                unassigned += 1
    if changes.get("active") == 0 or "role" in changes:
        app.db.end_all_sessions(emp)
    app.db.mutate(lambda st: domain.log(st, "login updated", "users", emp, u["name"], admin["name"]))
    return {"unassigned": unassigned, "state": state_payload(app, admin)}, []


def reset_password(app, body, ctx):
    need(ctx["user"], "admin")
    emp = str(body.get("empId", ""))
    u = app.db.user(emp)
    if not u:
        raise ApiError(404, "User not found")
    problem = security.password_problem(body.get("password", ""), emp, u["name"])
    if problem:
        raise ApiError(400, problem)
    app.db.update_user(emp, password=body["password"], must_change=1)
    app.db.end_all_sessions(emp)
    app.db.mutate(lambda st: domain.log(st, "password reset", "users", emp, u["name"], ctx["user"]["name"]))
    return {"ok": True}, []


def delete_user(app, body, ctx):
    admin = ctx["user"]
    need(admin, "admin")
    emp = str(body.get("empId", ""))
    u = app.db.user(emp)
    if not u:
        raise ApiError(404, "User not found")
    if emp == admin["emp_id"]:
        raise ApiError(400, "You cannot delete your own login")
    if u["role"] == "admin" and app.db.count_admins(exclude=emp) == 0:
        raise ApiError(400, "There must always be at least one active admin")
    app.db.delete_user(emp)            # their requests stay; reports lose their manager
    app.db.mutate(lambda st: domain.log(st, "login deleted", "users", emp, u["name"], admin["name"]))
    return {"state": state_payload(app, admin)}, []


# ------------------------------------------------------------------ public holidays (admin)
def holiday_add(app, body, ctx):
    need(ctx["user"], "admin")

    def fn(st):
        day = domain.add_holiday(st, body.get("date"), body.get("name"))
        domain.log(st, "holiday added", "holidays", day, " ".join(str(body.get("name") or "").split())[:60], ctx["user"]["name"])
    app.db.mutate(fn)
    return {"state": state_payload(app, ctx["user"])}, []


def holiday_remove(app, body, ctx):
    need(ctx["user"], "admin")
    date = str(body.get("date") or "")

    def fn(st):
        domain.remove_holiday(st, date)
        domain.log(st, "holiday removed", "holidays", date, "", ctx["user"]["name"])
    app.db.mutate(fn)
    return {"state": state_payload(app, ctx["user"])}, []


# ------------------------------------------------------------------ teams (admin)
def team_add(app, body, ctx):
    need(ctx["user"], "admin")

    def fn(st):
        name = domain.add_team(st, body.get("name"))
        domain.log(st, "team created", "teams", "", name, ctx["user"]["name"])
    app.db.mutate(fn)
    return {"state": state_payload(app, ctx["user"])}, []


def team_remove(app, body, ctx):
    need(ctx["user"], "admin")
    name = body.get("name")
    app.db.mutate(lambda st: domain.remove_team(st, name))      # refuses an unknown name before anyone is changed
    moved = 0
    for u in app.db.users().values():
        if name in u["teams"]:
            app.db.update_user(u["emp_id"], teams=[t for t in u["teams"] if t != name])
            moved += 1
    app.db.mutate(lambda st: domain.log(st, "team removed (%d members)" % moved, "teams", "", name, ctx["user"]["name"]))
    return {"removed": moved, "state": state_payload(app, ctx["user"])}, []


# ------------------------------------------------------------------ settings (admin)
def option_add(app, body, ctx):
    need(ctx["user"], "admin")
    app.db.mutate(lambda st: domain.add_option(st, body.get("key"), body.get("value"), bool(body.get("planned"))))
    return {"state": state_payload(app, ctx["user"])}, []


def option_remove(app, body, ctx):
    need(ctx["user"], "admin")
    app.db.mutate(lambda st: domain.remove_option(st, body.get("key"), body.get("value")))
    return {"state": state_payload(app, ctx["user"])}, []


def option_planned(app, body, ctx):
    need(ctx["user"], "admin")
    reason, planned = body.get("reason"), bool(body.get("planned"))

    def fn(st):
        pr = [r for r in st["plannedReasons"] if r != reason]
        st["plannedReasons"] = pr + [reason] if planned else pr
    app.db.mutate(fn)
    return {"state": state_payload(app, ctx["user"])}, []


def purge(app, body, ctx):
    need(ctx["user"], "admin")
    cutoff = domain.cutoff_for(body.get("amount", 1), body.get("unit"))
    opts = {k: bool(body.get(k)) for k in ("leave", "wfh", "keepPending")}

    def fn(st):
        n = domain.purge(st, cutoff, opts)
        domain.log(st, "purged %d records before %s" % (n["leave"] + n["wfh"], cutoff), "cleanup", "", "", ctx["user"]["name"])
        return n
    removed = app.db.mutate(fn)
    return {"removed": removed, "cutoff": cutoff, "state": state_payload(app, ctx["user"])}, []


def sync(app, body, ctx):
    need(ctx["user"], "admin")
    if not os.path.exists(app.seed_path):
        raise ApiError(400, "No sheet import found on the server. Run tools/import-sheet.py first.")
    seed, h = domain.load_seed(app.seed_path)

    def fn(st):
        s = domain.sync_from_seed(st, seed, h)
        domain.log(st, "synced sheet data (%d new, %d updated, %d removed)" % (s["added"], s["updated"], s["removed"]),
                   "sync", "", seed.get("generatedAt", ""), ctx["user"]["name"])
        return s
    summary = app.db.mutate(fn)
    return {"summary": summary, "state": state_payload(app, ctx["user"])}, []


def export(app, body, ctx):
    need(ctx["user"], "admin")
    st = app.db.read_state()
    st["users"] = [public_user(u) for u in app.db.users().values()]
    return st, []


ROUTES = {
    ("POST", "/api/login"): login,
    ("POST", "/api/logout"): logout,
    ("GET", "/api/me"): me,
    ("POST", "/api/me/password"): change_password,
    ("GET", "/api/state"): get_state,
    ("POST", "/api/requests"): submit_request,
    ("POST", "/api/requests/decide"): decide_request,
    ("POST", "/api/requests/delete"): delete_request,
    ("POST", "/api/users"): create_user,
    ("POST", "/api/users/update"): update_user,
    ("POST", "/api/users/reset-password"): reset_password,
    ("POST", "/api/users/delete"): delete_user,
    ("POST", "/api/holidays/add"): holiday_add,
    ("POST", "/api/holidays/remove"): holiday_remove,
    ("POST", "/api/teams/add"): team_add,
    ("POST", "/api/teams/remove"): team_remove,
    ("POST", "/api/options/add"): option_add,
    ("POST", "/api/options/remove"): option_remove,
    ("POST", "/api/options/planned"): option_planned,
    ("POST", "/api/purge"): purge,
    ("POST", "/api/sync"): sync,
    ("GET", "/api/export"): export,
}


def handle(app, method, path, body, ctx):
    """Returns (status, payload, headers)."""
    fn = ROUTES.get((method, path))
    if not fn:
        return 404, {"error": "Not found"}, []
    user = ctx["user"]
    if user and user["must_change"] and path not in PASSWORD_GATE_OPEN:
        return 403, {"error": "Please set a new password first", "mustChange": True}, []
    if fn not in (login,) and not user and path != "/api/logout":
        return 401, {"error": "Please sign in"}, []
    try:
        payload, headers = fn(app, body, ctx)
        return 200, payload, headers
    except ApiError as e:
        return e.status, e.payload, []
    except RuleError as e:
        return 400, {"error": str(e)}, []

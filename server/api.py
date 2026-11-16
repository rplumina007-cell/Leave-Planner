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
import base64
import binascii
import os
import re
import sys
import time
from html import escape as html_escape

import bulk_users
import domain
import mailer
import security
from domain import RuleError

IMPORT_MAX_FILE = 2 * 1024 * 1024      # a spreadsheet of logins

ROLES = ("employee", "manager", "admin")          # stored roles


def split_role(value):
    """A role chosen in Settings -> (stored role, senior flag). A senior manager is
    a manager with the senior designation: the same permissions, and the person
    other managers are mapped to."""
    if value == "senior_manager":
        return "manager", True
    if value in ROLES:
        return value, False
    raise ApiError(400, "Unknown role")
EMP_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
# Reachable while a user still has to replace their initial password.
PASSWORD_GATE_OPEN = {"/api/me", "/api/me/password", "/api/logout", "/api/password/forgot", "/api/password/reset"}

RESET_SENT = ("If that employee ID has an email address on file, a 6-digit code is on its way. "
              "It expires in %d minutes." % security.RESET_MINUTES)
RESET_BAD_CODE = "That code is wrong or has expired. Check the latest email, or ask for a new code."
# A code request takes at least this long whether or not an email goes out, so
# the timing doesn't reveal which employee IDs exist or have an address.
RESET_PAD_SECONDS = 2.5


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.payload = status, dict(error=message, **extra)


def public_user(u, with_email=False):
    """A login as the browser sees it. Email addresses go only to admins and to
    the person themselves."""
    out = {"empId": u["emp_id"], "name": u["name"], "role": u["role"],
           "managerEmpId": u["manager_emp_id"], "teams": u.get("teams") or [], "senior": bool(u.get("senior")),
           "mustChange": u["must_change"], "active": u["active"]}
    if with_email:
        out["email"] = u.get("email") or ""
    return out


def need(user, *roles):
    if not user:
        raise ApiError(401, "Please sign in")
    if roles and user["role"] not in roles:
        raise ApiError(403, "You don't have permission to do that")


def state_payload(app, user):
    users = app.db.users()
    st = app.db.read_state()
    out = domain.view_for(st, user, users)
    out["me"] = public_user(user, with_email=True)
    if user["role"] in ("manager", "admin"):
        out["team"] = [public_user(u, with_email=user["role"] == "admin") for u in users.values()
                       if domain.can_see(user, u["emp_id"], users)]
    if user["role"] == "admin":
        out["mail"] = {"ready": app.mail.available, "status": app.mail.describe()}
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
    return {"user": public_user(ctx["user"], with_email=True)}, []


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


# ------------------------------------------------------------------ forgotten password: a code by email
def _reset_email(u, code):
    first = (u["name"].split() or ["there"])[0]
    minutes = security.RESET_MINUTES
    subject = "Your Leave Planner password reset code"
    text = ("Hi %s,\n\nYour code to reset your Leave Planner password is:\n\n    %s\n\n"
            "It expires in %d minutes. Enter it on the sign-in page, under \"Forgot password?\".\n\n"
            "If you didn't ask for this, you can ignore this email - your password hasn't changed. "
            "Never share this code with anyone.\n" % (first, code, minutes))
    html = ('<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#2b2622;line-height:1.5">'
            '<p>Hi %s,</p><p>Your code to reset your Leave Planner password is:</p>'
            '<p style="font-size:28px;font-weight:700;letter-spacing:6px;margin:18px 0">%s</p>'
            '<p>It expires in %d minutes. Enter it on the sign-in page, under &ldquo;Forgot password?&rdquo;.</p>'
            '<p style="color:#6b625a;font-size:13px">If you didn\'t ask for this, you can ignore this email &mdash; '
            'your password hasn\'t changed. Never share this code with anyone.</p></div>'
            % (html_escape(first), code, minutes))
    return subject, text, html


def forgot_password(app, body, ctx):
    """Email a reset code to the address on file. The reply is the same whether
    or not the ID exists, is active or has an email - and takes as long - so
    this can't be used to find out who has an account."""
    started = time.perf_counter()                # precise on every OS (monotonic ticks in ~15 ms on Windows)
    if not app.mail.available:
        raise ApiError(503, "Password reset by email isn't set up yet. Ask your admin to reset your password.")
    emp = str(body.get("empId", "")).strip()
    if not emp or len(emp) > 64:
        raise ApiError(400, "Enter your employee ID")
    keys = ("reset-ip:" + ctx["ip"], "reset-id:" + emp.lower())
    if app.limiter.blocked(*keys):
        raise ApiError(429, "Too many code requests. Try again in 15 minutes.")
    app.limiter.fail(*keys)                      # counts requests, not failures
    u = app.db.user(emp)
    if u and u["active"] and u.get("email"):
        code = security.new_reset_code()
        app.db.put_reset_code(u["emp_id"], security.reset_code_hash(code), time.time() + security.RESET_MINUTES * 60)
        try:
            app.mail.send(u["email"], *_reset_email(u, code))
        except mailer.MailError as e:
            print("Password reset email for %s not sent: %s" % (u["emp_id"], e), file=sys.stderr)
            app.db.drop_reset_code(u["emp_id"])
    time.sleep(max(0.0, getattr(app, "reset_pad", RESET_PAD_SECONDS) - (time.perf_counter() - started)))
    return {"ok": True, "message": RESET_SENT}, []


def reset_with_code(app, body, ctx):
    """Set a new password with an emailed code. The code is checked before the
    new password, so password rules (which mention the person's name and ID)
    never tell a stranger anything about an account."""
    emp = str(body.get("empId", "")).strip()
    code = re.sub(r"\s", "", str(body.get("code", "")))
    nxt = body.get("password", "")
    ip_key = "code-ip:" + ctx["ip"]
    if app.limiter.blocked(ip_key):
        raise ApiError(429, "Too many wrong codes. Try again in 15 minutes.")
    u = app.db.user(emp) if emp else None
    live = app.db.use_reset_attempt(u["emp_id"]) if (u and u["active"] and re.fullmatch(r"\d{6}", code)) else None
    if not live or live["attempts"] > security.RESET_TRIES or not security.check_reset_code(code, live["code_hash"]):
        if live and live["attempts"] >= security.RESET_TRIES:
            app.db.drop_reset_code(u["emp_id"])  # used up: they need a new code
        app.limiter.fail(ip_key)
        raise ApiError(400, RESET_BAD_CODE)
    problem = security.password_problem(nxt, u["emp_id"], u["name"])
    if problem:
        raise ApiError(400, problem)            # the code stays valid for another go
    app.db.update_user(u["emp_id"], password=nxt, must_change=0)
    app.db.drop_reset_code(u["emp_id"])
    app.db.end_all_sessions(u["emp_id"])         # anyone signed in as them is signed out
    keys = security.LoginLimiter.login_keys(u["emp_id"], ctx["ip"])
    app.limiter.clear(keys[0], keys[2])          # a locked-out person can sign in straight away
    app.db.mutate(lambda st: domain.log(st, "password reset by email code", "users", u["emp_id"], u["name"], u["name"]))
    return {"ok": True}, []


def mail_test(app, body, ctx):
    """Admin: send a test email to their own address, to check the settings."""
    admin = ctx["user"]
    need(admin, "admin")
    if not app.mail.available:
        raise ApiError(400, "Email isn't set up yet — add the SMTP settings in Vercel, then redeploy.")
    to = admin.get("email")
    if not to:
        raise ApiError(400, "Add your own email address first (Settings → Users), then send a test.")
    try:
        app.mail.send(to, "Leave Planner test email",
                      "This is a test from Leave Planner. Password reset codes will reach people the same way.\n",
                      '<p style="font-family:Segoe UI,Arial,sans-serif">This is a test from Leave Planner. '
                      'Password reset codes will reach people the same way.</p>')
    except mailer.MailError as e:
        raise ApiError(502, str(e))
    return {"ok": True, "to": to}, []


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
    """A clean list of existing team names. All Teams means "sees everyone"; it can
    be held alongside named teams, which is what puts its holder on those teams'
    calendars - All Teams alone never makes anyone visible to others."""
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
    return out


def _check_email(app, value, emp_id=None):
    """A clean address (or "" for none) that no other login is using."""
    email = domain.clean_email(value)
    owner = domain.email_owner(app.db.users(), email, except_emp=emp_id)
    if owner:
        raise ApiError(409, "%s is already used by %s" % (email, owner))
    return email


def create_user(app, body, ctx):
    need(ctx["user"], "admin")
    emp = str(body.get("empId", "")).strip()
    name = " ".join(str(body.get("name", "")).split())
    role = body.get("role", "employee")
    if not EMP_ID_RE.match(emp):
        raise ApiError(400, "Employee ID may use letters, digits, dot, dash or underscore (max 32)")
    if not 2 <= len(name) <= 80:
        raise ApiError(400, "Enter the employee's full name")
    role, senior = split_role(role)
    if app.db.user(emp):
        raise ApiError(409, "A login for employee ID %s already exists" % emp)
    problem = security.password_problem(body.get("password", ""), emp, name)
    if problem:
        raise ApiError(400, problem)
    manager = _check_manager(app, body.get("managerEmpId"), emp)
    teams = _check_teams(app, body.get("teams"))
    email = _check_email(app, body.get("email"))
    if not email:
        raise ApiError(400, "Enter their email address — it's needed for password reset codes")
    app.db.create_user(emp, name, role, body["password"], manager, must_change=True, teams=teams, senior=senior,
                       email=email)
    label = "senior manager" if senior else role
    app.db.mutate(lambda st: domain.log(st, "login created (%s)" % label, "users", emp, name, ctx["user"]["name"]))
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
        role, senior = split_role(body["role"])
        if u["role"] == "admin" and role != "admin" and app.db.count_admins(exclude=emp) == 0:
            raise ApiError(400, "There must always be at least one active admin")
        changes["role"] = role
        changes["senior"] = senior
    if "managerEmpId" in body:
        changes["manager_emp_id"] = _check_manager(app, body["managerEmpId"], emp)
    if "teams" in body:
        changes["teams"] = _check_teams(app, body["teams"])
    if "email" in body:
        changes["email"] = _check_email(app, body["email"], emp)
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
    if "email" in changes and changes["email"] != u["email"]:
        app.db.drop_reset_code(emp)                # a code sent to the old address stops working
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


# ------------------------------------------------------------------ many logins from a spreadsheet (admin)
def spreadsheet_file(app, path, ctx):
    """(status, xlsx bytes or an error payload) - wsgi sends a 200 as a file.
    /api/users/template: blank sheet for new logins.
    /api/users/emails-sheet: every login with its email, to fill in."""
    user = ctx["user"]
    if not user:
        return 401, {"error": "Please sign in"}
    if user["must_change"]:
        return 403, {"error": "Please set a new password first", "mustChange": True}
    if user["role"] != "admin":
        return 403, {"error": "You don't have permission to do that"}
    if path == "/api/users/emails-sheet":
        return 200, bulk_users.emails_xlsx(app.db.users())
    return 200, bulk_users.template_xlsx(app.db.read_state().get("teams", []))


def _upload(body):
    """(filename, bytes) of a spreadsheet sent as base64."""
    try:
        raw = base64.b64decode(str(body.get("data") or ""), validate=True)
    except (ValueError, binascii.Error):
        raise ApiError(400, "Could not read that file")
    if not raw:
        raise ApiError(400, "That file is empty")
    if len(raw) > IMPORT_MAX_FILE:
        raise ApiError(413, "Files up to 2 MB, please — split larger lists")
    return str(body.get("filename") or "")[:120], raw


def _import_row(p):
    return {"row": p["row"], "empId": p["empId"], "name": p["name"],
            "role": "senior_manager" if p["senior"] else p["role"], "manager": p["manager"], "teams": p["teams"],
            "newTeams": p.get("newTeams", []), "email": p.get("email") or "",
            "passwordGiven": bool(p["password"]), "errors": p["errors"],
            "created": bool(p.get("created")), "oneTimePassword": p.get("oneTimePassword")}


def import_emails(app, body, ctx):
    """Preview (commit false) or save (commit true) emails for existing logins
    from the downloaded email sheet. Only rows that pass are saved."""
    admin = ctx["user"]
    need(admin, "admin")
    filename, raw = _upload(body)
    plan = bulk_users.plan_emails(bulk_users.read_email_rows(filename, raw), app.db.users())
    ready = [p for p in plan if not p["errors"]]
    changes = sum(1 for p in ready if p["status"] == "change")
    if not body.get("commit"):
        return {"rows": plan, "changes": changes}, []
    n = bulk_users.apply_emails(app.db, ready)
    for p in ready:
        if p["status"] == "change":
            app.db.drop_reset_code(p["empId"])
    if n:
        app.db.mutate(lambda st: domain.log(st, "emails updated (%d)" % n, "users", "", filename, admin["name"]))
    return {"rows": plan, "updated": n, "state": state_payload(app, admin)}, []


def import_users(app, body, ctx):
    """Preview (commit false) or create (commit true) logins from an .xlsx/.csv.
    Every row is checked like a single login; only rows that pass are created."""
    admin = ctx["user"]
    need(admin, "admin")
    filename, raw = _upload(body)
    rows = bulk_users.read_rows(filename, raw)
    plan = bulk_users.plan(rows, app.db.users(), app.db.read_state().get("teams", []))
    ready = [p for p in plan if not p["errors"]]
    if not body.get("commit"):
        return {"rows": [_import_row(p) for p in plan], "ready": len(ready), "newTeams": bulk_users.new_teams(ready)}, []
    done, made_teams = bulk_users.create(app.db, ready, admin["name"])
    if done:
        app.db.mutate(lambda st: domain.log(st, "logins imported (%d)" % len(done), "users", "", filename, admin["name"]))
    return {"rows": [_import_row(p) for p in plan], "created": len(done), "createdTeams": made_teams,
            "state": state_payload(app, admin)}, []


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
    ("POST", "/api/password/forgot"): forgot_password,
    ("POST", "/api/password/reset"): reset_with_code,
    ("POST", "/api/mail/test"): mail_test,
    ("GET", "/api/state"): get_state,
    ("POST", "/api/requests"): submit_request,
    ("POST", "/api/requests/decide"): decide_request,
    ("POST", "/api/requests/delete"): delete_request,
    ("POST", "/api/users"): create_user,
    ("POST", "/api/users/update"): update_user,
    ("POST", "/api/users/reset-password"): reset_password,
    ("POST", "/api/users/delete"): delete_user,
    ("POST", "/api/users/import"): import_users,
    ("POST", "/api/users/emails"): import_emails,
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
    if fn not in (login, forgot_password, reset_with_code) and not user and path != "/api/logout":
        return 401, {"error": "Please sign in"}, []
    try:
        payload, headers = fn(app, body, ctx)
        return 200, payload, headers
    except ApiError as e:
        return e.status, e.payload, []
    except RuleError as e:
        return 400, {"error": str(e)}, []

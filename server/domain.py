"""Business rules, mirrored from src/js/store.js.

The browser still runs the same maths for instant previews, but every change is
re-validated and applied here, so a user editing requests in their browser's dev
tools can never bypass a rule or a permission.
"""
import datetime as dt
import hashlib
import json

PLANNED_REASONS = ["Planned Leave", "Compoff", "My Day"]
STATUSES = ["Pending", "Approved", "Declined"]
EDITABLE_LISTS = ["leaveReasons", "wfhReasons", "durations"]
KINDS = ("leave", "wfh")
ALL_TEAMS = "All Teams"          # a membership that counts as every team


class RuleError(Exception):
    """A request broke a business rule. The message is safe to show the user."""


# ---------------------------------------------------------------- dates
def parse(iso):
    return dt.date.fromisoformat(str(iso)[:10])


def working_days_between(frm, to, holidays=()):
    """Mon-Fri dates in the range, skipping any public holidays given."""
    d, end, out = parse(frm), parse(to or frm), []
    if (end - d).days > 800:
        raise RuleError("Date range is too long")
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def is_half(duration):
    return str(duration).startswith("Half")


def compute_days(frm, to, duration, holidays=()):
    if is_half(duration):
        return 0.5 if working_days_between(frm, frm, holidays) else 0
    return len(working_days_between(frm, to or frm, holidays))


def cutoff_for(amount, unit, today=None):
    today = today or dt.date.today()
    amount = max(1, int(amount))
    if unit == "days":
        return (today - dt.timedelta(days=amount)).isoformat()
    if unit == "weeks":
        return (today - dt.timedelta(weeks=amount)).isoformat()
    if unit == "months":
        m = today.month - 1 - amount
        y, m = today.year + m // 12, m % 12 + 1
        day = min(today.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                              31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
        return dt.date(y, m, day).isoformat()
    if unit == "years":
        try:
            return today.replace(year=today.year - amount).isoformat()
        except ValueError:                       # 29 Feb
            return today.replace(year=today.year - amount, day=28).isoformat()
    raise RuleError("Unknown unit")


# ---------------------------------------------------------------- records
def next_id(records, kind):
    prefix, top = ("L" if kind == "leave" else "W"), 0
    for r in records:
        try:
            top = max(top, int(str(r.get("id", ""))[1:]))
        except ValueError:
            pass
    return "%s%04d" % (prefix, top + 1)


def find(state, kind, rec_id):
    if kind not in KINDS:
        raise RuleError("Unknown request type")
    for r in state[kind]:
        if r.get("id") == rec_id:
            return r
    raise RuleError("Request not found")


def build_request(state, kind, body, owner):
    """Validate a new request. `owner` is the user row it is filed for."""
    if kind not in KINDS:
        raise RuleError("Unknown request type")
    v = state["validation"]
    reasons = v["leaveReasons"] if kind == "leave" else v["wfhReasons"]
    reason, duration = str(body.get("reason", "")), str(body.get("duration", ""))
    if reason not in reasons:
        raise RuleError("Pick a reason from the list")
    if duration not in v["durations"]:
        raise RuleError("Pick a duration from the list")
    try:
        frm = parse(body.get("from")).isoformat()
        to = frm if is_half(duration) else parse(body.get("to") or frm).isoformat()
    except (ValueError, TypeError):
        raise RuleError("Invalid date")
    if to < frm:
        raise RuleError("End date is before start date")
    days = compute_days(frm, to, duration, holiday_dates(state))
    if not days:
        raise RuleError("That range has no working days (weekends and public holidays don't count)")
    return {
        "id": next_id(state[kind], kind), "empId": owner["emp_id"], "name": owner["name"],
        "from": frm, "to": to, "duration": duration, "days": days, "reason": reason,
        "status": "Pending", "comment": str(body.get("comment", ""))[:500].strip(),
        "autoLogged": False, "submittedAt": now_iso(),
    }


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def log(state, action, kind="", rec_id="", name="", actor=""):
    state.setdefault("audit", []).insert(0, {
        "at": now_iso(), "action": action, "kind": kind, "id": rec_id, "name": name, "actor": actor})
    del state["audit"][300:]


# ---------------------------------------------------------------- dropdowns
def option_usage(state, key, value):
    field = "duration" if key == "durations" else "reason"
    kinds = ["wfh"] if key == "wfhReasons" else ["leave"] if key == "leaveReasons" else list(KINDS)
    return sum(1 for k in kinds for r in state[k] if r.get(field) == value)


def add_option(state, key, value, planned=False):
    if key not in EDITABLE_LISTS:
        raise RuleError("That list is not editable")
    value = str(value or "").strip()
    if not value:
        raise RuleError("Value is required")
    lst = state["validation"].setdefault(key, [])
    if value in lst:
        raise RuleError('"%s" already exists' % value)
    if key == "durations" and not (value == "Full Day" or value.startswith("Half Day")):
        raise RuleError('A duration must be "Full Day" or start with "Half Day"')
    lst.append(value)
    if key == "leaveReasons" and planned:
        state["plannedReasons"].append(value)


def remove_option(state, key, value):
    if key not in EDITABLE_LISTS:
        raise RuleError("That list is not editable")
    lst = state["validation"].get(key, [])
    if len(lst) <= 1:
        raise RuleError("At least one value must remain")
    state["validation"][key] = [v for v in lst if v != value]
    state["plannedReasons"] = [v for v in state["plannedReasons"] if v != value]


# ---------------------------------------------------------------- teams
def add_team(state, name):
    name = " ".join(str(name or "").split())
    if not 1 <= len(name) <= 40:
        raise RuleError("A team name needs 1 to 40 characters")
    if name.lower() in [t.lower() for t in state.setdefault("teams", [])] + [ALL_TEAMS.lower()]:
        raise RuleError('"%s" already exists' % name)
    state["teams"].append(name)
    return name


def remove_team(state, name):
    if name not in state.get("teams", []):
        raise RuleError("Team not found")
    state["teams"] = [t for t in state["teams"] if t != name]


# ---------------------------------------------------------------- public holidays
#   Holidays count as non-working days for new requests and in dashboard maths.
#   Existing records keep the day counts they were created with.
def holiday_dates(state):
    return {h["date"] for h in state.get("holidays", [])}


def add_holiday(state, date, name):
    try:
        day = parse(date).isoformat()
    except (ValueError, TypeError):
        raise RuleError("Pick a valid date")
    name = " ".join(str(name or "").split())
    if not 1 <= len(name) <= 60:
        raise RuleError("Give the holiday a name (up to 60 characters)")
    if day in holiday_dates(state):
        raise RuleError("%s is already a holiday" % day)
    state.setdefault("holidays", []).append({"date": day, "name": name})
    state["holidays"].sort(key=lambda h: h["date"])
    return day


def remove_holiday(state, date):
    kept = [h for h in state.get("holidays", []) if h["date"] != date]
    if len(kept) == len(state.get("holidays", [])):
        raise RuleError("Holiday not found")
    state["holidays"] = kept


# ---------------------------------------------------------------- cleanup
def purge(state, cutoff, opts):
    removed = {"leave": 0, "wfh": 0}
    for kind in KINDS:
        if not opts.get(kind):
            continue
        keep = []
        for r in state[kind]:
            old = (r.get("to") or r.get("from")) < cutoff
            protected = opts.get("keepPending") and r.get("status") == "Pending"
            if old and not protected:
                removed[kind] += 1
            else:
                keep.append(r)
        state[kind] = keep
    return removed


# ---------------------------------------------------------------- sheet sync
def load_seed(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    return json.loads(raw), hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def empty_seed():
    """Used when no sheet has been imported yet (e.g. a fresh clone): no records,
    and the dropdown values the tracker sheet's Validation tab started with."""
    return {
        "leave": [], "wfh": [], "generatedAt": "",
        "validation": {
            "leaveReasons": ["Planned Leave", "Compoff", "My Day", "Unplanned"],
            "durations": ["Full Day", "Half Day - 1st Half", "Half Day - 2nd Half"],
            "statuses": list(STATUSES),
            "wfhReasons": ["Personal Work", "Not Feeling Well", "Travel (Out of City)", "Emergency", "Other"],
        },
    }


def fresh_state(seed, seed_hash):
    return {
        "leave": seed["leave"], "wfh": seed["wfh"], "validation": seed["validation"],
        "plannedReasons": list(PLANNED_REASONS), "audit": [], "teams": [], "holidays": [],
        "seedHash": seed_hash, "seedGeneratedAt": seed.get("generatedAt", ""),
    }


def sync_from_seed(state, seed, seed_hash):
    """Sheet is the source of truth for its own rows; app-only work is kept.
    Same rules as the browser version (see README: Refreshing from the sheet)."""
    key = lambda r: "%s|%s|%s" % (r["name"], r["from"], r["to"])
    s = {"added": 0, "updated": 0, "removed": 0, "keptLocal": 0, "keptDecisions": 0}
    for kind in KINDS:
        local = {key(r): r for r in state[kind]}
        fresh = json.loads(json.dumps(seed[kind]))
        sheet_keys = set()
        for r in fresh:
            k = key(r)
            sheet_keys.add(k)
            old = local.get(k)
            if not old:
                s["added"] += 1
            elif old.get("decidedAt") and r["status"] == "Pending" and old["status"] != "Pending":
                for f in ("status", "decidedBy", "decidedAt", "comment"):
                    if old.get(f):
                        r[f] = old[f]
                s["keptDecisions"] += 1
            elif any(old.get(f) != r.get(f) for f in ("status", "days", "reason", "duration")):
                s["updated"] += 1
        local_only = [r for r in state[kind] if key(r) not in sheet_keys and r.get("submittedAt")]
        s["removed"] += sum(1 for r in state[kind] if key(r) not in sheet_keys and not r.get("submittedAt"))
        state[kind] = fresh
        for r in local_only:
            r["id"] = next_id(state[kind], kind)
            state[kind].insert(0, r)
        s["keptLocal"] += len(local_only)
    for k, vals in seed["validation"].items():
        extra = [v for v in state["validation"].get(k, []) if v not in vals]
        state["validation"][k] = list(vals) if k == "statuses" else list(vals) + extra
    state["seedHash"], state["seedGeneratedAt"] = seed_hash, seed.get("generatedAt", "")
    return s


# ---------------------------------------------------------------- permissions
#   Teams decide who sees whom; the reporting chain decides who a manager acts for.
def shares_team(viewer, emp_id, users):
    """Whether emp_id is in one of the viewer's teams. All Teams on either side
    matches every team. Someone with no team - or no login - matches none."""
    mine = set(viewer.get("teams") or [])
    theirs = set((users.get(emp_id) or {}).get("teams") or [])
    if ALL_TEAMS in mine:
        return True
    return bool(mine) and (ALL_TEAMS in theirs or bool(mine & theirs))


def can_see(viewer, emp_id, users):
    """Whether anything about someone reaches the viewer. Admin: everyone.
    Manager: themselves and people sharing one of their teams. Employee: the same,
    but fellow employees only - managers' and admins' leave, WFH and names never
    reach an employee. (People who only exist in the records have no role and
    count as employees.) Nothing at all about anyone else."""
    if viewer["role"] == "admin" or emp_id == viewer["emp_id"]:
        return True
    if viewer["role"] == "employee" and (users.get(emp_id) or {}).get("role") in ("manager", "admin"):
        return False
    return shares_team(viewer, emp_id, users)


def sees_in_full(viewer, emp_id, users):
    """Admin: everyone. Manager: everyone they can see. Employee: themselves -
    teammates arrive without reason or comments (see view_for)."""
    if viewer["role"] == "admin" or emp_id == viewer["emp_id"]:
        return True
    return viewer["role"] == "manager" and can_see(viewer, emp_id, users)


def reporting_chain(manager, users):
    """Employee IDs below a manager: people assigned to them, people assigned to
    those managers, and so on."""
    ids, frontier = {manager["emp_id"]}, [manager["emp_id"]]
    while frontier:                     # skipping ids already seen also stops loops (A -> B -> A)
        below = [u["emp_id"] for u in users.values()
                 if u.get("manager_emp_id") in frontier and u["emp_id"] not in ids]
        ids.update(below)
        frontier = below
    return ids - {manager["emp_id"]}


def manages(viewer, emp_id, users):
    """A manager acts for someone - files, decides, deletes - only when that person
    is below them in the reporting chain AND in one of their teams."""
    return (viewer["role"] == "manager" and emp_id != viewer["emp_id"]
            and shares_team(viewer, emp_id, users) and emp_id in reporting_chain(viewer, users))


def roster(state, users):
    """Everyone the tracker knows, {emp_id: name}: active logins plus anyone with
    records - so people from the sheet who have no login yet still count."""
    out = {}
    for kind in KINDS:
        for r in state[kind]:
            if r.get("empId"):
                out.setdefault(r["empId"], r.get("name") or r["empId"])
    for u in users.values():
        if u["active"] or u["emp_id"] in out:
            out[u["emp_id"]] = u["name"]
    return out


def can_decide(viewer, rec, users):
    """Admin: anything. Manager: requests from people they manage (see manages),
    never their own. Employee: nothing."""
    return viewer["role"] == "admin" or manages(viewer, rec.get("empId"), users)


def can_file_for(viewer, emp_id, users):
    """Admin: anyone. Manager: themselves and active people they manage.
    Employee: themselves."""
    if emp_id == viewer["emp_id"] or viewer["role"] == "admin":
        return True
    owner = users.get(emp_id)
    return bool(owner and owner["active"] and manages(viewer, emp_id, users))


def can_delete(viewer, rec, users):
    """Admin: any record. Manager: any record of people they manage.
    Everyone: their own request while it is pending or approved (a declined one
    stays as a record)."""
    if viewer["role"] == "admin":
        return True
    if rec.get("empId") == viewer["emp_id"]:
        return rec.get("status") in ("Pending", "Approved")
    return manages(viewer, rec.get("empId"), users)


def view_for(state, viewer, users):
    """The slice of state a viewer may see: only people sharing one of their teams
    (everyone for an admin). Managers see those people in full. Employees see
    only their own details: reasons, comments and approver are stripped from
    teammates' records (the reason also drives the planned/unplanned colour, so
    it has to go too)."""
    out = {k: state[k] for k in ("validation", "plannedReasons", "seedGeneratedAt")}
    out["holidays"] = state.get("holidays", [])
    out["people"] = sorted(({"empId": emp_id, "name": name, "full": sees_in_full(viewer, emp_id, users),
                             "canApply": can_file_for(viewer, emp_id, users)}
                            for emp_id, name in roster(state, users).items()
                            if can_see(viewer, emp_id, users)),
                           key=lambda p: p["name"].lower())
    for kind in KINDS:
        rows = []
        for r in state[kind]:
            if not can_see(viewer, r.get("empId"), users):
                continue
            if not sees_in_full(viewer, r.get("empId"), users):
                r = {f: r.get(f) for f in ("id", "empId", "name", "from", "to", "duration", "days", "status")}
                r.update(reason=None, comment="", redacted=True)
            else:
                r = dict(r, canDecide=can_decide(viewer, r, users), canDelete=can_delete(viewer, r, users))
            rows.append(r)
        out[kind] = rows
    if viewer["role"] == "admin":
        out["audit"] = state.get("audit", [])[:100]
    if viewer["role"] == "admin" or (viewer["role"] == "manager" and ALL_TEAMS in (viewer.get("teams") or [])):
        out["teams"] = state.get("teams", [])       # the team filter's choices
    return out

"""Adding many logins at once from a spreadsheet: the downloadable Excel template,
and reading a filled-in copy back (.xlsx or .csv). Also the email sheet: every
existing login with its email, for filling in many addresses at once.

Standard library only. The file comes from a user, so reading is defensive:
the upload is size-capped by the API, every part of the workbook is capped
again after unzipping, DTDs/entities are refused, and at most MAX_ROWS people
are read. Nothing is created until the admin confirms a preview.
"""
import csv
import io
import re
import secrets
import string
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import domain
import security
from domain import RuleError

MAX_ROWS = 500
MAX_PART = 8 * 1024 * 1024          # largest single part of an .xlsx, once unzipped
EMP_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAIN, DOC_RELS, PKG_RELS = "{%s}" % MAIN_NS, "{%s}" % DOC_RELS_NS, "{%s}" % PKG_RELS_NS

# (key, header in the template, column width)
COLUMNS = [
    ("empId", "Employee ID", 16),
    ("name", "Full name", 28),
    ("role", "Role", 18),
    ("manager", "Manager employee ID", 22),
    ("teams", "Teams", 32),
    ("email", "Email (optional)", 32),
    ("password", "One-time password (optional)", 30),
]
EMAIL_COLUMNS = [
    ("empId", "Employee ID", 16),
    ("name", "Full name (for reference)", 30),
    ("manager", "Manager (for reference)", 30),
    ("email", "Email", 36),
]
# Header spellings accepted when reading back (lower-case, letters only)
EMAIL_WORDS = ("emailoptional", "email", "emailaddress", "emailid", "mail", "workemail")
ALIASES = {
    "empId": ("employeeid", "empid", "employeeno", "employeenumber", "id"),
    "name": ("fullname", "name", "username", "usernamefullname", "employeename"),
    "role": ("role", "designation"),
    "manager": ("manageremployeeid", "managerid", "manager", "manageremployee"),
    "teams": ("teams", "team"),
    "email": EMAIL_WORDS,
    "password": ("onetimepasswordoptional", "onetimepassword", "password", "temporarypassword"),
}
EMAIL_ALIASES = {
    "empId": ALIASES["empId"],
    "name": ALIASES["name"] + ("fullnameforreference",),
    # Read but never applied: the sheet only changes emails, never who manages whom.
    "manager": ALIASES["manager"] + ("managerforreference", "managername", "reportsto"),
    "email": EMAIL_WORDS,
}
ROLE_WORDS = {
    "": ("employee", False), "employee": ("employee", False),
    "manager": ("manager", False),
    "seniormanager": ("manager", True), "srmanager": ("manager", True),
    "admin": ("admin", False), "administrator": ("admin", False),
}
ROLE_CHOICES = "Employee,Manager,Senior manager,Admin"


# ---------------------------------------------------------------- the template
def _col(i):
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i]


def _cell(ref, text, style):
    return '<c r="%s" t="inlineStr" s="%d"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, escape(text))


HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _data_sheet(columns, data_rows=(), role_dropdown=False):
    """A sheet with a frozen heading row, text-formatted columns (so IDs keep
    leading zeros) and, optionally, rows already filled in."""
    cols = "".join('<col min="%d" max="%d" width="%d" style="1" customWidth="1"/>' % (i + 1, i + 1, c[2])
                   for i, c in enumerate(columns))
    rows = ['<row r="1">%s</row>' % "".join(_cell("%s1" % _col(i), c[1], 2) for i, c in enumerate(columns))]
    for n, values in enumerate(data_rows, start=2):
        rows.append('<row r="%d">%s</row>' % (n, "".join(
            _cell("%s%d" % (_col(i), n), str(v), 1) for i, v in enumerate(values) if v)))
    dropdown = ('<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" '
                'errorTitle="Role" error="Choose Employee, Manager, Senior manager or Admin" sqref="C2:C%d">'
                '<formula1>"%s"</formula1></dataValidation></dataValidations>' % (MAX_ROWS + 1, ROLE_CHOICES)
                if role_dropdown else "")
    return (HEAD + '<worksheet xmlns="%s" xmlns:r="%s">' % (MAIN_NS, DOC_RELS_NS) +
            '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/><cols>%s</cols>' % cols +
            '<sheetData>%s</sheetData>%s</worksheet>' % ("".join(rows), dropdown))


def _guide_sheet(lines):
    rows = "".join('<row r="%d">%s</row>' % (i + 1, _cell("A%d" % (i + 1), text, style) if text else "")
                   for i, (text, style) in enumerate(lines))
    return (HEAD + '<worksheet xmlns="%s"><cols><col min="1" max="1" width="120" customWidth="1"/></cols>' % MAIN_NS +
            '<sheetData>%s</sheetData></worksheet>' % rows)


def template_xlsx(teams):
    """The Excel template: a Users sheet (header, text-formatted columns so IDs
    keep leading zeros, a Role dropdown) and an Instructions sheet that lists
    the teams that exist right now."""
    lines = [
        ("How to fill in the Users sheet", 2),
        ("", 0),
        ("One row per person, up to %d per file. Keep row 1 (the headings) as it is." % MAX_ROWS, 0),
        ("Employee ID — required and new: letters, digits, dot, dash or underscore (up to 32).", 0),
        ("Full name — required. This is also their user name.", 0),
        ("Role — Employee, Manager, Senior manager or Admin (pick from the list). Blank means Employee.", 0),
        ("Manager employee ID — optional: an existing Manager, Senior manager or Admin, or someone added in this same sheet with one of those roles.", 0),
        ("Teams — optional: one or more team names separated by commas, or All Teams. A team that doesn't exist yet is created when you import (up to 40 characters; you'll see it in the preview first).", 0),
        ("Email — optional: where their password reset codes go, so they can reset a forgotten password themselves. Each address can belong to one login only.", 0),
        ("One-time password — optional: at least 10 characters, not a common password, and not containing the person's name or ID. Leave blank to have one generated.", 0),
        ("Everyone signs in with their employee ID and chooses their own password at first sign-in.", 0),
        ("Save as .xlsx (or .csv), then choose the file in Settings → Users. You'll see a preview before anything is created.", 0),
        ("", 0),
        ("Teams that exist right now:", 2),
    ] + [("  " + t, 0) for t in teams or []] + ([("  (none yet — name new ones in the Teams column and they'll be created)", 0)] if not teams else [])
    return _workbook("Users", _data_sheet(COLUMNS, role_dropdown=True), _guide_sheet(lines))


def emails_xlsx(users):
    """Every login with their manager's name and current email, to fill in and
    upload again."""
    people = sorted(users.values(), key=lambda u: u["name"].lower())

    def manager_name(u):
        m = users.get(u.get("manager_emp_id") or "")
        return m["name"] if m else ""

    lines = [
        ("How to fill in the Emails sheet", 2),
        ("", 0),
        ("Every login is listed with their manager and the email on file now. Type or paste addresses in the Email column.", 0),
        ("Password reset codes go to this address, so people can reset a forgotten password themselves.", 0),
        ("Each address can belong to one login only.", 0),
        ("Leave an email blank to keep what's on file. To remove an address, edit that person in Settings → Users.", 0),
        ("Only the Email column is used. Names and managers are there for reference - changing them here changes "
         "nothing (assign managers in Settings → Users). Delete rows you aren't changing if you like.", 0),
        ("Save as .xlsx (or .csv), then choose the file under Update emails in Settings → Users. You'll see a preview first.", 0),
        ("", 0),
        ("This file holds people's email addresses - delete it once you've uploaded it.", 2),
    ]
    return _workbook("Emails", _data_sheet(EMAIL_COLUMNS, [(u["emp_id"], u["name"], manager_name(u), u.get("email") or "")
                                                           for u in people]),
                     _guide_sheet(lines))


def _workbook(first_sheet, data_xml, guide_xml):
    head = HEAD
    styles = (head + '<styleSheet xmlns="%s">' % MAIN_NS +
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF0EBE2"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyNumberFormat="1"/>'
              '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')

    sheet_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
    parts = [
        ("[Content_Types].xml", head +
         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/>'
         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
         '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="%s"/>'
         '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="%s"/>'
         '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
         '</Types>' % (sheet_type, sheet_type)),
        ("_rels/.rels", head + '<Relationships xmlns="%s">' % PKG_RELS_NS +
         '<Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % DOC_RELS_NS),
        ("xl/workbook.xml", head + '<workbook xmlns="%s" xmlns:r="%s"><sheets>' % (MAIN_NS, DOC_RELS_NS) +
         '<sheet name="%s" sheetId="1" r:id="rId1"/><sheet name="Instructions" sheetId="2" r:id="rId2"/></sheets></workbook>'
         % first_sheet),
        ("xl/_rels/workbook.xml.rels", head + '<Relationships xmlns="%s">' % PKG_RELS_NS +
         '<Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>' % DOC_RELS_NS +
         '<Relationship Id="rId2" Type="%s/worksheet" Target="worksheets/sheet2.xml"/>' % DOC_RELS_NS +
         '<Relationship Id="rId3" Type="%s/styles" Target="styles.xml"/></Relationships>' % DOC_RELS_NS),
        ("xl/worksheets/sheet1.xml", data_xml),
        ("xl/worksheets/sheet2.xml", guide_xml),
        ("xl/styles.xml", styles),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            z.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------- reading a file back
def read_rows(filename, raw):
    """[{row, empId, name, role, manager, teams, email, password}] from an .xlsx or .csv."""
    return _records(_grid(raw), ALIASES, ("empId", "name"),
                    "Couldn't find the Employee ID and Full name columns — start from the template")


def read_email_rows(filename, raw):
    """[{row, empId, name, email}] from a filled-in email sheet."""
    return _records(_grid(raw), EMAIL_ALIASES, ("empId", "email"),
                    "Couldn't find the Employee ID and Email columns — start from the downloaded email sheet")


def _grid(raw):
    if raw[:2] == b"PK":
        return _xlsx_grid(raw)
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise RuleError("That's an old-style .xls file — save it as .xlsx (or .csv) and try again")
    return _csv_grid(raw)


def _csv_grid(raw):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuleError("Couldn't read that file — save it as .xlsx, or as a UTF-8 .csv")
    if "\x00" in text:
        raise RuleError("That file isn't a spreadsheet — use the .xlsx template or a .csv")
    grid = []
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        grid.append((i + 1, [c.strip() for c in row]))
        if len(grid) > MAX_ROWS + 50:
            break
    return grid


def _xml(z, name):
    if z.getinfo(name).file_size > MAX_PART:
        raise RuleError("That spreadsheet is too large")
    data = z.read(name)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise RuleError("That file isn't a normal spreadsheet")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise RuleError("That spreadsheet looks damaged — re-save it in Excel and try again")


def _first_sheet(z, names):
    wb = _xml(z, "xl/workbook.xml")
    first = wb.find(MAIN + "sheets/" + MAIN + "sheet")
    if first is not None and "xl/_rels/workbook.xml.rels" in names:
        rid = first.get(DOC_RELS + "id")
        for rel in _xml(z, "xl/_rels/workbook.xml.rels").findall(PKG_RELS + "Relationship"):
            if rel.get("Id") == rid:
                target = (rel.get("Target") or "").lstrip("/")
                path = target if target.startswith("xl/") else "xl/" + target
                if path in names:
                    return path
    if "xl/worksheets/sheet1.xml" in names:
        return "xl/worksheets/sheet1.xml"
    raise RuleError("Couldn't find a worksheet in that file")


def _col_index(ref):
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _value(c, shared):
    t = c.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(MAIN + "t")).strip()
    v = c.find(MAIN + "v")
    text = (v.text or "") if v is not None else ""
    if t == "s":
        try:
            return shared[int(text)].strip()
        except (ValueError, IndexError):
            return ""
    if t == "b":
        return "TRUE" if text == "1" else "FALSE"
    if t in ("str", "e"):
        return text.strip()
    try:                                        # whole numbers (employee IDs) come back without ".0"
        f = float(text)
        if f.is_integer() and abs(f) < 1e15:
            return str(int(f))
    except ValueError:
        pass
    return text.strip()


def _xlsx_grid(raw):
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise RuleError("That file isn't a valid .xlsx spreadsheet")
    with z:
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise RuleError("That file isn't an Excel workbook")
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(z, "xl/sharedStrings.xml").findall(MAIN + "si"):
                shared.append("".join(t.text or "" for t in si.iter(MAIN + "t")))
        grid = []
        for row in _xml(z, _first_sheet(z, names)).iter(MAIN + "row"):
            cells = {}
            for c in row.findall(MAIN + "c"):
                col = _col_index(c.get("r")) if c.get("r") else len(cells)
                if 0 <= col < 64:
                    cells[col] = _value(c, shared)
            width = max(cells) + 1 if cells else 0
            grid.append((int(row.get("r") or len(grid) + 1), [cells.get(i, "") for i in range(width)]))
            if len(grid) > MAX_ROWS + 50:
                break
        return grid


def _key(header, aliases):
    h = re.sub(r"[^a-z]", "", str(header).lower())
    for key, names in aliases.items():
        if h in names:
            return key
    return None


def _records(grid, aliases, required, missing):
    rows = [(n, cells) for n, cells in grid if any(str(c).strip() for c in cells)]
    if not rows:
        raise RuleError("The file has no rows")
    header = rows[0][1]
    cols = {}
    for i, h in enumerate(header):
        k = _key(h, aliases)
        if k and k not in cols:
            cols[k] = i
    if any(k not in cols for k in required):
        raise RuleError(missing)
    data = rows[1:]
    if not data:
        raise RuleError("The sheet has headings but no people yet")
    if len(data) > MAX_ROWS:
        raise RuleError("Up to %d people per file — split it into smaller files" % MAX_ROWS)
    out = []
    for n, cells in data:
        rec = {"row": n}
        for k in aliases:
            i = cols.get(k)
            rec[k] = str(cells[i]).strip() if i is not None and i < len(cells) else ""
        out.append(rec)
    return out


# ---------------------------------------------------------------- checking and creating
def plan(rows, users, known_teams):
    """Check every row against the rules for a single login, plus the file as a
    whole (duplicate IDs; managers defined in the same file). All Teams may be
    listed alongside named teams. Each result has
    an "errors" list - empty means it's ready to create - and "newTeams": teams
    it names that don't exist yet, which are created with it. A new team keeps
    the spelling it first appears with; later rows match it in any case."""
    by_lower = {t.lower(): t for t in known_teams}
    new_lower = set()
    out, seen, emails_seen = [], {}, {}
    for r in rows:
        errs = []
        emp = r["empId"]
        name = " ".join(r["name"].split())
        role, senior = ROLE_WORDS.get(re.sub(r"[^a-z]", "", r["role"].lower()), (None, False))
        if role is None:
            errs.append('Role must be Employee, Manager, Senior manager or Admin (not "%s")' % r["role"])
        if not EMP_ID_RE.match(emp):
            errs.append("Employee ID may use letters, digits, dot, dash or underscore (max 32)")
        elif emp in users:
            errs.append("A login for %s already exists" % emp)
        elif emp in seen:
            errs.append("Same employee ID as row %d" % seen[emp])
        else:
            seen[emp] = r["row"]
        if not 2 <= len(name) <= 80:
            errs.append("Enter the full name")
        teams, new_here, too_long = [], [], []
        for part in re.split(r"[,;]", r["teams"]):
            t = " ".join(part.split())
            if not t:
                continue
            low = t.lower()
            if low == domain.ALL_TEAMS.lower():
                teams.append(domain.ALL_TEAMS)
            elif low in by_lower:
                teams.append(by_lower[low])
                if low in new_lower:
                    new_here.append(by_lower[low])
            elif len(t) > 40:
                too_long.append(t)
            else:                                   # a new team, created with this row
                by_lower[low] = t
                new_lower.add(low)
                teams.append(t)
                new_here.append(t)
        if too_long:
            errs.append("Team name%s longer than 40 characters: %s" % ("s" if len(too_long) > 1 else "", ", ".join(too_long)))
        teams, new_here = list(dict.fromkeys(teams)), list(dict.fromkeys(new_here))
        if r["password"]:
            problem = security.password_problem(r["password"], emp, name)
            if problem:
                errs.append(problem)
        email = _email_cell(r.get("email", ""), errs)
        if email:
            owner = domain.email_owner(users, email)
            if owner:
                errs.append("%s is already used by %s" % (email, owner))
            elif email in emails_seen:
                errs.append("Same email as row %d" % emails_seen[email])
            else:
                emails_seen[email] = r["row"]
        out.append({"row": r["row"], "empId": emp, "name": name, "role": role or "", "senior": senior,
                    "manager": r["manager"], "teams": teams, "newTeams": new_here, "email": email,
                    "password": r["password"], "errors": errs})

    in_file = {p["empId"]: p for p in out}
    for p in out:
        m = p["manager"]
        if not m:
            continue
        if m == p["empId"]:
            p["errors"].append("Someone can't be their own manager")
        elif m in users:
            u = users[m]
            if not u["active"] or u["role"] not in ("manager", "admin"):
                p["errors"].append("Manager %s must be an active Manager, Senior manager or Admin" % m)
        elif m in in_file:
            q = in_file[m]
            if q["role"] not in ("manager", "admin"):
                p["errors"].append("Manager %s must have the role Manager, Senior manager or Admin" % m)
            elif q["errors"]:
                p["errors"].append("Manager %s's own row has problems" % m)
        else:
            p["errors"].append("No login or row with employee ID %s for the manager" % m)
    return out


def temp_password(emp_id, name):
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(14))
        if not security.password_problem(pw, emp_id, name):
            return pw


def new_teams(rows):
    """New teams named by these rows, first spelling first, no repeats."""
    return list(dict.fromkeys(t for p in rows for t in p.get("newTeams", [])))


def create(db, ready, actor=""):
    """Create the teams the ready rows introduce, then the rows, then link
    managers once everyone exists. Each created row gets a oneTimePassword -
    given in the file, or generated. Returns (created rows, created teams)."""
    wanted, made_teams = new_teams(ready), []
    if wanted:
        def add_teams(st):
            for t in wanted:
                if t.lower() in {x.lower() for x in st.get("teams", [])}:
                    continue                          # appeared since the preview
                domain.add_team(st, t)
                made_teams.append(t)
            if made_teams:
                domain.log(st, "teams created by import (%d)" % len(made_teams), "teams", "", ", ".join(made_teams)[:200], actor)
        db.mutate(add_teams)
        canon = {t.lower(): t for t in db.read_state().get("teams", [])}
        for p in ready:                               # match whatever spelling is stored now
            p["teams"] = [t if t == domain.ALL_TEAMS else canon.get(t.lower(), t) for t in p["teams"]]
    done = []
    for p in ready:
        pw = p["password"] or temp_password(p["empId"], p["name"])
        try:
            db.create_user(p["empId"], p["name"], p["role"], pw, None, must_change=True, teams=p["teams"], senior=p["senior"],
                           email=p.get("email") or "")
        except Exception:                        # e.g. the same ID was created a moment ago
            p["errors"].append("Couldn't be created — the employee ID may have just been taken")
            continue
        p["created"], p["oneTimePassword"] = True, pw
        done.append(p)
    for p in done:
        if p["manager"]:
            if db.user(p["manager"]):
                db.update_user(p["empId"], manager_emp_id=p["manager"])
            else:
                p["errors"].append("Created without a manager — %s wasn't created" % p["manager"])
    return done, made_teams


# ---------------------------------------------------------------- emails for existing logins
def _email_cell(value, errs):
    try:
        return domain.clean_email(value)
    except RuleError as e:
        errs.append(str(e))
        return ""


def plan_emails(rows, users):
    """Check an email sheet against today's logins. Each row comes back with a
    status - "change", "same", or "blank" (left as it is) - and errors. Each
    address may belong to one login, judged on how things would look after the
    whole file is applied, so two people can swap addresses in one upload."""
    out, seen = [], {}
    for r in rows:
        errs = []
        emp = r["empId"]
        u = users.get(emp)
        if not u:
            errs.append("No login with employee ID %s" % emp if emp else "Employee ID is missing")
        elif emp in seen:
            errs.append("Same employee ID as row %d" % seen[emp])
        else:
            seen[emp] = r["row"]
        email = _email_cell(r.get("email", ""), errs)
        current = (u or {}).get("email") or ""
        status = "blank" if not email else "same" if email == current else "change"
        out.append({"row": r["row"], "empId": emp, "name": (u or {}).get("name") or r.get("name") or "",
                    "current": current, "email": email, "status": status, "errors": errs})

    final = {emp: u.get("email") or "" for emp, u in users.items()}
    for p in out:
        if not p["errors"] and p["status"] == "change":
            final[p["empId"]] = p["email"]
    for p in out:
        if not p["errors"] and p["status"] == "change":
            others = [emp for emp, e in final.items() if e == p["email"] and emp != p["empId"]]
            if others:
                p["errors"].append("%s would also belong to %s" % (p["email"], ", ".join(sorted(others))))
    return out


def apply_emails(db, ready):
    """Save the rows that change an address. Returns how many changed."""
    n = 0
    for p in ready:
        if p["status"] == "change":
            db.update_user(p["empId"], email=p["email"])
            n += 1
    return n

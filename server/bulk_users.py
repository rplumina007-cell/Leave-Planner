"""Adding many logins at once from a spreadsheet: the downloadable Excel template,
and reading a filled-in copy back (.xlsx or .csv).

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
    ("password", "One-time password (optional)", 30),
]
# Header spellings accepted when reading back (lower-case, letters only)
ALIASES = {
    "empId": ("employeeid", "empid", "employeeno", "employeenumber", "id"),
    "name": ("fullname", "name", "username", "usernamefullname", "employeename"),
    "role": ("role", "designation"),
    "manager": ("manageremployeeid", "managerid", "manager", "manageremployee"),
    "teams": ("teams", "team"),
    "password": ("onetimepasswordoptional", "onetimepassword", "password", "temporarypassword"),
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


def template_xlsx(teams):
    """The Excel template: a Users sheet (header, text-formatted columns so IDs
    keep leading zeros, a Role dropdown) and an Instructions sheet that lists
    the teams that exist right now."""
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    cols = "".join('<col min="%d" max="%d" width="%d" style="1" customWidth="1"/>' % (i + 1, i + 1, c[2])
                   for i, c in enumerate(COLUMNS))
    header = "".join(_cell("%s1" % _col(i), c[1], 2) for i, c in enumerate(COLUMNS))
    users = (head + '<worksheet xmlns="%s" xmlns:r="%s">' % (MAIN_NS, DOC_RELS_NS) +
             '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15"/><cols>%s</cols>' % cols +
             '<sheetData><row r="1">%s</row></sheetData>' % header +
             '<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" '
             'errorTitle="Role" error="Choose Employee, Manager, Senior manager or Admin" sqref="C2:C%d">'
             '<formula1>"%s"</formula1></dataValidation></dataValidations></worksheet>' % (MAX_ROWS + 1, ROLE_CHOICES))

    lines = [
        ("How to fill in the Users sheet", 2),
        ("", 0),
        ("One row per person, up to %d per file. Keep row 1 (the headings) as it is." % MAX_ROWS, 0),
        ("Employee ID — required and new: letters, digits, dot, dash or underscore (up to 32).", 0),
        ("Full name — required. This is also their user name.", 0),
        ("Role — Employee, Manager, Senior manager or Admin (pick from the list). Blank means Employee.", 0),
        ("Manager employee ID — optional: an existing Manager, Senior manager or Admin, or someone added in this same sheet with one of those roles.", 0),
        ("Teams — optional: one or more team names separated by commas, or All Teams. Teams must already exist in Settings → Teams.", 0),
        ("One-time password — optional: at least 10 characters, not a common password, and not containing the person's name or ID. Leave blank to have one generated.", 0),
        ("Everyone signs in with their employee ID and chooses their own password at first sign-in.", 0),
        ("Save as .xlsx (or .csv), then choose the file in Settings → Users. You'll see a preview before anything is created.", 0),
        ("", 0),
        ("Teams that exist right now:", 2),
    ] + [("  " + t, 0) for t in teams or []] + ([("  (none yet — create them in Settings → Teams)", 0)] if not teams else [])
    rows = "".join('<row r="%d">%s</row>' % (i + 1, _cell("A%d" % (i + 1), text, style) if text else "")
                   for i, (text, style) in enumerate(lines))
    guide = (head + '<worksheet xmlns="%s"><cols><col min="1" max="1" width="120" customWidth="1"/></cols>' % MAIN_NS +
             '<sheetData>%s</sheetData></worksheet>' % rows)

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
         '<sheet name="Users" sheetId="1" r:id="rId1"/><sheet name="Instructions" sheetId="2" r:id="rId2"/></sheets></workbook>'),
        ("xl/_rels/workbook.xml.rels", head + '<Relationships xmlns="%s">' % PKG_RELS_NS +
         '<Relationship Id="rId1" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>' % DOC_RELS_NS +
         '<Relationship Id="rId2" Type="%s/worksheet" Target="worksheets/sheet2.xml"/>' % DOC_RELS_NS +
         '<Relationship Id="rId3" Type="%s/styles" Target="styles.xml"/></Relationships>' % DOC_RELS_NS),
        ("xl/worksheets/sheet1.xml", users),
        ("xl/worksheets/sheet2.xml", guide),
        ("xl/styles.xml", styles),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            z.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------- reading a file back
def read_rows(filename, raw):
    """[{row, empId, name, role, manager, teams, password}] from an .xlsx or .csv."""
    if raw[:2] == b"PK":
        grid = _xlsx_grid(raw)
    elif raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise RuleError("That's an old-style .xls file — save it as .xlsx (or .csv) and try again")
    else:
        grid = _csv_grid(raw)
    return _records(grid)


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


def _key(header):
    h = re.sub(r"[^a-z]", "", str(header).lower())
    for key, names in ALIASES.items():
        if h in names:
            return key
    return None


def _records(grid):
    rows = [(n, cells) for n, cells in grid if any(str(c).strip() for c in cells)]
    if not rows:
        raise RuleError("The file has no rows")
    header = rows[0][1]
    cols = {}
    for i, h in enumerate(header):
        k = _key(h)
        if k and k not in cols:
            cols[k] = i
    if "empId" not in cols or "name" not in cols:
        raise RuleError("Couldn't find the Employee ID and Full name columns — start from the template")
    data = rows[1:]
    if not data:
        raise RuleError("The sheet has headings but no people yet")
    if len(data) > MAX_ROWS:
        raise RuleError("Up to %d people per file — split it into smaller files" % MAX_ROWS)
    out = []
    for n, cells in data:
        rec = {"row": n}
        for k in ALIASES:
            i = cols.get(k)
            rec[k] = str(cells[i]).strip() if i is not None and i < len(cells) else ""
        out.append(rec)
    return out


# ---------------------------------------------------------------- checking and creating
def plan(rows, users, known_teams):
    """Check every row against the rules for a single login, plus the file as a
    whole (duplicate IDs; managers defined in the same file). Each result has
    an "errors" list - empty means it's ready to create."""
    by_lower = {t.lower(): t for t in known_teams}
    out, seen = [], {}
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
        teams, unknown = [], []
        for part in re.split(r"[,;]", r["teams"]):
            t = " ".join(part.split())
            if not t:
                continue
            if t.lower() == domain.ALL_TEAMS.lower():
                teams.append(domain.ALL_TEAMS)
            elif t.lower() in by_lower:
                teams.append(by_lower[t.lower()])
            else:
                unknown.append(t)
        if unknown:
            errs.append("Unknown team%s: %s — create %s in Settings → Teams first" % (
                "s" if len(unknown) > 1 else "", ", ".join(unknown), "them" if len(unknown) > 1 else "it"))
        teams = [domain.ALL_TEAMS] if domain.ALL_TEAMS in teams else list(dict.fromkeys(teams))
        if r["password"]:
            problem = security.password_problem(r["password"], emp, name)
            if problem:
                errs.append(problem)
        out.append({"row": r["row"], "empId": emp, "name": name, "role": role or "", "senior": senior,
                    "manager": r["manager"], "teams": teams, "password": r["password"], "errors": errs})

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


def create(db, ready):
    """Create the rows that passed, then link managers once everyone exists.
    Each created row gets a oneTimePassword - given in the file, or generated."""
    done = []
    for p in ready:
        pw = p["password"] or temp_password(p["empId"], p["name"])
        try:
            db.create_user(p["empId"], p["name"], p["role"], pw, None, must_change=True, teams=p["teams"], senior=p["senior"])
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
    return done

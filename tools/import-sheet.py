#!/usr/bin/env python3
"""Import the tracker Google Sheet into data/seed.json + data/seed.js.

The sheet must be shared as "anyone with the link can view".

    pip install openpyxl
    python tools/import-sheet.py "<sheet link or ID>"   # first time
    python tools/import-sheet.py                        # afterwards

The sheet ID is never kept in the code. It comes from the argument, the
WFS_SHEET_ID environment variable, or data/sheet-id.txt - which the first run
writes, and which (like the imported data) is git-ignored.

Reads the Leave, WFH and Validation tabs. Everything else (pivot dashboards,
formula calendar, hidden tabs) is ignored - the app derives all of that from the
raw records instead.
"""
import datetime
import json
import os
import re
import sys
import tempfile
import urllib.request

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHEET_ID_FILE = os.path.join(ROOT, "data", "sheet-id.txt")


def resolve_sheet_id(arg):
    raw = arg or os.environ.get("WFS_SHEET_ID", "")
    if not raw and os.path.exists(SHEET_ID_FILE):
        with open(SHEET_ID_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
    if not raw:
        sys.exit('Pass the Google Sheet link or ID:  python tools/import-sheet.py "<link>"')
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
    sheet_id = m.group(1) if m else raw.strip()
    if not re.match(r"^[A-Za-z0-9_-]{20,}$", sheet_id):
        sys.exit("That doesn't look like a Google Sheet link or ID.")
    os.makedirs(os.path.dirname(SHEET_ID_FILE), exist_ok=True)
    with open(SHEET_ID_FILE, "w", encoding="utf-8") as f:
        f.write(sheet_id + "\n")
    return sheet_id


def download(sheet_id):
    url = "https://docs.google.com/spreadsheets/d/%s/export?format=xlsx" % sheet_id
    path = os.path.join(tempfile.gettempdir(), "wfs-import.xlsx")
    with urllib.request.urlopen(url) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def norm_duration(value):
    """Normalise 'Half Day <en-dash> 1nd Half' and friends to a canonical form."""
    if value is None:
        return "Full Day"
    s = re.sub(r"\s+", " ", str(value).replace("�", "-").replace("–", "-")).strip()
    if s.lower().startswith("full"):
        return "Full Day"
    if "1nd" in s or "1st" in s:
        return "Half Day - 1st Half"
    if "2nd" in s:
        return "Half Day - 2nd Half"
    return s


def as_date(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    return str(value) if value else None


def read_rows(ws):
    return [r for r in ws.iter_rows(values_only=True)
            if any(c is not None and str(c).strip() != "" for c in r)]


def grab(wb, sheet, prefix):
    rows = read_rows(wb[sheet])
    out = []
    for i, r in enumerate(rows[1:]):
        name = (r[1] or "").strip()
        if not name:
            continue
        status = (str(r[8]).strip() if r[8] else "Pending")
        # The sheet overloads Status with "Unplanned" for records logged after the
        # fact. The app keeps planned-ness on the reason and Status for workflow,
        # so those become Approved with an autoLogged marker.
        auto = status in ("Unplanned", "")
        if auto:
            status = "Approved"
        if status == "Decline":
            status = "Declined"
        out.append({
            "id": "%s%04d" % (prefix, i + 1),
            "empId": str(r[2] or "").split(".")[0],
            "name": name,
            "from": as_date(r[3]),
            "to": as_date(r[4]) or as_date(r[3]),
            "duration": norm_duration(r[5]),
            "days": float(r[6]) if r[6] not in (None, "") else 0,
            "reason": (str(r[7]).strip() if r[7] else ""),
            "status": status,
            "comment": (str(r[9]).strip() if r[9] else ""),
            "autoLogged": auto,
        })
    return out


def main():
    sheet_id = resolve_sheet_id(sys.argv[1] if len(sys.argv) > 1 else "")
    wb = openpyxl.load_workbook(download(sheet_id), data_only=True)

    rows = read_rows(wb["Validation"])
    people = [{"empId": str(r[0]).split(".")[0], "name": str(r[1]).strip()}
              for r in rows[1:] if r[0] and r[1]]

    def column(idx):
        vals = []
        for r in rows[1:]:
            if idx < len(r) and r[idx] and str(r[idx]).strip():
                v = norm_duration(r[idx]) if idx == 3 else str(r[idx]).strip()
                if v not in vals:
                    vals.append(v)
        return vals

    seed = {
        "people": people,
        "validation": {
            "leaveReasons": column(2),
            "durations": column(3),
            "statuses": ["Pending", "Approved", "Declined"],
            "wfhReasons": column(5),
        },
        "leave": grab(wb, "Leave", "L"),
        "wfh": grab(wb, "WFH", "W"),
        "generatedFrom": "Google Sheet",
        "generatedAt": datetime.date.today().isoformat(),
    }

    blob = json.dumps(seed, indent=1)
    with open(os.path.join(ROOT, "data", "seed.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    with open(os.path.join(ROOT, "data", "seed.js"), "w", encoding="utf-8") as f:
        f.write("/* Auto-generated by tools/import-sheet.py - not committed. */\n")
        f.write("window.WFS_SEED = %s;\n" % blob)

    print("people: %d | leave: %d | wfh: %d"
          % (len(people), len(seed["leave"]), len(seed["wfh"])))
    print("wrote data/seed.json and data/seed.js")


if __name__ == "__main__":
    main()

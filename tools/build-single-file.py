#!/usr/bin/env python3
"""Inline every stylesheet and script from index.html into one portable file.

    python tools/build-single-file.py

Writes dist/leave-planner.html - a single self-contained page you can email, drop
on a share, or host anywhere. Reads the script/link tags out of index.html, so
adding a new page file needs no change here.
"""
import datetime
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_external(url):
    return url.startswith(("http://", "https://", "//"))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def main():
    # Retired: a single self-contained file would carry every employee's records
    # with no sign-in at all, defeating the login gateway. Serve the app with
    # server/app.py instead.
    raise SystemExit("build-single-file is retired now that the app has sign-in. "
                     "Run the server instead:  python server/app.py")
    html = read("index.html")

    # External URLs (Google Fonts) stay as links; only local files get inlined.
    for href in re.findall(r'<link rel="stylesheet" href="([^"]+)"\s*/?>', html):
        if is_external(href):
            continue
        tag = re.search(r'<link rel="stylesheet" href="%s"\s*/?>' % re.escape(href), html).group(0)
        html = html.replace(tag, "<style>\n%s\n</style>" % read(href))

    for src in re.findall(r'<script src="([^"]+)"></script>', html):
        if is_external(src):
            continue
        tag = '<script src="%s"></script>' % src
        html = html.replace(tag, "<script>\n%s\n</script>" % read(src))

    html = re.sub(r"\n<!--[^>]*-->", "", html)

    # Stamp the build so a downloaded copy always identifies itself in
    # Settings -> Data. Running from source leaves it as 'dev'.
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    marker = "var BUILD = 'dev';"
    if marker not in html:
        raise SystemExit("build stamp marker missing from app.js")
    html = html.replace(marker, "var BUILD = '%s';" % stamp, 1)

    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "leave-planner.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print("wrote dist/leave-planner.html (%.0f KB)  build %s" % (os.path.getsize(out) / 1024, stamp))


if __name__ == "__main__":
    main()

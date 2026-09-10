#!/usr/bin/env python3
"""Draw the app icon - the header's "L" badge - as favicon.svg, favicon.ico
(16/32/48) and apple-touch-icon.png in the project root.

    python tools/make-icons.py

Standard library only. The L is drawn as shapes, not text, so it looks the
same everywhere. Colours match .brand .dot in src/css/app.css.
"""
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STOPS = [(0.0, (0xa6, 0x3d, 0x1f)), (0.55, (0xc9, 0x8a, 0x2b)), (1.0, (0x0f, 0x6b, 0x6b))]  # --leave, --c2, --wfh
ANGLE = 140                     # linear-gradient(140deg, ...)
RADIUS = 0.28                   # corner radius, as a share of the icon
STEM = (0.315, 0.24, 0.465, 0.76)   # the L: x0, y0, x1, y1 (share of the icon)
FOOT = (0.315, 0.61, 0.690, 0.76)

DX, DY = math.sin(math.radians(ANGLE)), -math.cos(math.radians(ANGLE))
SPAN = abs(DX) + abs(DY)        # CSS gradient line length for a square


def gradient(x, y):
    t = min(1.0, max(0.0, ((x - 0.5) * DX + (y - 0.5) * DY) / SPAN + 0.5))
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))
    return STOPS[-1][1]


def in_rounded_square(x, y, r):
    cx, cy = min(max(x, r), 1 - r), min(max(y, r), 1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def in_rect(x, y, box):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def render(size, radius, ss=6):
    """RGBA rows, supersampled ss x ss per pixel for smooth edges."""
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            acc, cover = [0.0, 0.0, 0.0], 0
            for sy in range(ss):
                for sx in range(ss):
                    x, y = (px + (sx + 0.5) / ss) / size, (py + (sy + 0.5) / ss) / size
                    if not in_rounded_square(x, y, radius):
                        continue
                    c = (255, 255, 255) if in_rect(x, y, STEM) or in_rect(x, y, FOOT) else gradient(x, y)
                    acc = [a + b for a, b in zip(acc, c)]
                    cover += 1
            if cover:
                row += bytes(round(v / cover) for v in acc) + bytes([round(255 * cover / (ss * ss))])
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return rows


def png(size, radius):
    raw = b"".join(b"\0" + r for r in render(size, radius))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes):
    images = [png(s, RADIUS) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, img in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    return out + b"".join(images)


def svg():
    n = 64
    half = SPAN * n / 2
    x1, y1, x2, y2 = (n / 2 - DX * half, n / 2 - DY * half, n / 2 + DX * half, n / 2 + DY * half)
    stops = "".join('<stop offset="%g" stop-color="#%02x%02x%02x"/>' % ((t,) + c) for t, c in STOPS)
    s = lambda v: "%g" % round(v * n, 2)
    ell = "M%s %sH%sV%sH%sV%sH%sZ" % (s(STEM[0]), s(STEM[1]), s(STEM[2]), s(FOOT[1]), s(FOOT[2]), s(FOOT[3]), s(STEM[0]))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d">'
            '<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f">%s'
            '</linearGradient></defs>'
            '<rect width="%d" height="%d" rx="%s" fill="url(#g)"/><path d="%s" fill="#fff"/></svg>\n'
            % (n, n, x1, y1, x2, y2, stops, n, n, s(RADIUS), ell))


def main():
    files = {
        "favicon.svg": svg().encode("utf-8"),
        "favicon.ico": ico([16, 32, 48]),
        "apple-touch-icon.png": png(180, 0.0),      # iOS rounds the corners itself
    }
    for name, data in files.items():
        with open(os.path.join(ROOT, name), "wb") as f:
            f.write(data)
        print("wrote %s (%d bytes)" % (name, len(data)))


if __name__ == "__main__":
    main()

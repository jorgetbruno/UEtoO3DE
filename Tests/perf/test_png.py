"""
test_png.py — the PNG decoder that rescues a texture UE refuses to write as TGA.

Pure: no editor. Run: python Tests/perf/test_png.py   (exit code is the verdict)

Why the decoder exists at all is in `ueo3de/png.py`. Why it needs testing this
hard: it runs on ONE texture in 155, so a wrong decode would not announce
itself by breaking a run. It would produce a level in which a single material
is quietly wrong -- exactly the failure mode this repo keeps finding and
keeps deciding is unacceptable.

Every case below is built by ENCODING known pixels with zlib and the PNG
filter of interest, then requiring the decoder to return those same pixels.

Be clear about what that does and does not prove. Encoder and decoder here are
written from the same reading of the specification, so a shared misreading
would round-trip happily. These tests catch implementation slips -- an
off-by-one in the Paeth predictor, a channel swapped in `to_tga`, a filter
left unhandled -- and they do not, on their own, prove the decode is correct.
The independent check is `Tests/ue/probe_texture_export.py`, which exports the
same real textures as both PNG and TGA and compares this decoder's output
against the bytes UE's own TGA exporter wrote. That is ground truth from a
different implementation; this file is the fast guard that runs without an
editor.
"""

import os
import struct
import sys
import tempfile
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import png, tga  # noqa: E402

failures = []
paths = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def encode(width, height, colour, rows, filter_type=0, depth=8, extra=b""):
    """`rows` is a list of bytes objects, one per scanline, unfiltered."""
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    bpp = max(1, samples * (depth // 8))
    raw = bytearray()
    previous = bytes(len(rows[0]))
    for line in rows:
        raw.append(filter_type)
        if filter_type == 0:
            raw += line
        elif filter_type == 1:
            raw += bytes((line[i] - (line[i - bpp] if i >= bpp else 0)) & 0xFF
                         for i in range(len(line)))
        elif filter_type == 2:
            raw += bytes((line[i] - previous[i]) & 0xFF for i in range(len(line)))
        elif filter_type == 3:
            raw += bytes((line[i] - (((line[i - bpp] if i >= bpp else 0)
                                      + previous[i]) >> 1)) & 0xFF
                         for i in range(len(line)))
        elif filter_type == 4:
            out = bytearray()
            for i in range(len(line)):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                out.append((line[i] - pred) & 0xFF)
            raw += out
        previous = line
    body = (_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth,
                                              colour, 0, 0, 0))
            + extra + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))
    handle = tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False)
    handle.write(body)
    handle.close()
    paths.append(handle.name)
    return handle.name


_SIG = b"\x89PNG\r\n\x1a\n"


def rgba(image, x, y):
    i = (y * image["width"] + x) * 4
    return tuple(image["pixels"][i:i + 4])


# --- RGB, every filter type -------------------------------------------------
# Values chosen so neighbouring pixels differ in all channels: a filter bug
# that leaks the left or upper pixel produces a wrong answer rather than
# accidentally the right one.
ROWS_RGB = [bytes([10, 20, 30, 200, 100, 50, 7, 7, 7]),
            bytes([40, 60, 80, 90, 210, 130, 250, 3, 128]),
            bytes([0, 0, 0, 255, 255, 255, 128, 64, 32])]
EXPECT_RGB = [[(10, 20, 30, 255), (200, 100, 50, 255), (7, 7, 7, 255)],
              [(40, 60, 80, 255), (90, 210, 130, 255), (250, 3, 128, 255)],
              [(0, 0, 0, 255), (255, 255, 255, 255), (128, 64, 32, 255)]]

for filter_type in (0, 1, 2, 3, 4):
    image = png.read(encode(3, 3, 2, ROWS_RGB, filter_type=filter_type))
    ok = all(rgba(image, x, y) == EXPECT_RGB[y][x]
             for y in range(3) for x in range(3))
    check(ok, "filter %d decoded wrong: %r" % (filter_type, image["pixels"][:12]))

# Paeth's TIE-BREAK. The fixture above cannot reach it -- a mutation test
# found that changing `pb <= pc` to `pb < pc` left every assertion green -- so
# here is a case that does. Greyscale, one byte per pixel, so the arithmetic is
# visible:
#
#     row 0:  100  90       for the second pixel of row 1:
#     row 1:  105   ?         left=105, up=90, up-left=100
#                             p  = 105 + 90 - 100 = 95
#                             pa = |95-105| = 10
#                             pb = |95-90|  =  5   <- tie, and both
#                             pc = |95-100| =  5   <- beat pa
#
# The rule predicts `up` (90); breaking the tie the other way predicts
# `up-left` (100). Ten levels of grey, wrong, on every pixel that hits this
# case, in a file nothing else inspects.
#
# The OTHER tie -- pa == pb -- was checked exhaustively over all 256^3
# neighbour values and cannot change the result: pa == pb forces either a == b
# (both branches predict the same value) or c exactly between them, which makes
# pc zero so pc wins outright. `pa < pb` is a genuine equivalent mutant, not a
# hole in this test, and it is recorded here so nobody spends an afternoon
# trying to kill it.
PAETH_TIE = [bytes([100, 90]), bytes([105, 200])]
tie = png.read(encode(2, 2, 0, PAETH_TIE, filter_type=4))
check(rgba(tie, 0, 0) == (100, 100, 100, 255) and rgba(tie, 1, 0) == (90, 90, 90, 255)
      and rgba(tie, 0, 1) == (105, 105, 105, 255) and rgba(tie, 1, 1) == (200, 200, 200, 255),
      "Paeth tie-break decoded wrong: %r" % (tie["pixels"],))

# The control: a decoder that ignored filtering entirely would pass filter 0
# and fail the rest, so prove filter 0 and filter 4 really do carry different
# bytes on disk for the same image.
raw0 = open(encode(3, 3, 2, ROWS_RGB, filter_type=0), "rb").read()
raw4 = open(encode(3, 3, 2, ROWS_RGB, filter_type=4), "rb").read()
check(raw0 != raw4,
      "filter 0 and filter 4 encoded to identical bytes, so the filter tests "
      "above proved nothing")

# --- the other colour types -------------------------------------------------
grey = png.read(encode(2, 1, 0, [bytes([17, 200])]))
check(rgba(grey, 0, 0) == (17, 17, 17, 255) and rgba(grey, 1, 0) == (200, 200, 200, 255),
      "greyscale decoded wrong: %r" % (grey["pixels"][:8],))

grey_alpha = png.read(encode(2, 1, 4, [bytes([17, 128, 200, 5])]))
check(rgba(grey_alpha, 0, 0) == (17, 17, 17, 128)
      and rgba(grey_alpha, 1, 0) == (200, 200, 200, 5),
      "greyscale+alpha decoded wrong: %r" % (grey_alpha["pixels"][:8],))

rgba_img = png.read(encode(2, 1, 6, [bytes([1, 2, 3, 4, 250, 251, 252, 253])]))
check(rgba(rgba_img, 0, 0) == (1, 2, 3, 4) and rgba(rgba_img, 1, 0) == (250, 251, 252, 253),
      "RGBA decoded wrong: %r" % (rgba_img["pixels"][:8],))

plte = chunk(b"PLTE", bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
pal = png.read(encode(3, 1, 3, [bytes([0, 1, 2])], extra=plte))
check(rgba(pal, 0, 0) == (255, 0, 0, 255) and rgba(pal, 2, 0) == (0, 0, 255, 255),
      "palette decoded wrong: %r" % (pal["pixels"][:12],))

trns = plte + chunk(b"tRNS", bytes([9, 200, 255]))
pal_a = png.read(encode(3, 1, 3, [bytes([0, 1, 2])], extra=trns))
check(rgba(pal_a, 0, 0) == (255, 0, 0, 9) and rgba(pal_a, 1, 0) == (0, 255, 0, 200),
      "palette transparency decoded wrong: %r" % (pal_a["pixels"][:12],))

# 16-bit: the high byte is what survives, matching UE's own TGA export.
deep = png.read(encode(2, 1, 2, [bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC,
                                        0xDE, 0xF0, 0x11, 0x22, 0x33, 0x44])],
                       depth=16))
check(rgba(deep, 0, 0) == (0x12, 0x56, 0x9A, 255),
      "16-bit decode took the wrong byte: %r" % (deep["pixels"][:4],))

# --- what must be REFUSED rather than half-decoded --------------------------
def refuses(description, make):
    try:
        png.read(make())
    except png.PngError:
        return True
    except Exception as exc:
        failures.append("%s raised %s instead of PngError" % (description, type(exc).__name__))
        print("FAIL: " + failures[-1])
        return False
    failures.append("%s was accepted silently" % description)
    print("FAIL: " + failures[-1])
    return False


def _interlaced():
    body = (_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 1))
            + chunk(b"IDAT", zlib.compress(bytes([0, 1, 2, 3, 4, 5, 6])))
            + chunk(b"IEND", b""))
    handle = tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False)
    handle.write(body); handle.close(); paths.append(handle.name)
    return handle.name


def _not_a_png():
    handle = tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False)
    handle.write(b"GIF89a not a png at all"); handle.close(); paths.append(handle.name)
    return handle.name


refuses("an interlaced PNG", _interlaced)
refuses("a 4-bit PNG", lambda: encode(2, 1, 0, [bytes([0x12])], depth=4))
refuses("a non-PNG file", _not_a_png)


# --- to_tga: the file the rest of the pipeline actually reads ---------------
source = encode(3, 3, 2, ROWS_RGB, filter_type=4)
out = tempfile.NamedTemporaryFile("wb", suffix=".tga", delete=False)
out.close()
paths.append(out.name)
png.to_tga(source, out.name)

# It must parse as a TGA through the module that reads them in production --
# not through anything written for this test.
parsed = tga.read(out.name)
check(parsed["width"] == 3 and parsed["height"] == 3 and parsed["bpp"] == 32,
      "converted TGA has wrong geometry: %r"
      % {k: parsed[k] for k in ("width", "height", "bpp")})
check(parsed["descriptor"] & 0x20 == 0x20,
      "converted TGA is not top-down, so every image would come out flipped")

# And the PIXELS must survive, in BGRA order, in the same reading order.
expected = bytearray()
for row in EXPECT_RGB:
    for (r, g, b, a) in row:
        expected += bytes([b, g, r, a])
check(parsed["pixels"] == bytes(expected),
      "converted TGA pixels differ from the source PNG")

# The control for the pixel check: a comparison that cannot fail proves
# nothing, so confirm a DIFFERENT image really does produce different bytes.
other = encode(3, 3, 2, [bytes(9), bytes(9), bytes(9)], filter_type=0)
other_tga = tempfile.NamedTemporaryFile("wb", suffix=".tga", delete=False)
other_tga.close()
paths.append(other_tga.name)
png.to_tga(other, other_tga.name)
check(tga.read(other_tga.name)["pixels"] != bytes(expected),
      "two different images converted to identical TGA pixels")

for path in paths:
    try:
        os.remove(path)
    except OSError:
        pass

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

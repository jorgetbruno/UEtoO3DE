"""test_frame_stats.py -- the render-side check that would have caught a white level.

Pure: no editor. Run: python Tests/perf/test_frame_stats.py

Synthetic frames, because the point is the DECISION, not any one capture: a
blown frame must fail, a black frame must fail, and ordinary bright and dark
scenes must both pass. A check that fails a legitimate night scene gets
switched off, and then the real failure comes back.

The frame that motivated this measured, in the editor: a level where every
structural assertion passed and nothing was visible.
"""

import os
import struct
import sys
import tempfile
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))

import frame_stats  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def write_png(path, pixels, width, height):
    """Minimal 8-bit RGB PNG; `pixels` is a flat RGB byte sequence."""
    raw = b""
    stride = width * 3
    for row in range(height):
        raw += b"\x00" + bytes(pixels[row * stride:(row + 1) * stride])

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(blob)
    return path


WORK = tempfile.mkdtemp(prefix="ueo3de_frame_")
W, H = 64, 48


def solid(value):
    return [value] * (W * H * 3)


def gradient():
    out = []
    for row in range(H):
        for col in range(W):
            v = int(255.0 * col / float(W - 1))
            out += [v, v, v]
    return out


def night():
    """A legitimately dark scene: mostly low values, but with real detail."""
    out = []
    for row in range(H):
        for col in range(W):
            v = 4 + int(40.0 * ((row * W + col) % 37) / 37.0)
            out += [v, v, v]
    return out


def bright_sky():
    """Legitimate outdoor frame: a clipped sun/sky band over detailed ground.

    A THIRD of the frame clips, not two thirds. The earlier fixture blew out
    two thirds and forced the verdict to be so lenient that it passed the real
    broken capture (72% white). Atom tonemaps: a correctly exposed outdoor
    scene has highlights that clip, not a majority of the frame.
    """
    out = []
    for row in range(H):
        for col in range(W):
            if row < H // 3:
                out += [255, 255, 255]
            else:
                v = 30 + int(180.0 * col / float(W - 1))
                out += [v, v, v]
    return out


# --- 1. the failure that shipped ----------------------------------------------
blown = write_png(os.path.join(WORK, "blown.png"), solid(255), W, H)
stats = frame_stats.frame_stats(blown)
check(stats["mean"] > 0.98, "a white frame's mean luminance should be ~1.0; got %.3f"
      % stats["mean"])
check(stats["white_clipped"] > 0.99,
      "a white frame should be ~100%% clipped; got %.3f" % stats["white_clipped"])
reason = frame_stats.verdict(stats)
check(reason is not None,
      "A FULLY WHITE FRAME MUST FAIL. This is the exact frame that passed 905 "
      "entity checks, 693 collider verifications and 0 AP errors.")
if reason:
    check("WHITE" in reason, "the reason must name the direction; got %r" % reason)

# --- 2. the other direction ---------------------------------------------------
black = write_png(os.path.join(WORK, "black.png"), solid(0), W, H)
stats = frame_stats.frame_stats(black)
check(frame_stats.verdict(stats) is not None,
      "a fully black frame must fail too -- 'nothing is lit' is as broken as "
      "'everything is blown', and an import can produce either")

flat = write_png(os.path.join(WORK, "flat.png"), solid(128), W, H)
check(frame_stats.verdict(frame_stats.frame_stats(flat)) is not None,
      "a flat mid-grey field has no picture in it and must fail, even though "
      "its mean luminance is perfect -- mean alone is not the test")

# --- 3. frames that must PASS -------------------------------------------------
# A check that fails these gets disabled, and then it protects nothing.
ok = write_png(os.path.join(WORK, "gradient.png"), gradient(), W, H)
stats = frame_stats.frame_stats(ok)
check(frame_stats.verdict(stats) is None,
      "a full-range gradient is a healthy frame; got %r"
      % frame_stats.verdict(stats))

dark = write_png(os.path.join(WORK, "night.png"), night(), W, H)
stats = frame_stats.frame_stats(dark)
check(frame_stats.verdict(stats) is None,
      "a legitimate NIGHT scene (mean %.3f) must pass -- this check is not a "
      "brightness preference; got %r" % (stats["mean"], frame_stats.verdict(stats)))

sky = write_png(os.path.join(WORK, "sky.png"), bright_sky(), W, H)
stats = frame_stats.frame_stats(sky)
check(0.2 < stats["white_clipped"] < 0.5,
      "the sky fixture should clip a MINORITY of the frame -- a realistic "
      "tonemapped outdoor shot, not a blown one; got %.3f"
      % stats["white_clipped"])
check(frame_stats.verdict(stats) is None,
      "a bright sky over detailed ground must pass, or every outdoor level "
      "fails and the check gets switched off; got %r" % frame_stats.verdict(stats))

# The REAL broken capture's shape: 72% clipped white but a healthy 0.745
# spread. The first verdict required clipping AND flatness and so passed this.
mostly_white = []
for _row in range(H):
    for col in range(W):
        if col < int(W * 0.72):
            mostly_white += [255, 255, 255]
        else:
            v = 60 + int(190.0 * col / float(W - 1))
            mostly_white += [v, v, v]
blown_but_varied = write_png(os.path.join(WORK, "blown_varied.png"),
                             mostly_white, W, H)
stats = frame_stats.frame_stats(blown_but_varied)
check(stats["range"] > 0.05,
      "this fixture must NOT look flat -- the whole point is that the old "
      "'clipped AND flat' rule could not catch it. The real capture spread "
      "was 0.745; anything above the 0.05 flatness threshold reproduces the "
      "hole. Got %.3f" % stats["range"])
check(frame_stats.verdict(stats) is not None,
      "A FRAME THAT IS 72%% CLIPPED WHITE MUST FAIL EVEN WITH A WIDE "
      "HISTOGRAM. This is the exact shape of the real blown capture, and the "
      "first version of verdict() passed it.")

# --- 4. the statistics themselves ---------------------------------------------
stats = frame_stats.frame_stats(ok)
check(stats["range"] > 0.8, "a 0..255 gradient should span nearly the full "
                            "range; got %.3f" % stats["range"])
check(0.45 < stats["mean"] < 0.55,
      "a linear gradient averages mid-grey; got %.3f" % stats["mean"])
check(stats["width"] == W and stats["height"] == H,
      "the decoded dimensions should match what was written")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

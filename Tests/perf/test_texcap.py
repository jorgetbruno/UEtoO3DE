"""
test_texcap.py — UEO3DE_TEX_MAX: capping cooked texture products at staging.

Pure: no editor, no Asset Processor. Run: python Tests/perf/test_texcap.py

WHY THIS EXISTS. A city export carries 4K source textures; a project that
wants "nothing over ~1080p" should not re-export 19 GB to get it. The image
builder already knows how to halve a product (SizeReduceLevel in the per-image
`.assetinfo`), so staging computes the halvings from the image header and
writes the sidecar. These tests pin the three parts that would fail silently:
reading dimensions from PNG/TGA headers, the halving arithmetic, and the
sidecar bytes the builder parses.
"""

import os
import struct
import sys
import tempfile
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import staging  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- the halving arithmetic --------------------------------------------------
# 1080 is not a power of two; the point is "halve until it fits", not "round
# to the nearest power of two". 4096 needs two halvings (1024), 1920 one
# (960), 1080 none, 1200 one (600).
for width, height, cap, expected in ((4096, 4096, 1080, 2), (2048, 2048, 1080, 1),
                                     (1920, 1080, 1080, 1), (1080, 1080, 1080, 0),
                                     (1200, 300, 1080, 1), (512, 8192, 1080, 3),
                                     (1, 1, 1080, 0)):
    got = staging.size_reduce_level(width, height, cap)
    check(got == expected, "%dx%d at cap %d must reduce %d times, got %d"
          % (width, height, cap, expected, got))

# --- the knob ---------------------------------------------------------------
check(staging.texture_max({}) is None, "unset UEO3DE_TEX_MAX must mean no cap")
check(staging.texture_max({"UEO3DE_TEX_MAX": " 1080 "}) == 1080,
      "the cap must parse with whitespace")
for garbage in ("1080p", "0", "-4"):
    try:
        staging.texture_max({"UEO3DE_TEX_MAX": garbage})
        check(False, "UEO3DE_TEX_MAX=%r must raise, not fall back" % garbage)
    except ValueError:
        pass

# --- header readers ---------------------------------------------------------
scratch = tempfile.mkdtemp(prefix="ueo3de_texcap_")


def write_png(path, width, height):
    ihdr = struct.pack(">II5B", width, height, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(struct.pack(">I", len(ihdr)) + chunk
                     + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF))


def write_tga(path, width, height):
    header = struct.pack("<3B2HB4H2B", 0, 0, 2, 0, 0, 0, 0, 0, width, height, 32, 8)
    with open(path, "wb") as handle:
        handle.write(header)


png_path = os.path.join(scratch, "t_brick_bc.png")
write_png(png_path, 4096, 2048)
check(staging.image_dimensions(png_path) == (4096, 2048),
      "PNG dimensions must come from IHDR; got %r"
      % (staging.image_dimensions(png_path),))

tga_path = os.path.join(scratch, "t_brick_n.tga")
write_tga(tga_path, 1920, 1080)
check(staging.image_dimensions(tga_path) == (1920, 1080),
      "TGA dimensions must come from the header; got %r"
      % (staging.image_dimensions(tga_path),))

broken = os.path.join(scratch, "broken.tga")
with open(broken, "wb") as handle:
    handle.write(b"xy")
check(staging.image_dimensions(broken) is None,
      "a truncated image must read as None, not raise")
check(staging.image_dimensions(os.path.join(scratch, "missing.png")) is None,
      "a missing image must read as None, not raise")

# --- the sidecar bytes ------------------------------------------------------
sidecar = staging._TEXTURE_SETTINGS_SIDECAR % 2
check('field="SizeReduceLevel" value="2"' in sidecar,
      "the sidecar must carry the reduce level where the builder reads it")
check('type="{980132FF-C450-425D-8AE0-BD96A8486177}"' in sidecar,
      "the sidecar must name the TextureSettings type the builder deserializes")
check(sidecar.startswith('<ObjectStream version="3">'),
      "the sidecar must be ObjectStream v3, the format LoadTextureSetting parses")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

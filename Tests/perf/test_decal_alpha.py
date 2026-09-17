"""
test_decal_alpha.py — a decal's mask reaches O3DE inside the base colour's alpha.

Pure: no editor. Run: python Tests/perf/test_decal_alpha.py

WHY THIS EXISTS. O3DE's decal shader takes opacity from the base colour's
ALPHA and reads no opacity map at all (Atom/Features/PBR/Decals.azsli:
`baseMap.a * m_opacity * decalAttenuation`). Exported the StandardPBR way --
opacity as its own texture and `opacity.textureMap` in the material -- every
NYC1950 decal drew its whole projector box opaque over the street, because
`baseMap.a` was 1 everywhere. Two independent halves: composite the mask into
the alpha (here), and name the file `_decal` so the Asset Processor's
Decal_AlbedoWithOpacity preset keeps that alpha instead of Albedo's BC1
`DiscardAlpha`.
"""

import os
import struct
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import tga  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def write_tga(path, width, height, pixels, bpp=24, descriptor=0x20):
    with open(path, "wb") as handle:
        handle.write(struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                                 width, height, bpp, descriptor))
        handle.write(bytes(pixels))


folder = tempfile.mkdtemp(prefix="ueo3de_decal_")
# 4x1: a visible red texel at x=1, black elsewhere; the mask keeps only x=1.
colour = bytearray()
for x in range(4):
    colour += bytes([0, 0, 255] if x == 1 else [0, 0, 0])      # BGR
mask = bytearray()
for x in range(4):
    mask += bytes([255, 255, 255] if x == 1 else [0, 0, 0])
colour_path = os.path.join(folder, "c.tga")
mask_path = os.path.join(folder, "m.tga")
write_tga(colour_path, 4, 1, colour)
write_tga(mask_path, 4, 1, mask)

out = tga.write_with_alpha(colour_path, mask_path, os.path.join(folder, "d.tga"))
image = tga.read(out)
check(image["bpp"] == 32, "a decal base colour must be 32 bpp, got %d" % image["bpp"])
alpha = list(image["pixels"][3::4])
check(alpha == [0, 255, 0, 0], "the mask must land in alpha; got %r" % alpha)
reds = list(image["pixels"][2::4])
check(reds == [255, 255, 255, 255],
      "RGB must be dilated under alpha 0, never left black (BC7 blocks and mips "
      "average it into the visible texels and ring the decal); got %r" % reds)
check(image["descriptor"] & 0x0F == 8, "the descriptor must declare 8 alpha bits")
check(image["descriptor"] & 0x20 == 0x20, "row order must be preserved")

# a mask stored bottom-up against a top-down colour is flipped to match
bottom_up = bytearray()
for y in range(2):
    for x in range(2):
        bottom_up += bytes([255, 255, 255] if y == 0 else [0, 0, 0])
top_down_colour = bytes([10, 20, 30]) * 4
write_tga(os.path.join(folder, "c2.tga"), 2, 2, top_down_colour, descriptor=0x20)
write_tga(os.path.join(folder, "m2.tga"), 2, 2, bottom_up, descriptor=0x00)
out2 = tga.write_with_alpha(os.path.join(folder, "c2.tga"), os.path.join(folder, "m2.tga"),
                            os.path.join(folder, "d2.tga"))
alpha2 = list(tga.read(out2)["pixels"][3::4])
check(alpha2 == [0, 0, 255, 255],
      "a bottom-up mask must be flipped onto a top-down colour; got %r" % alpha2)

# a mask of a different size is a refusal, not a silent crop
write_tga(os.path.join(folder, "m3.tga"), 2, 1, bytes(6))
try:
    tga.write_with_alpha(colour_path, os.path.join(folder, "m3.tga"),
                         os.path.join(folder, "d3.tga"))
    check(False, "a mask whose size differs must raise")
except tga.TgaError as error:
    check("mask" in str(error), "the refusal must name the mismatch: %s" % error)

# a mask of low-level NOISE is still masked: dilation fills from what is visible
noisy = bytearray()
for x in range(4):
    noisy += bytes([0, 0, 200, 255] if x == 2 else [0, 0, 0, 4])   # BGRA, alpha noise 4
tga.dilate_rgb(4, 1, noisy, 4)
check(list(noisy[2::4]) == [200, 200, 200, 200],
      "alpha noise (a real mask averages ~4) must not count as visible colour, or "
      "nothing is dilated and the black stays; got %r" % list(noisy[2::4]))

# an all-transparent image has nothing to dilate from, and must not hang
pixels = bytearray([0, 0, 0, 0] * 16)
tga.dilate_rgb(4, 4, pixels, 4)
check(all(v == 0 for v in pixels), "a fully masked-out image is left alone")

# --- the decal material subset -----------------------------------------------------
import types  # noqa: E402

stub = types.ModuleType("unreal")
stub.MaterialEditingLibrary = object()
sys.modules.setdefault("unreal", stub)

from ueo3de import material_export as me  # noqa: E402


class Warnings(object):
    def __init__(self):
        self.records = []

    def add(self, code, subject, detail):
        self.records.append((code, subject, detail))

    def codes(self):
        return [code for code, _s, _d in self.records]


class FakeBank(object):
    """Enough of TextureBank to observe what a decal asks of it."""

    def __init__(self):
        self.textures = {"base": ("base_tex", {"guid": "base", "o3de_relative_path": "t_b_basecolor.tga"}),
                         "mask": ("mask_tex", {"guid": "mask", "o3de_relative_path": "t_o_opacity.tga"})}
        self.requests = []
        self.discarded = []

    def find_by_guid(self, guid):
        texture, entry = self.textures[guid]
        return ("key:" + guid, texture, entry)

    def request(self, texture, role, channel=None, tint=None, alpha_from=None):
        self.requests.append((texture, role, channel, alpha_from))
        return {"guid": "decal_guid", "o3de_relative_path": "t_b_decal.tga"}

    def discard(self, key):
        self.discarded.append(key)


bank, warnings = FakeBank(), Warnings()
properties = {
    "base_color": {"source": "texture", "texture_guid": "base", "channel": None, "factor": None},
    "opacity": {"source": "texture", "texture_guid": "mask", "channel": "R", "factor": None},
    "normal": {"source": "texture", "texture_guid": "base", "channel": None, "factor": None},
    "roughness": {"source": "scalar", "value": 0.5},
    "metallic": {"source": "scalar", "value": 1.0},
}
kept = me._decal_properties(properties, bank, warnings, "/Game/MI_Crack")
check(set(kept) == {"base_color", "normal"},
      "a decal keeps albedo and normal only (the decal pass writes nothing else); got %r"
      % sorted(kept))
check(kept["base_color"]["texture_guid"] == "decal_guid",
      "base colour must point at the composited texture")
check(bank.requests and bank.requests[0][1] == "decal" and bank.requests[0][3] == ("mask_tex", "R"),
      "the request must ask for the decal role with the mask as alpha; got %r" % (bank.requests,))
check(sorted(bank.discarded) == ["key:base", "key:mask"],
      "the plain basecolor and standalone opacity files are no longer this "
      "material's (prune_unreferenced keeps them if anything else uses them); got %r"
      % bank.discarded)
check("DECAL_ALPHA_BAKED" in warnings.codes() and "DECAL_CHANNELS_DROPPED" in warnings.codes(),
      "both the bake and the dropped channels must be reported; got %r" % warnings.codes())

# an opacity that is a scalar cannot become a mask: say so rather than pretend
bank2, warnings2 = FakeBank(), Warnings()
kept2 = me._decal_properties(
    {"base_color": {"source": "texture", "texture_guid": "base"},
     "opacity": {"source": "scalar", "value": 0.5}}, bank2, warnings2, "/Game/MI_Flat")
check("DECAL_MASK_UNMAPPED" in warnings2.codes(),
      "a scalar opacity on a decal must warn; got %r" % warnings2.codes())
check(kept2["base_color"]["texture_guid"] == "base" and not bank2.requests,
      "with no mask to bake, the base colour is left as it is")

# a decal with no opacity at all is opaque by design, and silent about it
bank3, warnings3 = FakeBank(), Warnings()
kept3 = me._decal_properties({"base_color": {"source": "texture", "texture_guid": "base"}},
                             bank3, warnings3, "/Game/MI_Opaque")
check(warnings3.codes() == [] and not bank3.requests,
      "an opaque decal needs no bake and no warning; got %r" % warnings3.codes())


print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

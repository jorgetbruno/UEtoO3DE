"""
test_m4_artifacts.py — M4 acceptance, offline half (plan v2.2).

    "Fixture materials round-trip: albedo/normal/roughness/metallic resolve;
     ORM-packed material produces three resolved maps; Masked imports as
     Cutout; Translucent as Blended. The deliberately unsupported material
     yields MAT_EXPR_UNSUPPORTED and its entity gets the default material."

Asserts on three artifact layers, none of which needs an editor:

  1. the manifest: material_data presence/absence + warning codes;
  2. the exported texture files: the ORM split produced three grayscale TGAs
     with the right channels, verified by the same pure TGA reader the
     splitter uses (plus a synthetic split unit test with KNOWN channel
     values, because the fixture's textures are white on every channel and
     white would hide a channel-indexing bug);
  3. the staged .material JSON: flipY on normals, Cutout/Blended modes, Split
     alpha, and every texture reference resolving to a staged file.

Run:  python Tests/m4/test_m4_artifacts.py [project_path]
"""

import json
import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueo3de import tga  # noqa: E402
from ueimporter import manifest_io  # noqa: E402

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
EXPORT_ASSETS = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "Assets")
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests"))
from paths import PATHS  # noqa: E402

# Tests/paths.config (or the env var) -- a hardcoded home directory is a
# fallback onto someone else's disk on every machine but one.
DEFAULT_PROJECT = PATHS.get("O3DE_PROJECT_JOLT")

failures = []


def fail(message):
    failures.append(str(message))
    print("FAIL: " + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def material_by_name(document, name):
    return next((a for a in document["assets"]
                 if a["kind"] == "material" and a["name"] == name), None)


# ---------------------------------------------------------------------------

def test_tga_split_synthetic(scratch):
    """Channel split against KNOWN values -- white fixtures can't catch a
    channel-index bug, so a synthetic 2x1 image with distinct channels does.

    The split output is 8-BIT GRAYSCALE PNG, not TGA: the engine's TgaLoader
    rejects grayscale TGA outright ("unsupported type code [3]", measured),
    and channel-replicated 24-bit TGA was 5.1x the bytes. Read back through
    the same pure PNG reader the exporter package ships.
    """
    from ueo3de import png as png_module

    source = os.path.join(scratch, "synthetic.tga")
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 1, 32, 8)
    # BGRA: pixel0 = (B=10, G=20, R=30, A=40), pixel1 = (50, 60, 70, 80)
    with open(source, "wb") as handle:
        handle.write(header + bytes([10, 20, 30, 40, 50, 60, 70, 80]))

    for channel, expected in (("R", (30, 70)), ("G", (20, 60)),
                              ("B", (10, 50)), ("A", (40, 80))):
        out = os.path.join(scratch, "split_%s.png" % channel)
        tga.write_channel_png(source, out, channel)
        image = png_module.read(out)
        # png.read normalizes to RGBA; a grayscale source reads back R=G=B.
        got = (image["pixels"][0], image["pixels"][4])
        check(got == expected,
              "channel %s split produced %r, expected %r" % (channel, got, expected))
        check(image["pixels"][0] == image["pixels"][1] == image["pixels"][2],
              "a grayscale split must read back with R=G=B")

    # A from a 24-bpp source is implicitly opaque -> solid white.
    source24 = os.path.join(scratch, "synthetic24.tga")
    header24 = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 24, 0)
    with open(source24, "wb") as handle:
        handle.write(header24 + bytes([1, 2, 3]))
    out = os.path.join(scratch, "split24_A.png")
    tga.write_channel_png(source24, out, "A")
    check(png_module.read(out)["pixels"][0] == 255,
          "alpha of a 24-bpp image must split as opaque white")

    # ROW ORDER: TGA default origin is bottom-left, PNG is top-down. A 1x2
    # bottom-up TGA whose STORED rows are [bottom=5, top=200] must read back
    # from the PNG with 200 first. Getting this wrong flips every split
    # against the basecolor it shares UVs with -- and a symmetric texture
    # would never show it.
    source_rows = os.path.join(scratch, "rows.tga")
    header_rows = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 2, 24, 0)
    with open(source_rows, "wb") as handle:
        handle.write(header_rows + bytes([5, 5, 5, 200, 200, 200]))
    out = os.path.join(scratch, "rows_R.png")
    tga.write_channel_png(source_rows, out, "R")
    pixels = png_module.read(out)["pixels"]
    check((pixels[0], pixels[4]) == (200, 5),
          "bottom-up TGA rows must be flipped to PNG top-down order; "
          "got top=%r bottom=%r" % (pixels[0], pixels[4]))


def test_parameter_role_matching():
    """The name->role rules behind MAT_PARAMS_BY_NAME (pure, param_roles.py).

    Shaped after the real failure they exist for: MM_Building's textures are
    parameters inside an unwalkable material function, and the only usable
    signal is artist-chosen names among a soup of blend-layer parameters.
    """
    from ueo3de import param_roles

    # The MM_Building shape: plain base names among blend-layer noise.
    roles = param_roles.pick_parameter_roles([
        "Plaster BaseColor", "Plaster Normal", "Plaster ORM",
        "GroundBlend BaseColor", "GroundBlend Normal", "GroundBlend ORM",
        "Grunge Mask", "Macro Variation", "Dirt Overlay",
    ])
    check(roles.get("basecolor") == "Plaster BaseColor",
          "base colour should pick the plain name, got %r" % roles.get("basecolor"))
    check(roles.get("normal") == "Plaster Normal", "normal pick: %r" % roles.get("normal"))
    check(roles.get("orm") == "Plaster ORM", "orm pick: %r" % roles.get("orm"))
    check("GroundBlend BaseColor" not in roles.values(),
          "a blend-layer parameter must never be chosen")

    # Short tokens need word boundaries: armour is not ORM, chaos is not AO.
    roles = param_roles.pick_parameter_roles(["Armor Color", "Chaos Texture"])
    check("orm" not in roles, "'Armor' must not match the ORM role: %r" % roles)
    check("ao" not in roles, "'Chaos' must not match the AO role: %r" % roles)
    check(roles.get("basecolor") == "Armor Color",
          "'Armor Color' is a legitimate colour name, got %r" % roles)

    # Separate roughness/metallic when no packed map exists; one parameter
    # is claimed by at most one role.
    roles = param_roles.pick_parameter_roles(["T_Roughness", "T_Metallic", "T_AO"])
    check(roles.get("roughness") == "T_Roughness" and roles.get("metallic") == "T_Metallic"
          and roles.get("ao") == "T_AO", "separate maps should each match: %r" % roles)

    # Nothing role-shaped -> nothing chosen (the material stays grey, loudly).
    check(param_roles.pick_parameter_roles(["Foo", "Bar", "Wind Intensity"]) == {},
          "unrecognizable names must yield no roles")


def test_one_texture_on_several_roles_is_a_packed_map():
    """Roughness+metallic+AO from ONE texture with no mask is packed.

    MEASURED on Vehicles/VOL4_RetroCars: the graph WAS walkable, so each
    property found its texture -- but there was no ComponentMask between them,
    so every channel hint came back None and the same TX_Car_24a_RMA was
    exported WHOLE three times. The three files were byte-identical (same MD5,
    50,331,666 bytes each), so Atom read the same data for roughness, metallic
    and AO. Metallic and occlusion were driven by the roughness map and the
    cars rendered dark and wrongly reflective.

    Same root cause as the ORM/RMA bug, reached by the other classification
    path -- which is why the earlier fix did not cover this level.
    """
    # material_export imports `unreal` at module scope; the function under
    # test never touches it, so a bare stub keeps this suite offline.
    import types
    sys.modules.setdefault("unreal", types.ModuleType("unreal"))
    from ueo3de import material_export

    class FakeTexture(object):
        def __init__(self, name): self._name = name
        def get_name(self): return self._name

    class FakeBank(object):
        """Enough of TextureBank to observe what gets requested.

        MODELS THE REAL GUID SCHEME: `request` derives the guid from
        ue_path + "#" + role, so ONE texture on three roles yields THREE
        DIFFERENT guids. An earlier version of this fake handed out a single
        shared guid; the test passed and the real export did nothing, because
        the code was grouping on guid and never saw a shared texture.
        """
        def __init__(self, ue_path, texture, name):
            self.ue_path = ue_path
            self.texture = texture
            self.name = name
            self.records = {}
            self.requested = []
            self.discarded = []
            # Whole-texture requests, one per role, as the real exporter makes.
            for role in ("roughness", "metallic", "ao"):
                self.request(texture, role, None)

        def request(self, texture, role, channel=None):
            self.requested.append((texture.get_name(), role, channel))
            role_key = role if channel is None else "%s@%s" % (role, channel)
            guid = "%s#%s" % (self.ue_path, role_key)
            self.records[(self.ue_path, role_key)] = {
                "entry": {"guid": guid, "name": self.name,
                          "ue_path": self.ue_path},
                "texture": texture}
            return {"guid": guid}

        def find_by_guid(self, guid):
            for key, rec in self.records.items():
                if rec["entry"]["guid"] == guid:
                    return key, rec["texture"], rec["entry"]
            return None, None, None

        def discard(self, key):
            self.discarded.append(key)
            self.records.pop(key, None)

    class FakeWarnings(object):
        def __init__(self): self.added = []
        def add(self, code, subject, detail): self.added.append((code, subject, detail))

    def packed_props(texture_name):
        texture = FakeTexture(texture_name)
        bank = FakeBank("/Game/T/%s" % texture_name, texture, texture_name)
        props = {}
        for key, role in (("roughness", "roughness"), ("metallic", "metallic"),
                          ("occlusion", "ao")):
            props[key] = {"source": "texture",
                          "texture_guid": "/Game/T/%s#%s" % (texture_name, role),
                          "channel": None, "factor": None}
        warns = FakeWarnings()
        material_export.split_shared_packed_texture(props, bank, warns, "MI_X")
        return props, bank, warns

    props, bank, warns = packed_props("TX_Car_24a_RMA")
    check(props["roughness"]["channel"] == "R",
          "RMA: roughness is the R channel, got %r" % props["roughness"]["channel"])
    check(props["metallic"]["channel"] == "G",
          "RMA: metallic is the G channel, got %r" % props["metallic"]["channel"])
    check(props["occlusion"]["channel"] == "B",
          "RMA: AO is the B channel, got %r" % props["occlusion"]["channel"])
    check(len({props[k]["texture_guid"] for k in props}) == 3,
          "the three roles must end up on three DIFFERENT channel-split "
          "entries; got %r" % {k: props[k]["texture_guid"] for k in props})
    check(len(bank.discarded) == 3,
          "all THREE whole-texture requests must be dropped -- there is one "
          "per role, and leaving any writes a full 48 MB copy nothing uses; "
          "discarded %r" % (bank.discarded,))
    check(bank.discarded,
          "the whole-texture request must be discarded once the splits replace "
          "it, or the 48 MB full copy is still exported and nothing uses it")
    check(any(c == "MAT_PACKED_TEXTURE_SPLIT" for c, _s, _d in warns.added),
          "the split must be reported")

    # ORM order for an ORM-named map, so the detection is not RMA-specific.
    props, _bank, _warns = packed_props("TX_Wall_ORM")
    check(props["occlusion"]["channel"] == "R" and props["metallic"]["channel"] == "B",
          "ORM: AO is R and metallic is B, got %r"
          % {k: props[k]["channel"] for k in props})

    # ONE role on a texture is a normal dedicated map and must NOT be split.
    texture = FakeTexture("TX_Thing_Roughness")
    bank = FakeBank("/Game/T/Solo", texture, "TX_Thing_Roughness")
    props = {"roughness": {"source": "texture",
                           "texture_guid": "/Game/T/Solo#roughness",
                           "channel": None, "factor": None}}
    warns = FakeWarnings()
    material_export.split_shared_packed_texture(props, bank, warns, "MI_Y")
    check(props["roughness"]["texture_guid"] == "/Game/T/Solo#roughness"
          and props["roughness"]["channel"] is None,
          "a texture used by a SINGLE role is a dedicated map and must be left "
          "whole; got %r" % props["roughness"])
    check(not warns.added, "no split, no warning")

    # Already channel-masked properties are the graph's own answer: leave them.
    texture = FakeTexture("TX_Masked_RMA")
    bank = FakeBank("/Game/T/Masked", texture, "TX_Masked_RMA")
    props = {"roughness": {"source": "texture",
                           "texture_guid": "/Game/T/Masked#roughness@R",
                           "channel": "R", "factor": None},
             "metallic": {"source": "texture",
                          "texture_guid": "/Game/T/Masked#metallic@G",
                          "channel": "G", "factor": None}}
    warns = FakeWarnings()
    material_export.split_shared_packed_texture(props, bank, warns, "MI_Z")
    check(props["roughness"]["channel"] == "R" and props["metallic"]["channel"] == "G",
          "a ComponentMask in the graph is authoritative and must not be "
          "second-guessed; got %r" % {k: props[k]["channel"] for k in props})


def test_packed_channel_order_follows_the_token():
    """ORM/ARM/RMA/MRA are FOUR orders, not four spellings of one.

    THE BUG THIS PINS, measured on Docks/VOL4_Albert `Demonstration`: 53
    textures named `*_RMA` were every one split as if they were ORM, because
    all four tokens mapped to a single role and the splitter hard-coded
    R->ao, G->roughness, B->metallic. So roughness received METALLIC data,
    metallic received AO, and AO received ROUGHNESS -- all three channels
    wrong on every PBR surface in the level.

    Nothing caught it: the material files were well-formed, every texture
    resolved, the importer assigned 810 materials, and the Asset Processor
    reported no errors. The only symptom was that the level looked wrong.

    This only applies to name-classified materials (`MAT_PARAMS_BY_NAME`);
    when the graph is walkable the channel comes from its ComponentMask.
    """
    from ueo3de import param_roles

    expected = {
        "ORM": {"R": "ao", "G": "roughness", "B": "metallic"},
        "ARM": {"R": "ao", "G": "roughness", "B": "metallic"},
        "RMA": {"R": "roughness", "G": "metallic", "B": "ao"},
        "MRA": {"R": "metallic", "G": "roughness", "B": "ao"},
    }
    for token, want in expected.items():
        order, seen = param_roles.packed_channel_order("T_Boat_17a_%s" % token)
        check(seen == token.lower(),
              "%r should be recognised as %r, got %r" % (token, token.lower(), seen))
        check(order == want,
              "%s must split as %r, got %r" % (token, want, order))

    # The two that differ are the whole point: if these ever agree, the fix
    # has been undone.
    orm, _ = param_roles.packed_channel_order("T_X_ORM")
    rma, _ = param_roles.packed_channel_order("T_X_RMA")
    check(orm != rma,
          "ORM and RMA must NOT split alike -- treating them alike is the bug")
    check(rma["R"] == "roughness" and orm["R"] == "ao",
          "the R channel is roughness in RMA and occlusion in ORM")

    # An unlabelled packed map falls back to ORM, and the caller must be able
    # to see that it was a guess.
    order, seen = param_roles.packed_channel_order("T_Surface_Packed")
    check(seen is None,
          "a name with no convention token must report None, not a guess "
          "dressed as a fact")
    check(order == expected["ORM"],
          "the fallback is ORM (most common, and the previous behaviour)")

    # Word boundaries still apply: "armor" is not ARM.
    check(param_roles.packing_token("Armor Packed") is None,
          "'Armor' must not be read as the ARM packing token")


def test_manifest_material_data(document):
    for name in ("M_Fixture_PBR", "M_Fixture_ORM", "M_Fixture_Masked",
                 "M_Fixture_Translucent"):
        asset = material_by_name(document, name)
        if not check(asset is not None, "manifest missing material " + name):
            continue
        check(asset.get("material_data") is not None,
              "%s should have material_data" % name)

    unsupported = material_by_name(document, "M_Fixture_Unsupported")
    if check(unsupported is not None, "manifest missing M_Fixture_Unsupported"):
        check(unsupported.get("material_data") is None,
              "the deliberately unsupported material must have material_data "
              "None (default material fallback)")
    codes = {(r["code"], r["subject"]) for r in document["warnings"]}
    check(any(code == "MAT_EXPR_UNSUPPORTED" and "M_Fixture_Unsupported" in subject
              for code, subject in codes),
          "MAT_EXPR_UNSUPPORTED must be reported for M_Fixture_Unsupported")

    pbr = material_by_name(document, "M_Fixture_PBR")["material_data"]
    for key in ("base_color", "normal", "roughness", "metallic"):
        check(key in pbr["properties"], "PBR material lost property %r" % key)

    orm = material_by_name(document, "M_Fixture_ORM")["material_data"]
    assets_by_guid = {a["guid"]: a for a in document["assets"]}
    channels = {}
    for key in ("occlusion", "roughness", "metallic"):
        if not check(key in orm["properties"], "ORM material lost %r" % key):
            continue
        texture = assets_by_guid[orm["properties"][key]["texture_guid"]]
        channels[key] = texture.get("channel")
        check(texture["ue_path"] == "/Game/Textures/T_Fixture_ORM",
              "%s must come from the packed ORM texture" % key)
    check(channels == {"occlusion": "R", "roughness": "G", "metallic": "B"},
          "ORM split channels wrong: %r (expected R/G/B = AO/rough/metal)" % channels)

    check(material_by_name(document, "M_Fixture_Masked")["material_data"]["blend_mode"] == "masked",
          "Masked material lost its blend mode")
    check(material_by_name(document, "M_Fixture_Translucent")["material_data"]["blend_mode"] == "translucent",
          "Translucent material lost its blend mode")


def test_exported_textures(document):
    count = 0
    for asset in document["assets"]:
        if asset["kind"] != "texture":
            continue
        path = os.path.join(EXPORT_ASSETS, asset["o3de_relative_path"])
        if not check(os.path.exists(path), "exported texture missing: " + path):
            continue
        image = tga.read(path)  # parses = structurally valid
        count += 1
        if asset.get("channel"):
            # split output is replicated grayscale: B == G == R per pixel
            pixels = image["pixels"]
            stride = image["bpp"] // 8
            sample = range(0, min(len(pixels), 30 * stride), stride)
            check(all(pixels[i] == pixels[i + 1] == pixels[i + 2] for i in sample),
                  "%s: split output is not grayscale" % asset["o3de_relative_path"])
    print("  %d texture files verified" % count)


def test_staged_materials(document, project):
    project_assets = os.path.join(project, "Assets")

    def load(name):
        asset = material_by_name(document, name)
        path = os.path.join(project_assets, asset["o3de_relative_path"])
        if not check(os.path.exists(path), "staged material missing: " + path):
            return None, None
        with open(path) as handle:
            return json.load(handle), os.path.dirname(path)

    pbr, folder = load("M_Fixture_PBR")
    if pbr:
        values = pbr["propertyValues"]
        for key in ("baseColor.textureMap", "normal.textureMap",
                    "roughness.textureMap", "metallic.textureMap"):
            if not check(key in values, "PBR material JSON lost %s" % key):
                continue
            target = os.path.normpath(os.path.join(folder, values[key]))
            check(os.path.exists(target),
                  "%s reference does not resolve: %s" % (key, target))
        check(values.get("normal.flipY") is True,
              "normal.flipY must be true (UE normals are DirectX-style; "
              "silent ugly lighting otherwise)")
        check("opacity.mode" not in values, "opaque material must not set opacity.mode")

    orm, folder = load("M_Fixture_ORM")
    if orm:
        values = orm["propertyValues"]
        for key in ("occlusion.diffuseTextureMap", "roughness.textureMap",
                    "metallic.textureMap"):
            check(key in values, "ORM material JSON lost %s" % key)
        maps = {values.get("occlusion.diffuseTextureMap"),
                values.get("roughness.textureMap"), values.get("metallic.textureMap")}
        check(len(maps) == 3, "ORM must reference three DISTINCT split maps, got %r" % maps)

    masked, folder = load("M_Fixture_Masked")
    if masked:
        values = masked["propertyValues"]
        check(values.get("opacity.mode") == "Cutout", "Masked must import as Cutout")
        check(values.get("opacity.alphaSource") == "Split",
              "Masked alpha must come from the split opacity map")
        check("opacity.textureMap" in values, "Masked lost its opacity map")

    translucent, folder = load("M_Fixture_Translucent")
    if translucent:
        values = translucent["propertyValues"]
        check(values.get("opacity.mode") == "Blended", "Translucent must import as Blended")
        check(values.get("opacity.factor") == 1.0,
              "opacity.factor must be 1.0 under a texture (the material type's "
              "default 0.5 would halve the alpha)")

    unsupported = material_by_name(document, "M_Fixture_Unsupported")
    path = os.path.join(project_assets, unsupported["o3de_relative_path"])
    check(not os.path.exists(path),
          "no .material may be written for the unsupported material -- its "
          "entities keep the default material")


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJECT
    if not project:
        print("FAIL: no project argument and O3DE_PROJECT_JOLT is not "
              "configured (Tests/paths.config or environment)")
        return 1
    scratch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "tga")
    os.makedirs(scratch, exist_ok=True)

    document = manifest_io.load(MANIFEST_PATH)

    for name, test in (
            ("tga split (synthetic, known channels)", lambda: test_tga_split_synthetic(scratch)),
            ("parameter role matching (pure)", test_parameter_role_matching),
            ("packed channel order ORM/ARM/RMA/MRA (pure)",
             test_packed_channel_order_follows_the_token),
            ("one texture on several roles is packed (pure)",
             test_one_texture_on_several_roles_is_a_packed_map),
            ("manifest material_data", lambda: test_manifest_material_data(document)),
            ("exported texture files", lambda: test_exported_textures(document)),
            ("staged .material JSON", lambda: test_staged_materials(document, project)),
    ):
        before = len(failures)
        print("== %s ==" % name)
        test()
        print("  %s" % ("ok" if len(failures) == before else "FAILED"))

    print("")
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

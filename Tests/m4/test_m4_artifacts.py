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
DEFAULT_PROJECT = r"C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"

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
    channel-index bug, so a synthetic 2x1 image with distinct channels does."""
    source = os.path.join(scratch, "synthetic.tga")
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 1, 32, 8)
    # BGRA: pixel0 = (B=10, G=20, R=30, A=40), pixel1 = (50, 60, 70, 80)
    with open(source, "wb") as handle:
        handle.write(header + bytes([10, 20, 30, 40, 50, 60, 70, 80]))

    for channel, expected in (("R", (30, 70)), ("G", (20, 60)),
                              ("B", (10, 50)), ("A", (40, 80))):
        out = os.path.join(scratch, "split_%s.tga" % channel)
        tga.write_grayscale_from_channel(source, out, channel)
        image = tga.read(out)
        got = (image["pixels"][0], image["pixels"][3])
        check(got == expected,
              "channel %s split produced %r, expected %r" % (channel, got, expected))

    # A from a 24-bpp source is implicitly opaque -> solid white.
    source24 = os.path.join(scratch, "synthetic24.tga")
    header24 = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 24, 0)
    with open(source24, "wb") as handle:
        handle.write(header24 + bytes([1, 2, 3]))
    out = os.path.join(scratch, "split24_A.tga")
    tga.write_grayscale_from_channel(source24, out, "A")
    check(tga.read(out)["pixels"][0] == 255,
          "alpha of a 24-bpp image must split as opaque white")


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
    scratch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "tga")
    os.makedirs(scratch, exist_ok=True)

    document = manifest_io.load(MANIFEST_PATH)

    for name, test in (
            ("tga split (synthetic, known channels)", lambda: test_tga_split_synthetic(scratch)),
            ("parameter role matching (pure)", test_parameter_role_matching),
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

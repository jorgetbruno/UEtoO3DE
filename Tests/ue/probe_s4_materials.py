"""
probe_s4_materials.py — spike S4.0 (plan M4): how bad is material coverage really?

    "Run the graph serializer over one real non-fixture UE material and count
     what falls outside the supported subset. If coverage is hopeless, cut
     scope to 'flatten to base colour + textures' before building the full
     converter. This is the plan's biggest unknown; buy information first."

Two questions, answered by measurement:

  1. What can UE 5.8's Python actually read of a material graph? The expected
     surface is `MaterialEditingLibrary.get_material_property_input_node`
     (the expression feeding each material property) -- verified here, not
     assumed, along with what a TextureSample expression exposes.
  2. Coverage: for EVERY material in the EasternProvince project and every
     fixture material, classify each driven property as inside or outside the
     supported subset (TextureSample -> BaseColor/Normal/Roughness/Metallic;
     Constant/Constant3Vector/VertexColor recognized; Multiply for tints).

Also dumps what a UTexture2D exposes (srgb, compression) and tests one
texture export to TGA -- the format the ORM splitter will parse.

Run against EasternProvince (its own project, plugins irrelevant):
  Tests/ue/export_level.bat sets UEO3DE_MAP; this probe instead uses:
  UnrealEditor-Cmd.exe <EP.uproject> -run=pythonscript -script=<this> ...
Output: Tests/ue/results/probe_s4_materials.txt
"""

import os
import traceback

import unreal

OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results"
OUT_PATH = OUT_DIR + "/probe_s4_materials.txt"
SCRATCH = OUT_DIR + "/s4"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_S4] " + str(msg))


# Properties the subset maps, in StandardPBR terms.
PROPERTIES = [
    ("MP_BASE_COLOR", "BaseColor"),
    ("MP_NORMAL", "Normal"),
    ("MP_ROUGHNESS", "Roughness"),
    ("MP_METALLIC", "Metallic"),
    ("MP_SPECULAR", "Specular"),
    ("MP_EMISSIVE_COLOR", "Emissive"),
    ("MP_OPACITY", "Opacity"),
    ("MP_OPACITY_MASK", "OpacityMask"),
    ("MP_AMBIENT_OCCLUSION", "AO"),
]

SUPPORTED_EXPRESSIONS = {
    "MaterialExpressionTextureSample",
    "MaterialExpressionTextureSampleParameter2D",
    "MaterialExpressionConstant",
    "MaterialExpressionConstant3Vector",
    "MaterialExpressionConstant4Vector",
    "MaterialExpressionScalarParameter",
    "MaterialExpressionVectorParameter",
    "MaterialExpressionMultiply",
}


def classify_material(material):
    """Return (driven, supported, unsupported_kinds) for one material."""
    mel = unreal.MaterialEditingLibrary
    driven = []
    unsupported = []
    for enum_name, label in PROPERTIES:
        prop = getattr(unreal.MaterialProperty, enum_name, None)
        if prop is None:
            continue
        try:
            node = mel.get_material_property_input_node(material, prop)
        except Exception as exc:
            out("      %s: get_material_property_input_node raised %r" % (label, exc))
            continue
        if node is None:
            continue
        kind = node.get_class().get_name()
        driven.append((label, kind))
        if kind not in SUPPORTED_EXPRESSIONS:
            unsupported.append((label, kind))
    return driven, unsupported


def probe_api_surface():
    out("=== 1. graph-reading API surface (fixture M_Fixture_PBR if present) ===")
    mel = unreal.MaterialEditingLibrary
    members = [m for m in dir(mel) if "input" in m.lower() or "property" in m.lower()
               or "connect" in m.lower() or "parameter" in m.lower()]
    out("  MaterialEditingLibrary members of interest:")
    for member in sorted(members):
        out("    " + member)


def probe_texture_surface(texture):
    out("  texture %s:" % texture.get_path_name())
    for prop in ("srgb", "compression_settings", "lod_group", "source_color_settings"):
        try:
            out("    %s = %r" % (prop, texture.get_editor_property(prop)))
        except Exception as exc:
            out("    %s: %r" % (prop, exc))
    try:
        out("    size: %dx%d" % (texture.blueprint_get_size_x(), texture.blueprint_get_size_y()))
    except Exception:
        pass


def probe_texture_export(texture):
    out("=== 3. texture export to TGA (the ORM splitter's input format) ===")
    os.makedirs(SCRATCH, exist_ok=True)
    path = SCRATCH + "/" + texture.get_name() + ".tga"
    task = unreal.AssetExportTask()
    task.object = texture
    task.filename = path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    okay = unreal.Exporter.run_asset_export_task(task)
    size = os.path.getsize(path) if os.path.exists(path) else -1
    out("  export %s -> ok=%s, %d bytes" % (path, okay, size))
    if size > 18:
        with open(path, "rb") as handle:
            header = handle.read(18)
        out("  TGA header: type=%d bpp=%d size=%dx%d"
            % (header[2], header[16],
               header[12] | header[13] << 8, header[14] | header[15] << 8))


def sweep_project_materials():
    out("=== 2. COVERAGE SWEEP over every material in the project ===")
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_class(
        unreal.TopLevelAssetPath("/Script/Engine", "Material"), True)
    out("  %d Material assets found" % len(assets))

    totals = {"materials": 0, "fully_supported": 0, "partial": 0, "instances_skipped": 0}
    kind_counts = {}
    sample_texture = None

    for data in assets:
        package = str(data.package_name)
        if package.startswith("/Engine/"):
            continue
        material = unreal.EditorAssetLibrary.load_asset(package)
        if material is None or not isinstance(material, unreal.Material):
            continue
        totals["materials"] += 1
        driven, unsupported = classify_material(material)
        blend = str(material.get_editor_property("blend_mode"))
        two_sided = bool(material.get_editor_property("two_sided"))
        status = "OK " if not unsupported else "PART"
        totals["fully_supported" if not unsupported else "partial"] += 1
        out("  [%s] %-58s blend=%s two_sided=%s" % (status, package, blend.split(".")[-1], two_sided))
        for label, kind in driven:
            marker = " " if (label, kind) not in unsupported else "!"
            out("      %s %-11s <- %s" % (marker, label, kind))
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            if kind.startswith("MaterialExpressionTextureSample") and sample_texture is None:
                try:
                    sample_texture = mel_texture(material, label)
                except Exception:
                    pass

    # Material INSTANCES are a separate class -- count them so the scope is honest.
    instances = registry.get_assets_by_class(
        unreal.TopLevelAssetPath("/Script/Engine", "MaterialInstanceConstant"), True)
    game_instances = [d for d in instances if not str(d.package_name).startswith("/Engine/")]
    totals["instances_skipped"] = len(game_instances)

    out("")
    out("  --- totals ---")
    for key, value in sorted(totals.items()):
        out("  %-18s %d" % (key, value))
    out("  --- expression kinds seen ---")
    for kind, count in sorted(kind_counts.items(), key=lambda kv: -kv[1]):
        supported = "supported" if kind in SUPPORTED_EXPRESSIONS else "OUTSIDE SUBSET"
        out("  %4d  %-46s %s" % (count, kind, supported))
    return sample_texture


def mel_texture(material, label):
    """The texture object feeding a property, if a TextureSample drives it."""
    mel = unreal.MaterialEditingLibrary
    mapping = {"BaseColor": unreal.MaterialProperty.MP_BASE_COLOR,
               "Normal": unreal.MaterialProperty.MP_NORMAL,
               "Roughness": unreal.MaterialProperty.MP_ROUGHNESS,
               "Metallic": unreal.MaterialProperty.MP_METALLIC}
    node = mel.get_material_property_input_node(material, mapping.get(label))
    return node.get_editor_property("texture") if node else None


def main():
    probe_api_surface()
    sample = sweep_project_materials()
    if sample is not None:
        out("")
        out("=== texture surface + export ===")
        probe_texture_surface(sample)
        probe_texture_export(sample)


status = "PASS"
try:
    main()
except Exception:
    out("FATAL:")
    out(traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)

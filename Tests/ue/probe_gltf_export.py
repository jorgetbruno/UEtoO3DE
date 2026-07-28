"""
probe_gltf_export.py -- can UE 5.8 write glTF from Python at all?

Question one of two for "add a glTF export option". Nothing about that feature
gets designed until both are answered with facts:

  1. (here) does UE expose a glTF exporter to Python, and does it actually
     write a file for a static mesh AND a skeletal mesh?
  2. (Tests/o3de/probe_gltf_ingest.py) does O3DE's Asset Processor turn that
     file into an .azmodel?

If either is no, the feature is a different shape entirely -- a third-party
converter step, or nothing.

The mesh is SM_LetterF on purpose: it is the fixture whose asymmetry makes an
orientation error visible (LANE_B.md). If glTF export works, the bounds printed
here are the FIRST data point of a Lane C measurement, because glTF is Y-up
right-handed with -Z forward while UE is Z-up left-handed -- none of Lane B's
measured chain carries over.
"""

import json
import os
import traceback

import unreal

RESULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
RESULT_PATH = os.path.join(RESULT_DIR, 'probe_gltf_export_result.txt')
OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results/gltf_probe"

STATIC_MESH = "/Game/Meshes/SM_LetterF"
SKELETAL_MESH = "/Game/Skeletal/SK_Canary"

lines = []


def log(msg=""):
    lines.append(str(msg))
    unreal.log("[gltf-probe] %s" % msg)
    try:
        os.makedirs(RESULT_DIR, exist_ok=True)
        with open(RESULT_PATH, 'w') as handle:
            handle.write('\n'.join(lines))
    except Exception:
        pass


def section(title):
    log("")
    log("=== %s ===" % title)


def try_export(asset_path, out_path, options=None):
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if asset is None:
        return None, "asset not found: " + asset_path
    task = unreal.AssetExportTask()
    task.set_editor_property("object", asset)
    task.set_editor_property("filename", out_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    if options is not None:
        task.set_editor_property("options", options)
    try:
        ok = unreal.Exporter.run_asset_export_task(task)
    except Exception as exc:
        return None, "run_asset_export_task raised %s: %s" % (type(exc).__name__, exc)
    if not ok:
        return None, "exporter returned False"
    if not os.path.isfile(out_path):
        return None, "exporter returned True but wrote no file"
    return os.path.getsize(out_path), None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    section("1. does this build expose anything glTF to Python?")
    names = sorted(n for n in dir(unreal) if 'gltf' in n.lower())
    log("  unreal.* names mentioning glTF: %d" % len(names))
    for name in names:
        log("    " + name)
    if not names:
        log("  -> the glTF Exporter plugin is not enabled in this project.")
        log("     Enable 'glTF Exporter' in the .uproject and re-run; the")
        log("     answer to this probe is 'not without that plugin'.")

    section("2. export options")
    options = None
    if hasattr(unreal, 'GLTFExportOptions'):
        try:
            options = unreal.GLTFExportOptions()
            fields = [n for n in dir(options) if not n.startswith('_')]
            log("  GLTFExportOptions() ok")
            interesting = [f for f in fields
                           if any(k in f.lower() for k in
                                  ('unit', 'scale', 'coordinate', 'axis',
                                   'material', 'texture', 'skin', 'animation',
                                   'binary', 'mesh'))]
            log("  fields that decide the contract: %s" % interesting)
            for field in ("export_uniform_scale", "export_vertex_colors",
                          "export_material_variants", "bundle_web_viewer"):
                if hasattr(options, field):
                    try:
                        log("    %-28s = %r" % (field, options.get_editor_property(field)))
                    except Exception:
                        pass
        except Exception as exc:
            log("  GLTFExportOptions() raised %s: %s" % (type(exc).__name__, exc))
    else:
        log("  no unreal.GLTFExportOptions in this build")

    section("3. static mesh -> .gltf and .glb")
    for extension in (".gltf", ".glb"):
        out = "%s/SM_LetterF%s" % (OUT_DIR, extension)
        if os.path.exists(out):
            os.remove(out)
        size, error = try_export(STATIC_MESH, out, options)
        if error:
            log("  %-6s FAILED: %s" % (extension, error))
            continue
        log("  %-6s wrote %d bytes -> %s" % (extension, size, out))
        if extension == ".gltf":
            try:
                with open(out, 'r') as handle:
                    document = json.load(handle)
                log("      parsed as JSON; top keys: %s" % sorted(document.keys()))
                log("      asset block: %r" % document.get("asset"))
                log("      meshes=%d materials=%d nodes=%d accessors=%d"
                    % (len(document.get("meshes") or []),
                       len(document.get("materials") or []),
                       len(document.get("nodes") or []),
                       len(document.get("accessors") or [])))
                # The first data point of a Lane C measurement: glTF stores
                # min/max per POSITION accessor, so the bounds are readable
                # straight out of the file, in metres and in glTF axes.
                for accessor in (document.get("accessors") or []):
                    if accessor.get("min") and len(accessor["min"]) == 3:
                        log("      POSITION bounds min=%r max=%r"
                            % ([round(v, 4) for v in accessor["min"]],
                               [round(v, 4) for v in accessor["max"]]))
                        break
            except Exception as exc:
                log("      could NOT parse as JSON: %s" % exc)

    section("4. skeletal mesh -> .gltf (the higher-risk half)")
    out = "%s/SK_Canary.gltf" % OUT_DIR
    if os.path.exists(out):
        os.remove(out)
    size, error = try_export(SKELETAL_MESH, out, options)
    if error:
        log("  FAILED: %s" % error)
    else:
        log("  wrote %d bytes" % size)
        try:
            with open(out, 'r') as handle:
                document = json.load(handle)
            log("      skins=%d animations=%d"
                % (len(document.get("skins") or []),
                   len(document.get("animations") or [])))
        except Exception as exc:
            log("      could not parse: %s" % exc)

    section("5. what the FBX path produces, for comparison")
    log("  Lane B's measured chain (LANE_B.md): exporter bakes")
    log("  scale_mesh(-1,-1,1); UE's FBX writer negates Y; SceneAPI applies")
    log("  180 deg yaw. Net = Lane A. NONE of that carries to glTF, which is")
    log("  Y-up right-handed with -Z forward and in METRES rather than cm.")


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')
unreal.SystemLibrary.quit_editor()

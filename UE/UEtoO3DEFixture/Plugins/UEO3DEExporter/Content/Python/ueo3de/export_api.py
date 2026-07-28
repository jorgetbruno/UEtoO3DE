"""
export_api.py — one export entry point, for the button and for CI (plan M10).

Until M10 the export orchestration lived in `Tests/ue/export_fixture.py`:
manifest, then static FBX, then skeletal FBX, then textures, in that order and
with the count assertions between them. That was fine while the only caller
was a test. It stops being fine the moment a menu item exports too, because
then the thing users click is not the thing CI checks -- and the difference
would show up as "works in CI, does nothing from the menu", which is the
worst kind of bug to be told about second-hand.

So the sequence lives here, in the plugin, and both callers drive it:

    Tests/ue/export_fixture.py   adds its FBX-bounds verification around it
    ueo3de.ue_menu               wraps it in a ScopedSlowTask and a folder picker

`progress` is an optional callable `(index, total, label)`. It exists so the
UI can show a real progress bar without the export knowing what a progress bar
is; CI passes nothing.
"""

import os

STEPS = ("Reading the level", "Exporting static meshes",
         "Exporting skeletal meshes and animations", "Exporting textures")


class ExportError(RuntimeError):
    pass


def export_level(map_path, output_dir, log=None, progress=None, load=True):
    """Export one UE level to a UEtoO3DE interchange folder.

    Writes `<output_dir>/manifest.json` and `<output_dir>/Assets/**`.
    Returns a summary dict; raises ExportError if a stage produced fewer files
    than the manifest promised, because a manifest that references an asset
    nobody wrote is a broken import waiting to happen on the other side.

    `load=False` exports the level as it stands in memory instead of reloading
    it from disk. The menu item uses it, because reloading would discard the
    user's unsaved edits before exporting the older version on disk.
    """
    from . import mesh_export
    from . import ue_level

    def emit(message):
        if log is not None:
            log(message)

    def step(index):
        if progress is not None:
            progress(index, len(STEPS), STEPS[index])

    output_dir = str(output_dir).replace("\\", "/").rstrip("/")
    manifest_path = output_dir + "/manifest.json"
    assets_root = output_dir + "/Assets"
    raw_textures = output_dir + "/RawTextures"
    os.makedirs(output_dir, exist_ok=True)

    step(0)
    emit("== manifest ==")
    document, warnings, asset_table = ue_level.export_level(
        map_path, manifest_path, load=load)
    emit("  wrote " + manifest_path)
    emit("  entities: %d  assets: %d  warnings: %d"
         % (len(document["entities"]), len(document["assets"]), len(warnings)))

    step(1)
    emit("== static mesh FBX export ==")
    exported = mesh_export.export_meshes(document["assets"], assets_root, log=log)
    mesh_assets = [a for a in document["assets"] if a["kind"] == "static_mesh"]
    _require(len(exported), len(mesh_assets), "static mesh FBX files")

    step(2)
    emit("== skeletal mesh + animation FBX export ==")
    skeletal = mesh_export.export_skeletal(document["assets"], assets_root, log=log)
    skeletal_assets = [a for a in document["assets"]
                       if a["kind"] in ("skeletal_mesh", "animation")]
    _require(len(skeletal), len(skeletal_assets), "skeletal/animation FBX files")

    step(3)
    emit("== texture export ==")
    textures = asset_table.texture_bank.export_all(assets_root, raw_textures, log=log)
    texture_assets = [a for a in document["assets"] if a["kind"] == "texture"]
    _require(len(textures), len(texture_assets), "texture files")

    return {
        "manifest_path": manifest_path,
        "output_dir": output_dir,
        "assets_root": assets_root,
        "document": document,
        "warnings": warnings,
        "asset_table": asset_table,
        "static_meshes": exported,
        "skeletal": skeletal,
        "textures": textures,
        "counts": {
            "entities": len(document["entities"]),
            "assets": len(document["assets"]),
            "static_meshes": len(exported),
            "skeletal": len(skeletal),
            "textures": len(textures),
            "warnings": len(warnings),
        },
    }


def _require(written, promised, what):
    if written != promised:
        raise ExportError(
            "wrote %d %s but the manifest references %d. An import that "
            "follows this manifest would look for a file that was never "
            "written." % (written, what, promised))


def summary_text(result):
    """Human-readable result, for the menu's completion dialog."""
    counts = result["counts"]
    warnings = result["warnings"]
    lines = [
        "Exported to: %s" % result["output_dir"],
        "",
        "Entities:        %d" % counts["entities"],
        "Assets:          %d" % counts["assets"],
        "  static meshes: %d" % counts["static_meshes"],
        "  skeletal:      %d" % counts["skeletal"],
        "  textures:      %d" % counts["textures"],
        "Warnings:        %d" % counts["warnings"],
    ]
    try:
        from .warnings import ERROR, WARN
        lines.append("  errors:        %d" % warnings.count_by_severity(ERROR))
        lines.append("  warn:          %d" % warnings.count_by_severity(WARN))
    except Exception:
        pass
    return "\n".join(lines)

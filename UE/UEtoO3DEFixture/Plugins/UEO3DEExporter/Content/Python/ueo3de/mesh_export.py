"""
mesh_export.py — FBX export of the static meshes a manifest references (plan M2).

--------------------------------------------------------------------------
Lane B: who applies the reflection (measured, M2)
--------------------------------------------------------------------------
UE is left-handed and O3DE is right-handed, so geometry needs the same
determinant -1 map Lane A applies to transforms. **UE's FBX exporter already
applies it**: it performs the left- to right-handed conversion on the way out,
which for UE means negating Y.

That is measured, not assumed -- `Tests/ue/probe_m2_fbx_handedness.py` exports
the same mesh twice:

    UE source asset      y = [-12.500, 37.500]
    FBX from source      y = [-37.500, 12.500]      <- negated by the exporter
    baked mirrored asset y = [-37.500, 12.500]
    FBX from mirrored    y = [-12.500, 37.500]      <- mirror cancelled out

So this module must NOT mirror anything. An earlier revision did, and the two
reflections cancelled: the FBX came out with the original UE geometry, which
would have placed mirrored meshes under correct transforms.

M0's spike S0.2 concluded the exporter writes geometry "verbatim". That
conclusion was drawn from a canary mesh that happened to be symmetric about Y,
which made a Y negation invisible. The rebuilt canary is asymmetric on all
three axes, which is what made this measurable. LANE_B.md carries the
correction.

The remaining Lane B correction is units only: SceneAPI applies no unit
conversion, so the `.assetinfo` sidecar carries `scale: 0.01`.

--------------------------------------------------------------------------
Export options
--------------------------------------------------------------------------
Two are set deliberately rather than left at their defaults:

  * `collision = False`. UE exports simple collision as extra `UCX_` nodes.
    SM_LetterF has none (which is why S0.2 saw a clean file with stock
    options), but the engine cube, sphere and cylinder all do, and those nodes
    would reach SceneAPI as extra meshes. Collision travels in the manifest.
  * `level_of_detail = False`. LOD chains are M9; exporting them now would add
    nodes the `.assetinfo` does not name.
"""

import os

import unreal


class MeshExportError(Exception):
    pass


def _make_export_options():
    options = unreal.FbxExportOption()
    required = {
        "collision": False,
        "level_of_detail": False,
    }
    for name, value in required.items():
        try:
            options.set_editor_property(name, value)
        except Exception as exc:
            # Load-bearing: falling back to the default would put UCX_ and LOD
            # nodes in the FBX that the importer's .assetinfo does not name.
            raise MeshExportError(
                "FbxExportOption.%s could not be set (%s); the exported FBX "
                "would carry nodes the importer does not expect" % (name, exc))
    return options


def _export_fbx(asset, output_path, options):
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    task = unreal.AssetExportTask()
    task.object = asset
    task.filename = output_path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    task.options = options
    if not unreal.Exporter.run_asset_export_task(task):
        raise MeshExportError("FBX export failed for " + output_path)
    if not os.path.exists(output_path):
        raise MeshExportError("FBX export reported success but wrote nothing: " + output_path)


def source_bounds(mesh):
    """The asset's local AABB in UE space (centimetres)."""
    box = mesh.get_bounding_box()
    return ([box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z])


def export_meshes(assets, output_root, log=None):
    """Export every static_mesh asset entry to `<output_root>/<relative path>`.

    Returns one record per exported FBX -- one per unique GUID, which is what
    makes the manifest's deduplication observable on disk. Each record carries
    the source asset's UE-space bounds so the caller can check the written file
    against Lane A without reopening the asset.
    """
    def emit(message):
        if log is not None:
            log(message)

    options = _make_export_options()
    exported = []
    seen_guids = set()

    for asset in assets:
        if asset.get("kind") != "static_mesh":
            continue
        guid = asset["guid"]
        if guid in seen_guids:
            # The manifest already deduplicates by GUID; belt and braces so a
            # future manifest bug cannot silently double-export.
            raise MeshExportError("duplicate static mesh GUID in manifest: " + guid)
        seen_guids.add(guid)

        source = unreal.EditorAssetLibrary.load_asset(asset["ue_path"])
        if source is None:
            raise MeshExportError("could not load source mesh " + asset["ue_path"])

        output_path = os.path.join(output_root, asset["o3de_relative_path"]).replace("\\", "/")
        _export_fbx(source, output_path, options)

        bounds_min, bounds_max = source_bounds(source)
        exported.append({
            "guid": guid,
            "ue_path": asset["ue_path"],
            "relative_path": asset["o3de_relative_path"],
            # UE names the FBX mesh node after the asset; the importer builds
            # `RootNode.<this>` for the .assetinfo node selection.
            "node_name": source.get_name(),
            "ue_bounds_min": bounds_min,
            "ue_bounds_max": bounds_max,
            "bytes": os.path.getsize(output_path),
        })
        emit("  %-42s -> %-46s (%d bytes, node %r)"
             % (asset["ue_path"], asset["o3de_relative_path"],
                exported[-1]["bytes"], exported[-1]["node_name"]))

    return exported

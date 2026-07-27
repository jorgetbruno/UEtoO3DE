"""
staging.py — copy exported FBX files into the O3DE project so AP can see them.

PURE (stdlib only). Deliberately runs outside the editor: putting the source
files and their `.assetinfo` sidecars in place is plain file I/O, and doing it
as a separate step means CI can run `AssetProcessorBatch` to completion *before*
the editor starts. The editor-side import then still calls `wait_for_asset`
before referencing any product (global constraint 8) -- it just usually returns
immediately, instead of being the only thing standing between the importer and
a missing-asset reference.

Product paths follow from where the files land: a source at
`<project>/Assets/uetoo3de/game/meshes/sm_letterf.fbx` produces
`assets/uetoo3de/game/meshes/sm_letterf.fbx.azmodel`, because the project root
is the scan folder and AP lowercases product paths (observed in S0.1 and S0.2).
"""

import os
import shutil

from . import assetinfo


class StagingError(Exception):
    pass


def product_path_for(relative_path, product_prefix):
    """`uetoo3de/a/b.fbx` -> `assets/uetoo3de/a/b.fbx.azmodel` (lowercased)."""
    return ("%s/%s.azmodel" % (product_prefix, relative_path)).lower()


def stage(document, source_root, project_assets_root, log=None):
    """Copy every static mesh FBX and write its `.assetinfo`.

    Returns one record per mesh asset:
        {guid, ue_path, relative_path, source_fbx, staged_fbx, assetinfo,
         product_path}
    """
    def emit(message):
        if log is not None:
            log(message)

    product_prefix = os.path.basename(os.path.normpath(project_assets_root)).lower()
    records = []

    for asset in document["assets"]:
        if asset["kind"] != "static_mesh":
            continue

        relative_path = asset["o3de_relative_path"]
        source_fbx = os.path.join(source_root, relative_path).replace("\\", "/")
        if not os.path.exists(source_fbx):
            raise StagingError(
                "exported FBX is missing for %s: %s (run the UE export first)"
                % (asset["ue_path"], source_fbx))

        node_name = asset.get("fbx_node_name")
        if not node_name:
            raise StagingError(
                "%s has no fbx_node_name; the .assetinfo node path cannot be "
                "built and the Asset Processor job would fail" % asset["ue_path"])

        staged_fbx = os.path.join(project_assets_root, relative_path).replace("\\", "/")
        os.makedirs(os.path.dirname(staged_fbx), exist_ok=True)
        shutil.copyfile(source_fbx, staged_fbx)
        sidecar = assetinfo.write(staged_fbx, node_name)

        record = {
            "guid": asset["guid"],
            "ue_path": asset["ue_path"],
            "relative_path": relative_path,
            "source_fbx": source_fbx,
            "staged_fbx": staged_fbx,
            "assetinfo": sidecar,
            "product_path": product_path_for(relative_path, product_prefix),
        }
        records.append(record)
        emit("  %-42s -> %s" % (asset["ue_path"], record["product_path"]))

    return records


def clear(project_assets_root, subfolder, log=None):
    """Remove a previously staged tree, for cold-cache runs.

    Only ever deletes inside `<project_assets_root>/<subfolder>`, and only a
    path that actually resolves under it -- this deletes files, so it refuses
    to act on anything it cannot prove is in bounds.
    """
    root = os.path.abspath(project_assets_root)
    target = os.path.abspath(os.path.join(root, subfolder))
    if not target.startswith(root + os.sep):
        raise StagingError("refusing to clear %r: outside %r" % (target, root))
    if os.path.isdir(target):
        shutil.rmtree(target)
        if log is not None:
            log("  cleared " + target)

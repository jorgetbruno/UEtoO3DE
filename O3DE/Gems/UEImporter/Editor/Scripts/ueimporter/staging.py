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
    """Stage everything a manifest references into the project.

    Static meshes: copy FBX + write `.assetinfo` (M2). Textures: copy the
    role-suffixed TGAs -- the suffix alone selects the Atom image preset, so
    no sidecar is needed (M4). Materials with material_data: write the
    StandardPBR `.material` file (M4); materials without stay unwritten so
    their entities keep the default material.

    Returns one record per staged file with kind + product_path; only records
    whose products the importer must wait on carry `wait: True` (models and
    materials -- image products are dependencies of the material job).
    """
    from . import material_build

    def emit(message):
        if log is not None:
            log(message)

    product_prefix = os.path.basename(os.path.normpath(project_assets_root)).lower()
    assets_by_guid = {a["guid"]: a for a in document["assets"]}
    records = []

    for asset in document["assets"]:
        if asset["kind"] == "texture":
            relative_path = asset["o3de_relative_path"]
            source = os.path.join(source_root, relative_path).replace("\\", "/")
            if not os.path.exists(source):
                raise StagingError("exported texture missing for %s: %s"
                                   % (asset["ue_path"], source))
            staged = os.path.join(project_assets_root, relative_path).replace("\\", "/")
            os.makedirs(os.path.dirname(staged), exist_ok=True)
            shutil.copyfile(source, staged)
            records.append({"kind": "texture", "guid": asset["guid"],
                            "relative_path": relative_path, "staged": staged,
                            "wait": False})
            continue

        if asset["kind"] == "material":
            if not asset.get("material_data"):
                continue  # default material by design; nothing to write
            path = material_build.write(asset, assets_by_guid, project_assets_root)
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": "material", "guid": asset["guid"],
                "relative_path": relative_path, "staged": path,
                "staged_fbx": path,  # wait_for_asset names this in timeouts
                "product_path": ("%s/%s" % (product_prefix,
                                            relative_path.rsplit(".", 1)[0]
                                            + ".azmaterial")).lower(),
                "wait": True,
            })
            emit("  %-42s -> %s" % (asset["ue_path"], records[-1]["product_path"]))
            continue

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
            "kind": "static_mesh",
            "guid": asset["guid"],
            "ue_path": asset["ue_path"],
            "relative_path": relative_path,
            "source_fbx": source_fbx,
            "staged_fbx": staged_fbx,
            "assetinfo": sidecar,
            "product_path": product_path_for(relative_path, product_prefix),
            "wait": True,
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

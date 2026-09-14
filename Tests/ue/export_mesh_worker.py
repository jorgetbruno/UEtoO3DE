"""
export_mesh_worker.py — one worker editor of a wide mesh export (runs inside UE).

Launched by Tests/ue/export_level.py when UEO3DE_MESH_WORKERS > 1, on an EMPTY
map, never on the level: it exports only meshes that load as standalone assets
(see ueo3de/export_slices.py). Arguments, all forward-slashed:

    <worker index> <worker count> <manifest.json> <assets root> <done file> [<kind> <level map>]

`kind` is "standalone" (default: an empty map, meshes that load as assets) or
"spline" (opens <level map> first, then bakes its share of the spline meshes).

Writes `<done file>` atomically when finished -- records on success, the error
on failure -- and quits the editor. The lead treats a missing done file as a
failed worker, never as an empty slice.
"""

import json
import os
import sys
import time
import traceback

import unreal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))).replace("\\", "/")
PACKAGE_ROOT = REPO_ROOT + "/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python"
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

ARGS = [a for a in sys.argv[1:] if a.strip() and not a.startswith("-")]


def write_done(path, payload):
    temp = path + ".tmp"
    with open(temp, "w") as handle:
        json.dump(payload, handle)
    os.replace(temp, path)


def main():
    index, count = int(ARGS[0]), int(ARGS[1])
    manifest_path, assets_root, done_path = ARGS[2], ARGS[3], ARGS[4]
    kind = ARGS[5] if len(ARGS) > 5 else "standalone"
    started = time.time()
    payload = {"worker": index, "workers": count, "kind": kind, "records": [], "error": None}
    try:
        # Each editor bakes into its own temp package directory: the export
        # deletes that directory when it finishes, which must never happen
        # under another editor's in-flight bake.
        os.environ["UEO3DE_TEMP_SUFFIX"] = "_%s%d" % (kind[0], index)
        from ueo3de import export_slices, mesh_export
        with open(manifest_path, "r") as handle:
            document = json.load(handle)
        if kind == "spline":
            loaded = time.time()
            unreal.EditorLoadingAndSavingUtils.load_map(ARGS[6])
            payload["level_load_seconds"] = round(time.time() - loaded, 1)
            mine = export_slices.spline_worker_meshes(document["assets"], index, count)
        else:
            mine = export_slices.worker_meshes(document["assets"], index, count)
        payload["assigned"] = len(mine)
        payload["records"] = mesh_export.export_meshes(mine, assets_root)
        payload["bake_stats"] = mesh_export.bake_stats()
    except Exception:
        payload["error"] = traceback.format_exc()
    payload["seconds"] = round(time.time() - started, 1)
    write_done(done_path, payload)


try:
    main()
finally:
    unreal.SystemLibrary.quit_editor()

"""
importer.py — orchestration for the O3DE side of the import (plan M2).

Two entry points, because they run in different processes:

  `stage_only`  pure file I/O -- copies the exported FBX files into the project
                and writes their `.assetinfo` sidecars. Needs no editor, so CI
                can run it, then run AssetProcessorBatch to completion, then
                start the editor. Determinism instead of a race.

  `import_level` the editor half -- waits for every product asset it is about
                to reference (constraint 8), creates the entities, and saves
                the prefab.

`import_level` calls `wait_for_asset` even when CI has already run AP to
completion. That is the point: the barrier has to be in the code path that
references the asset, not in the shell script that usually happens to run
first, or M10's interactive import (where AP is live and lagging) has no
protection at all.
"""

import os

from . import manifest_io
from . import staging
from .report import Report


def stage_only(manifest_path, source_assets_root, project_assets_root, log=None):
    """Copy FBX + write `.assetinfo`. Returns (document, staged records)."""
    def emit(message):
        if log is not None:
            log(message)

    document = manifest_io.load(manifest_path)
    emit("manifest ok: schema %d, %d entities, %d assets"
         % (document["schema_version"], len(document["entities"]), len(document["assets"])))
    emit("staging into " + project_assets_root)
    records = staging.stage(document, source_assets_root, project_assets_root, log=log)
    emit("staged %d static mesh source files" % len(records))
    return document, records


def import_level(manifest_path, source_assets_root, project_assets_root,
                 prefab_path, level_name="DefaultLevel", asset_timeout=180.0,
                 restage=False, log=None):
    """Import a manifest into a saved `.prefab`. Returns (report, prefab_path)."""
    import azlmbr.legacy.general as general

    from . import asset_wait
    from . import prefab_build

    def emit(message):
        if log is not None:
            log(message)

    report = Report()
    document = manifest_io.load(manifest_path)

    # Re-deriving the staged records is cheap and keeps this entry point usable
    # on its own (M10's interactive import does not run a separate stage step).
    if restage:
        records = staging.stage(document, source_assets_root, project_assets_root, log=log)
    else:
        product_prefix = os.path.basename(os.path.normpath(project_assets_root)).lower()
        records = []
        for asset in manifest_io.static_mesh_assets(document):
            relative_path = asset["o3de_relative_path"]
            records.append({
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": staging.product_path_for(relative_path, product_prefix),
            })

    # Prefab authoring needs a root prefab instance, i.e. an open level (S0.1).
    general.idle_enable(True)
    general.open_level_no_prompt(level_name)
    general.idle_wait_frames(30)

    emit("waiting for %d product assets (timeout %.0fs each)"
         % (len(records), asset_timeout))
    asset_ids = asset_wait.wait_for_all(records, timeout_seconds=asset_timeout, log=emit)
    report.count("assets_waited_for", len(asset_ids))
    emit("  all %d products present in the catalog" % len(asset_ids))

    level_root_name = document["level"]["name"]
    emit("creating entities under level root %r" % level_root_name)
    level_root = prefab_build.create_level_root(level_root_name)
    created = prefab_build.create_entities(document, asset_ids, report, level_root, log=emit)
    report.count("entities_created", len(created))
    report.count("manifest_roots", sum(1 for item in document["entities"]
                                       if item["parent_id"] is None))
    if not created:
        raise prefab_build.PrefabBuildError("manifest produced no entities")

    emit("saving prefab")
    # One entity, at the origin: the container lands at the origin too, so
    # instantiating the prefab at the origin reproduces the level exactly.
    prefab_build.create_prefab_in_memory([level_root], prefab_path)
    prefab_build.flush_template_to_disk(prefab_path, level_root_name, log=emit)

    return report, prefab_path

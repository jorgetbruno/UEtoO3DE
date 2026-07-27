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
                 restage=False, backend=None, log=None):
    """Import a manifest into a saved `.prefab`. Returns (report, prefab_path).

    `backend` is the explicit physics backend name ('jolt'/'physx') or None to
    detect. Detection never guesses: if both backends resolve and no explicit
    choice is given, the import fails before authoring anything (constraint 5).
    """
    import json as json_module

    import azlmbr.legacy.general as general

    from . import asset_wait
    from . import physics_build
    from . import prefab_build
    from .adapters import detect_in_editor, make_adapter

    def emit(message):
        if log is not None:
            log(message)

    report = Report()
    document = manifest_io.load(manifest_path)

    # An open level comes FIRST: prefab authoring needs a root prefab instance
    # (S0.1), and the adapter's resolve step creates a scratch entity to read
    # the backend's contact offset -- entity creation without a level throws.
    general.idle_enable(True)
    general.open_level_no_prompt(level_name)
    general.idle_wait_frames(30)

    # --- physics backend: detect, resolve-or-fail, negotiate (M3) ---
    detection = detect_in_editor(explicit=backend)
    emit("physics backend: %s (source: %s, settings hint: %r)"
         % (detection["backend"], detection["source"], detection["settings_hint"]))
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    emit("  components resolved; contact offset %.4f m" % adapter.contact_offset())
    physics_build.negotiate(adapter, document, report)

    profiles_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "collision_profiles.json")
    with open(profiles_path, "r") as handle:
        all_profiles = json_module.load(handle)
    profile_map = {k: v for k, v in
                   (all_profiles.get(detection["backend"]) or {}).items()
                   if not k.startswith("_")}

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

    # --- physics authoring, all through the adapter (M3) ---
    # After the meshes: mesh colliders bake from the entity's own render model,
    # which must already be assigned (and its product waited for, above).
    emit("authoring physics through the %r adapter" % adapter.name())
    assets_by_guid = manifest_io.assets_by_guid(document)
    bodies = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        if entity_id is None:
            continue
        summary = physics_build.author_entity_physics(
            adapter, entity_id, item, assets_by_guid, report, profile_map)
        if summary:
            bodies += 1
            emit("  %-22s %s" % (item["name"], summary))
    report.count("physics_bodies", bodies)
    # Let mesh-collider bakes tick with models loaded before serialization.
    general.idle_wait_frames(60)
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

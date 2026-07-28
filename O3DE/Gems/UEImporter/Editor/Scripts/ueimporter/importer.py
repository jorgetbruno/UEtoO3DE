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
                 restage=False, backend=None, log=None, max_entities=None):
    """Import a manifest into a saved `.prefab`. Returns (report, prefab_path).

    `backend` is the explicit physics backend name ('jolt'/'physx') or None to
    detect. Detection never guesses: if both backends resolve and no explicit
    choice is given, the import fails before authoring anything (constraint 5).
    """
    import json as json_module

    import azlmbr.legacy.general as general

    from . import asset_wait
    from . import env_build
    from . import light_build
    from . import physics_build
    from . import prefab_build
    from .adapters import detect_in_editor, make_adapter

    def emit(message):
        if log is not None:
            log(message)

    report = Report()
    document = manifest_io.load(manifest_path)
    skip_indices = {int(i) for i in os.environ.get("UEO3DE_SKIP", "").split(",") if i.strip()}
    if skip_indices:
        document = dict(document)
        document["entities"] = [e for i, e in enumerate(document["entities"])
                                if i not in skip_indices]
    if max_entities is not None:
        # Diagnostic bisect knob (UEO3DE_MAX_ENTITIES): import only the first
        # N entities to localize scale- or content-dependent failures.
        keep = {e["id"] for e in document["entities"][:max_entities]}
        document = dict(document)
        document["entities"] = [e for e in document["entities"]
                                if e["id"] in keep and
                                (e["parent_id"] is None or e["parent_id"] in keep)]

    # An open level comes FIRST: prefab authoring needs a root prefab instance
    # (S0.1), and the adapter's resolve step creates a scratch entity to read
    # the backend's contact offset -- entity creation without a level throws.
    # BEFORE opening: a level holding an instance of the prefab this import is
    # about to rewrite makes CreatePrefabInMemory throw, and no amount of
    # settling helps. See prefab_build.detach_conflicting_instances.
    project_root = os.path.dirname(os.path.normpath(project_assets_root))
    report.count("stale_instances_removed",
                 prefab_build.detach_conflicting_instances(
                     project_root, level_name, prefab_path, log=emit))

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
                "kind": "static_mesh",
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": staging.product_path_for(relative_path, product_prefix),
                "wait": True,
            })
        for asset in manifest_io.skeletal_assets(document):
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": asset["kind"],
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": staging.skeletal_product_path_for(
                    relative_path, product_prefix, asset["kind"]),
                "wait": True,
            })
        for asset in document["assets"]:
            if asset["kind"] != "material" or not asset.get("material_data"):
                continue
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": "material",
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": ("%s/%s" % (product_prefix,
                                            relative_path.rsplit(".", 1)[0]
                                            + ".azmaterial")).lower(),
                "wait": True,
            })

    waitable = [record for record in records if record.get("wait")]
    emit("waiting for %d product assets (timeout %.0fs each)"
         % (len(waitable), asset_timeout))
    asset_ids = asset_wait.wait_for_all(waitable, timeout_seconds=asset_timeout, log=emit)
    report.count("assets_waited_for", len(asset_ids))
    emit("  all %d products present in the catalog" % len(asset_ids))

    mesh_asset_ids = {record["guid"]: asset_ids[record["guid"]]
                      for record in waitable if record["kind"] == "static_mesh"}
    material_asset_ids = {record["guid"]: asset_ids[record["guid"]]
                          for record in waitable if record["kind"] == "material"}
    skeletal_asset_ids = {record["guid"]: asset_ids[record["guid"]]
                          for record in waitable
                          if record["kind"] in ("skeletal_mesh", "animation")}

    level_root_name = document["level"]["name"]
    emit("creating entities under level root %r" % level_root_name)
    level_root = prefab_build.create_level_root(level_root_name)
    created = prefab_build.create_entities(document, mesh_asset_ids, report, level_root, log=emit)
    report.count("entities_created", len(created))

    # --- materials (M4): per entity, default slot or per-slot by label ---
    # A model whose mapped slots all share one material takes the default
    # slot (covers everything, no dependency on the model asset having
    # streamed in). Distinct materials per slot go through o3dimport's
    # label-matching technique in assign_material_slots.
    assets_by_guid = manifest_io.assets_by_guid(document)
    emit("assigning materials (%d converted)" % len(material_asset_ids))
    assigned = 0
    slots_assigned = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        # Skeletal entities carry the same per-slot structure; the Material
        # component consumes an Actor component's model the way it does a
        # Mesh component's (both are material consumers).
        mesh = item.get("mesh") or item.get("skeletal")
        if entity_id is None or mesh is None:
            continue
        slots = mesh.get("material_slots") or []
        mapped = [slot for slot in slots
                  if slot.get("material_guid") in material_asset_ids]
        if not mapped:
            continue  # unmapped material: the backend default stays, by design
        distinct = []
        for slot in mapped:
            if slot["material_guid"] not in distinct:
                distinct.append(slot["material_guid"])
        if len(distinct) == 1:
            prefab_build.assign_material(
                entity_id, material_asset_ids[distinct[0]], item["name"])
            assigned += 1
            continue
        # The LABEL is the mesh asset's own material name for that slot -- that
        # is what the baked FBX carries and what the azmodel slot is called.
        # The MATERIAL is the entity's effective one, which a component
        # override may have changed. Keying the label off the effective
        # material instead is the bug L_Showcase exposed: every tree overrides
        # its leaf material per instance, so no label ever matched and 97
        # entities silently kept the asset's default.
        mesh_asset = assets_by_guid.get(mesh["asset_guid"], {})
        asset_slot_names = mesh_asset.get("material_slot_material_names") or []
        assignments = []
        labels_seen = {}
        for slot in mapped:
            guid = slot["material_guid"]
            index = slot.get("index", 0)
            label = (asset_slot_names[index] if index < len(asset_slot_names)
                     else "") or assets_by_guid[guid]["name"]
            if label in labels_seen:
                if labels_seen[label] != guid:
                    report.warn("MAT_SLOT_LABEL_AMBIGUOUS", item["name"],
                                "slot label %r covers two different materials; "
                                "only the first can be assigned" % label)
                continue
            labels_seen[label] = guid
            assignments.append((label, material_asset_ids[guid]))
        slots_assigned += prefab_build.assign_material_slots(
            entity_id, assignments, item["name"], report)
        assigned += 1
    report.count("materials_assigned", assigned)
    report.count("material_slots_assigned", slots_assigned)

    # --- skeletal entities (M8): Actor + Simple Motion ---
    from . import skel_build
    emit("authoring skeletal entities")
    skeletal_authored = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        skeletal = item.get("skeletal")
        if entity_id is None or skeletal is None:
            continue
        plan = skel_build.plan_skeletal(skeletal, item["name"])
        skel_build.author_skeletal(
            entity_id, plan,
            skeletal_asset_ids.get(skeletal["asset_guid"]),
            skeletal_asset_ids.get(skeletal.get("animation_guid")),
            item["name"], prefab_build.resolve_component_type)
        skeletal_authored += 1
        emit("  %-22s Actor%s" % (
            item["name"],
            " + Simple Motion" if skeletal.get("animation_guid") else ""))
    report.count("skeletal_entities", skeletal_authored)

    # --- decals + cameras (M9) ---
    from . import camera_build
    from . import decal_build
    emit("authoring decals + cameras")
    decals = 0
    cameras = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        if entity_id is None:
            continue
        decal = item.get("decal")
        if decal is not None:
            material_asset = material_asset_ids.get(decal.get("material_guid"))
            plan = decal_build.plan_decal(decal, item["name"])
            if decal.get("material_guid") and material_asset is None:
                # Unconverted material: author the volume + sort key only,
                # and say so -- an invisible decal must never be silent.
                plan["properties"] = [p for p in plan["properties"]
                                      if p[1] != "material_asset"]
                report.warn("DECAL_MATERIAL_UNCONVERTED", item["name"],
                            "the decal's material did not convert; the decal "
                            "imports without a material")
            decal_build.author_decal(entity_id, plan, material_asset,
                                     item["name"],
                                     prefab_build.resolve_component_type)
            decals += 1
            emit("  %-22s Decal" % item["name"])
        camera = item.get("camera")
        if camera is not None:
            plan = camera_build.plan_camera(camera, item["name"])
            camera_build.author_camera(entity_id, plan, item["name"],
                                       prefab_build.resolve_component_type)
            cameras += 1
            emit("  %-22s Camera (v-fov %.2f deg)"
                 % (item["name"], plan["properties"][0][1]))
    report.count("decals_created", decals)
    report.count("cameras_created", cameras)

    # --- lights (M5) ---
    emit("authoring lights")
    lights = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        light = item.get("light")
        if entity_id is None or light is None:
            continue
        plan, light_warnings = light_build.plan_light(light, item["name"])
        for code, detail in light_warnings:
            report.warn(code, item["name"], detail)
        if plan is None:
            continue
        light_build.author_light(entity_id, plan, item["name"],
                                 prefab_build.resolve_component_type)
        lights += 1
        emit("  %-22s %s (%d properties)"
             % (item["name"], plan["component"], len(plan["properties"])))
    report.count("lights_created", lights)

    # --- environment (M6) ---
    # Sky first and only once: a level usually has both a SkyLight and a
    # SkyAtmosphere, and two Physical Sky components fight over the same sky.
    emit("authoring environment")
    environments = 0
    sky_authored = False
    # A SkyLight carries the artist's authored intensity; a SkyAtmosphere
    # carries scattering parameters Atom cannot represent at all. When a level
    # has both -- most do -- the skylight must win the one Physical Sky, or
    # that intensity is silently replaced by a default.
    def sky_first(item):
        kind = (item.get("environment") or {}).get("type")
        return 0 if kind == "skylight" else 1

    for item in sorted(document["entities"], key=sky_first):
        entity_id = created.get(item["id"])
        environment = item.get("environment")
        if entity_id is None or environment is None:
            continue
        plans, env_warnings = env_build.plan_environment(
            environment, item["name"], sky_already_authored=sky_authored)
        for code, detail in env_warnings:
            report.warn(code, item["name"], detail)
        if not plans:
            continue
        authored = env_build.author_environment(
            entity_id, plans, item["name"], prefab_build.resolve_component_type)
        if env_build.PHYSICAL_SKY in authored:
            sky_authored = True
        environments += 1
        emit("  %-22s %s" % (item["name"], ", ".join(authored)))
    report.count("environments_created", environments)

    # --- physics authoring, all through the adapter (M3) ---
    # After the meshes: mesh colliders bake from the entity's own render model,
    # which must already be assigned (and its product waited for, above).
    emit("authoring physics through the %r adapter" % adapter.name())
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
    # Each bake runs on the component's TickBus once its model streams in;
    # serializing mid-bake made CreatePrefabInMemory throw on a 135-collider
    # level, so the wait scales with the bake count.
    bake_count = report.counters.get("mesh_colliders", 0)
    # Material components stream their textures in asynchronously as well;
    # serializing while either is mid-flight makes CreatePrefabInMemory throw
    # (measured: cumulative threshold, not tied to any specific entity).
    # Per-slot assignments load one material instance each on top of the
    # per-entity ones, so they scale the wait too.
    # Actor + motion assets stream in asynchronously like materials do.
    general.idle_wait_frames(60 + 5 * bake_count + 5 * assigned + 5 * slots_assigned
                             + 10 * skeletal_authored)
    report.count("manifest_roots", sum(1 for item in document["entities"]
                                       if item["parent_id"] is None))
    if not created:
        raise prefab_build.PrefabBuildError("manifest produced no entities")

    emit("saving prefab")
    # One entity, at the origin: the container lands at the origin too, so
    # instantiating the prefab at the origin reproduces the level exactly.
    #
    # CreatePrefabInMemory surfaces internal failures as an opaque exception,
    # which this project twice mis-read as an asset-streaming race and "fixed"
    # with ever-longer settles. The real cause was a stale instance of the
    # target prefab inside the scratch level (see
    # prefab_build.detach_conflicting_instances) -- once removed, a 140-entity
    # level with 128 baked colliders saves on the FIRST attempt. The single
    # retry stays for genuine mid-bake serialization, but a failure here now
    # means something structural, not something to wait out.
    try:
        prefab_build.create_prefab_in_memory([level_root], prefab_path)
    except RuntimeError:
        emit("  CreatePrefabInMemory threw; settling 900 frames and retrying once")
        general.idle_wait_frames(900)
        prefab_build.create_prefab_in_memory([level_root], prefab_path)
    prefab_build.flush_template_to_disk(prefab_path, level_root_name, log=emit)

    return report, prefab_path

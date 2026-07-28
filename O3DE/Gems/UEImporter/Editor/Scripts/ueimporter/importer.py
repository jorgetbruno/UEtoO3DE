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


def settle_frames(bake_count, skeletal_authored):
    """Frames to idle after authoring, before serializing the prefab.

    It stays a blind constant, and that is a conclusion rather than a
    concession. Mesh colliders bake on their component's tick and the result is
    serialized into the prefab; serialize too early and the collider is written
    out with no geometry at all. Four probes looked for something to wait ON
    instead:

      * the bake appears in none of the collider's 17 reflected properties
      * a baked collider and an unbaked one read IDENTICALLY through every
        Python-visible call, compared side by side in one session
      * the in-memory template is a snapshot -- re-flushing it 12 times over
        3600 further frames recovered nothing
      * the prefab cannot be re-created in the same session ("Creating prefab
        as an override edit is currently not supported")

    So there is nothing to poll and nothing to repair. What makes a constant
    acceptable is the check that now follows the save: a bake that does not
    reach the file is reported as PHYS_COLLIDER_NOT_BAKED (error) instead of
    passing silently, which is what it did until it was measured.

    The numbers, on L_Showcase (2905 entities, 2501 mesh colliders, a Landscape
    whose baked data is 3 MB):

        settle    bakes in the file
             0    2486 / 2501   <- 15 lost, silently, import reported PASS
            30    2501 / 2501
           120    2501 / 2501
           200    2501 / 2501
          1500    2501 / 2501

    The old formula asked for 41,040 frames -- `60 + 5*bakes + 5*assigned +
    5*slots + 10*skeletal` -- against a real need somewhere under 30. It had
    grown across three rounds of tuning a `CreatePrefabInMemory` failure that
    turned out not to be a streaming race at all but a stale prefab instance in
    the scratch level (`prefab_build.detach_conflicting_instances`), and its
    terms were never re-measured once that cause was found.

    The two material terms are gone because they were measured to guard
    nothing: at settle=0 every material asset id in the prefab was identical to
    the control, and only cooked collider data differed. They were there
    against a serialization throw, which has its own retry below and which did
    not happen at settle=0 either.

    What remains is deliberately generous -- ~1550 frames on L_Showcase, some
    fifty times the measured need -- because the failure is unrecoverable
    within a session, one level on one machine is a thin basis for a threshold,
    and 20 s of a 125 s import is a cheap insurance premium. Collider count is
    a PROXY for bake work, which is really geometry volume; the proxy is
    acceptable only because being wrong is now loud. `UEO3DE_SETTLE_FRAMES`
    overrides it, which is how the table above was measured.

    The 10-per-skeletal term is unchanged and remains UNMEASURED: L_Showcase
    has no skeletal entities.
    """
    override = os.environ.get("UEO3DE_SETTLE_FRAMES", "").strip()
    if override:
        return int(override)
    return 300 + bake_count // 2 + 10 * skeletal_authored


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
                 restage=False, backend=None, log=None, max_entities=None,
                 reimport=True):
    """Import a manifest into a saved `.prefab`. Returns (report, prefab_path).

    `backend` is the explicit physics backend name ('jolt'/'physx') or None to
    detect. Detection never guesses: if both backends resolve and no explicit
    choice is given, the import fails before authoring anything (constraint 5).

    `reimport` (M10) makes a second import of the same prefab incremental: the
    previous import's ledger is consulted, entities are matched by manifest id,
    and anything the user edited by hand in O3DE is reported and KEPT rather
    than reverted. Pass False to ignore the ledger and author everything from
    the manifest -- the escape hatch for "just give me exactly what UE says".
    """
    import json as json_module
    import time as time_module

    import azlmbr.legacy.general as general

    from . import asset_wait
    from . import env_build
    from . import light_build
    from . import physics_build
    from . import prefab_build
    from . import reimport as reimport_module
    from .adapters import detect_in_editor, make_adapter

    def emit(message):
        if log is not None:
            log(message)

    report = Report()

    # A running stopwatch: `mark(name)` attributes everything since the last
    # mark to `name`. One line per phase boundary rather than a `with` block
    # around each -- the phases here are long sequential stretches, and
    # wrapping them would reindent most of this function for no extra
    # information. Because every mark closes the previous span, the figures
    # account for the WHOLE import rather than a chosen subset, which is the
    # property that makes them safe to reason about.
    _clock = [time_module.perf_counter()]

    def mark(name):
        now = time_module.perf_counter()
        report.timings[name] = report.timings.get(name, 0.0) + (now - _clock[0])
        _clock[0] = now

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

    # --- incremental re-import (M10) ---------------------------------------
    # Computed BEFORE anything is authored, because it reads the prefab as it
    # stands right now -- which is where the user's hand edits are. Once the
    # rebuild starts, that state is gone.
    previous_ledger = reimport_module.load_ledger(prefab_path) if reimport else None
    prefab_duplicates = set()
    transforms_before = reimport_module.read_prefab(prefab_path,
                                                   duplicates=prefab_duplicates)
    reimport_plan = reimport_module.plan(previous_ledger, document,
                                         transforms_before,
                                         prefab_duplicates=prefab_duplicates)
    emit(reimport_module.summarize(reimport_plan))
    if reimport and previous_ledger is None and transforms_before:
        report.warn("REIMPORT_LEDGER_MISSING", os.path.basename(prefab_path),
                    "a prefab exists at this path but has no ledger beside it; "
                    "hand edits in it cannot be detected and will be replaced")
    for name in sorted(set(reimport_plan["name_collisions"]) | prefab_duplicates):
        report.warn("REIMPORT_NAME_COLLISION", name,
                    "more than one entity carries this name (two manifest "
                    "entities, or an actor sharing the level root's name), so "
                    "hand edits on it cannot be told apart and are neither "
                    "detected nor preserved")
    for removed in reimport_plan["removed"]:
        report.warn("REIMPORT_ENTITY_REMOVED", removed["name"] or removed["id"],
                    "present in the previous import, absent from this manifest")
    for unmatched in reimport_plan["unmatched"]:
        report.warn("REIMPORT_ENTITY_UNMATCHED",
                    unmatched["name"] or unmatched["id"],
                    "the previous import authored this entity but the prefab "
                    "has no entity of that name; any hand edits on it cannot "
                    "be matched and are replaced")
    # "Added" means "new SINCE THE LAST IMPORT". On a first import every
    # entity is new in the trivial sense, and counting them all reads as
    # "12 actors appeared" on a report where nothing appeared -- so the
    # re-import counters stay at zero until there is a previous import to be
    # different from.
    if not reimport_plan["first_import"]:
        names_by_id = {e["id"]: e.get("name") for e in document["entities"]}
        for entity_id in reimport_plan["added"]:
            # Report the NAME, not the uuid: the subject column is what a user
            # reads to find the thing in their level, and a uuid5 identifies
            # nothing to them.
            report.warn("REIMPORT_ENTITY_ADDED", names_by_id.get(entity_id) or entity_id,
                        "new since the last import")
        report.count("reimport_added", len(reimport_plan["added"]))
    report.count("reimport_removed", len(reimport_plan["removed"]))
    report.count("reimport_conflicts", len(reimport_plan["conflicts"]))
    mark("reimport diff")

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
    mark("open level")

    # --- physics backend: detect, resolve-or-fail, negotiate (M3) ---
    detection = detect_in_editor(explicit=backend)
    emit("physics backend: %s (source: %s, settings hint: %r)"
         % (detection["backend"], detection["source"], detection["settings_hint"]))
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    emit("  components resolved; contact offset %.4f m" % adapter.contact_offset())
    physics_build.negotiate(adapter, document, report)
    mark("backend detect + resolve")

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

    mark("stage + resolve product paths")
    waitable = [record for record in records if record.get("wait")]
    emit("waiting for %d product assets (timeout %.0fs each)"
         % (len(waitable), asset_timeout))
    asset_ids = asset_wait.wait_for_all(waitable, timeout_seconds=asset_timeout, log=emit)
    report.count("assets_waited_for", len(asset_ids))
    emit("  all %d products present in the catalog" % len(asset_ids))
    mark("wait for product assets")

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
    mark("create entities")

    # --- materials (M4): per entity, default slot or per-slot by label ---
    # A model whose mapped slots all share one material takes the default
    # slot (covers everything, no dependency on the model asset having
    # streamed in). Distinct materials per slot go through o3dimport's
    # label-matching technique in assign_material_slots.
    assets_by_guid = manifest_io.assets_by_guid(document)
    emit("assigning materials (%d converted)" % len(material_asset_ids))
    prefab_build.reset_material_stats()
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
    mark("materials")
    # Sub-phases of the phase that turned out to BE half the import. Recorded
    # as timings so they appear beside the top-level rows, and as counters for
    # the frame budget, which is the actionable number: frames burned polling
    # for models to stream in are frames nobody chose to spend.
    for key, value in prefab_build.MATERIAL_STATS.items():
        if key.endswith("_s"):
            report.subtimings[key[:-2].replace("material_", "materials: ")] = value
        else:
            report.count(key, value)

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
    mark("skeletal")

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
    mark("decals + cameras")

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
    mark("lights")

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
    mark("environment")

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
    mark("physics authoring")
    # Let the mesh-collider bakes finish before serialization. Each runs on the
    # component's own tick, and its result is written INTO the prefab, so a
    # bake still in flight produces a collider with no geometry. See
    # `settle_frames` for why this is a constant and what it costs to get wrong.
    bake_count = report.counters.get("mesh_colliders", 0)
    settle = settle_frames(bake_count, skeletal_authored)
    report.count("settle_frames", settle)
    general.idle_wait_frames(settle)
    # Named for the collider bakes alone: it used to say "+ asset streaming"
    # too, and that half was measured to be false -- at settle=0 every material
    # asset id in the prefab matched the control exactly, and only cooked
    # collider data was lost.
    mark("settle: collider bakes")
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
    mark("save prefab")

    # --- did the collider bakes actually reach the file? ---
    #
    # `mesh_colliders` counts what was AUTHORED, and a collider whose bake had
    # not finished is written out fully configured with no geometry at all: it
    # collides with nothing, the save reports success, and the counter reads
    # the same either way. Measured on L_Showcase: settling zero frames lost 15
    # of 2501 bakes and every suite in this repo stayed green.
    #
    # This is a check rather than a wait because a wait is not available. Four
    # probes went looking for something to poll and found nothing (the bake is
    # in none of the collider's 17 reflected properties; a baked collider and
    # an unbaked one read identically through every Python-visible call). Nor
    # can it be repaired: the in-memory template is a snapshot that does not
    # track late bakes, and O3DE refuses a second CreatePrefabInMemory in the
    # same session. So the settle stays a constant, and this is what stops a
    # constant that is one day too small from failing in silence.
    unbaked = prefab_build.unbaked_colliders(prefab_path)
    report.count("colliders_cooked", bake_count - len(unbaked))
    for name in unbaked:
        report.warn("PHYS_COLLIDER_NOT_BAKED", name,
                    "settled %d frames before serializing; re-import with a "
                    "larger UEO3DE_SETTLE_FRAMES" % settle)
    if unbaked:
        emit("  %d of %d mesh collider bakes did NOT reach the prefab"
             % (len(unbaked), bake_count))
    mark("verify collider bakes")

    # --- record what this import AUTHORED, then put hand edits back (M10) ---
    #
    # The order matters and is not obvious. The ledger is written FIRST, from
    # the freshly rebuilt prefab -- that is, from the manifest's values, before
    # any hand edit is patched back over them. Writing it afterwards instead
    # made preservation survive exactly ONE re-import and then lose the edit in
    # silence:
    #
    #   run 2: conflict -> prefab patched to the user's value C
    #          ledger written from the patched file            -> records C
    #   run 3: file is C, ledger says C -> no conflict detected
    #          rebuild writes UE's value                       -> C is GONE
    #
    # The ledger's question is "what did WE author last time", so it must hold
    # what we authored. Then the conflict test -- does the file differ from
    # that? -- keeps answering yes for as long as the edit exists, and the edit
    # survives indefinitely and is reported on every run.
    ledger_path = reimport_module.write_ledger(
        prefab_path, reimport_module.build_ledger(document, prefab_path))
    emit("wrote import ledger " + os.path.basename(ledger_path))

    # The prefab has just been rebuilt from the manifest, so any entity the
    # user had moved is now back at UE's value. Patch those few entities in
    # the saved file and say which ones, loudly.
    if reimport_plan["conflicts"]:
        rebuilt = reimport_module.read_prefab(prefab_path)
        for conflict in reimport_plan["conflicts"]:
            # The REBUILT prefab is keyed by the NEW manifest names, so it must
            # be looked up by `new_name`. Using the ledger's old name made
            # `also_moved_in_ue` always False for a relabelled actor -- the
            # report then said "only you changed this" while UE's new
            # transform was being dropped. Same fix as preserve_conflicts;
            # it belongs in both places, and originally landed in only one.
            lookup = conflict.get("new_name") or conflict["name"]
            authored_now = rebuilt.get(lookup)
            also_moved_in_ue = (
                authored_now is not None
                and not reimport_module.transforms_equal(authored_now,
                                                         conflict["authored"]))
            if also_moved_in_ue:
                detail = ("edited in O3DE AND moved in UE since the last "
                          "import; the O3DE edit is kept, so this actor's new "
                          "UE transform was NOT applied")
            else:
                detail = ("edited in O3DE since the last import; the edit is "
                          "kept and the manifest's transform was not applied")
            # Report the name the entity has NOW, so the subject names
            # something the user can find in their level. The two warnings for
            # one entity used to disagree: this one used the old name while
            # REIMPORT_CONFLICT_NOT_PRESERVED used the new one.
            report.warn("REIMPORT_ENTITY_CONFLICT", lookup, detail)
        patched = reimport_module.preserve_conflicts(
            prefab_path, reimport_plan["conflicts"])
        report.count("reimport_preserved", len(patched))
        emit("preserved %d hand-edited transform(s)" % len(patched))
        # Reporting a conflict and then not preserving it is the worst of both
        # outcomes: the user is told their edit was kept, and it was not. That
        # can only happen if an entity could not be found in the rebuilt
        # prefab under the name we looked for, so name it rather than let the
        # counters quietly disagree.
        if len(patched) != len(reimport_plan["conflicts"]):
            lost = sorted({(c.get("new_name") or c.get("name"))
                           for c in reimport_plan["conflicts"]} - set(patched))
            for name in lost:
                report.warn("REIMPORT_CONFLICT_NOT_PRESERVED", name,
                            "reported as hand-edited, but no entity of that "
                            "name was found in the rebuilt prefab, so the edit "
                            "could NOT be restored and has been lost")

    mark("ledger + hand edits")

    emit("")
    _total = sum(report.timings.values())
    emit("where the time went (%.1f s total):" % _total)
    for name, seconds, percent in report.timing_rows():
        emit("  %-42s %8.1f s  %5.1f%%" % (name, seconds, percent))
    if report.subtimings:
        emit("  within a phase (already counted above):")
        for name, seconds in sorted(report.subtimings.items(), key=lambda kv: -kv[1]):
            emit("    %-40s %8.1f s  %5.1f%%"
                 % (name, seconds, (100.0 * seconds / _total) if _total else 0.0))

    return report, prefab_path

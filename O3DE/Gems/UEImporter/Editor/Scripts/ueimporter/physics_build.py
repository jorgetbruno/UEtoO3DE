"""
physics_build.py — manifest physics -> adapter calls (plan M3).

This module contains NO physics component names -- it speaks only the
`PhysicsBackendAdapter` vocabulary, which is what lets M3b add PhysX without
touching it. The seam guard test enforces that.

Body classification (the plan's M3 mapping table, backend-neutral):

    simulates_physics                  -> dynamic body
    movable + collision, no simulate   -> dynamic body, kinematic
    is_trigger                         -> static body + trigger colliders
    static + collision                 -> static body
    no collision                       -> no physics components at all

Collision shapes come from the mesh ASSET's simple collision (cached per GUID
in the manifest), scaled by the entity's world scale because the collider
components live outside the transform's scale:

  * `AZ::Transform` scale is uniform-only, and non-uniform scale sits on a
    separate component whose interaction with collider components is not a
    contract anywhere -- so the importer bakes the entity's scale into the
    collider dimensions itself. Deterministic, and identical across backends.
  * A sphere under non-uniform scale has no exact image; the largest axis
    wins and `PHYS_SHAPE_APPROXIMATED` is reported.
  * UE's zero-thickness planes produce boxes with a zero dimension; solvers
    reject or misbehave on degenerate shapes, so those are clamped to
    `MIN_DIMENSION` and reported.

Capability negotiation happens BEFORE authoring: required shape kinds are
compared against `adapter.capabilities()`, and anything unsupported is
substituted (convex for cylinder, and so on) with `PHYS_SHAPE_APPROXIMATED`
in the report -- per-backend geometry differences must be visible, never
silent (plan M3).

Mesh-shaped collision has two backend routes, and `author_entity_physics`
picks per entity, COOKED FIRST:

  * CAP_SHAPE_MESH_COOKED: the caller passes `cooked_mesh_ids`, a
    per-asset-guid map to the `.pxmesh` / `.joltmesh` product the Asset
    Processor cooked because staging wrote that backend's mesh group into the
    FBX's sidecar (`assetinfo.physics_for_asset`).
  * CAP_SHAPE_CONVEX / CAP_SHAPE_TRIMESH: the collider bakes from the entity's
    render mesh on its own tick; no asset involved.
  * neither -> AABB boxes over each element, reported.

A backend can advertise BOTH (Jolt does, once its mesh colliders moved to
assets and the bake moved to a second component), and then the cooked product
wins wherever one exists: nothing bakes on a tick, so there is no settle to
get wrong and no `PHYS_COLLIDER_NOT_BAKED` risk, and every instance references
one shared asset instead of carrying its own copy of the geometry -- the same
level measured 315.7 MB of baked Jolt prefab against 22.0 MB of PhysX asset
references. The bake remains the fallback for meshes that got no product.
"""

from .adapters import base

MIN_DIMENSION = 0.01  # meters; clamp for degenerate collider dimensions


def _scaled(vector, scale):
    return [vector[i] * scale[i] for i in range(3)]


def _clamped(values, subject, what, report):
    clamped = []
    hit = False
    for value in values:
        if abs(value) < MIN_DIMENSION:
            clamped.append(MIN_DIMENSION)
            hit = True
        else:
            clamped.append(value)
    if hit:
        report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                    "%s had a degenerate dimension; clamped to %g m"
                    % (what, MIN_DIMENSION))
    return clamped


def _uniformish(scale, subject, what, report):
    """Largest-axis approximation for shapes that cannot scale per-axis."""
    largest = max(abs(component) for component in scale)
    if max(scale) - min(scale) > 1e-6:
        report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                    "%s under non-uniform scale %r; largest axis %g used"
                    % (what, [round(c, 4) for c in scale], largest))
    return largest


def required_capabilities(document):
    """Shape kinds the manifest needs, for pre-authoring negotiation."""
    needed = set()
    assets = {asset["guid"]: asset for asset in document["assets"]}
    for entity in document["entities"]:
        physics = entity.get("physics")
        if physics is None:
            continue
        if physics["is_trigger"]:
            needed.add(base.CAP_TRIGGER)
        if physics["kinematic"]:
            needed.add(base.CAP_KINEMATIC)
        if physics["ccd"]:
            needed.add(base.CAP_CCD)
        shape_lists = [physics.get("shapes") or []]
        source = physics.get("shapes_from_asset")
        if source and source in assets:
            shape_lists.append(assets[source].get("collision", {}).get("shapes") or [])
            if assets[source].get("collision", {}).get("source") == "none":
                needed.add(base.CAP_SHAPE_TRIMESH)
        for shapes in shape_lists:
            for shape in shapes:
                needed.add({
                    "box": base.CAP_SHAPE_BOX,
                    "sphere": base.CAP_SHAPE_SPHERE,
                    "capsule": base.CAP_SHAPE_CAPSULE,
                    "convex": base.CAP_SHAPE_CONVEX,
                }.get(shape["type"], base.CAP_SHAPE_BOX))
    return needed


def _mesh_collider_supported(adapter, convex):
    """Can this backend build a collider from the entity's render mesh?

    Jolt can (its Mesh Collider bakes from the render mesh on activation);
    PhysX CANNOT -- it needs a cooked .pxmesh asset and has no render-mesh
    path (measured, M3b). Without this guard the render-mesh fallbacks below
    called `add_mesh_collider` unconditionally and the PhysX adapter's loud
    refusal propagated out of `import_level`, aborting the whole import with
    no prefab written -- on 4 entities of Fixture_01 and 14 of L_Showcase.
    `negotiate()` is advisory (it warns and its result is discarded), so the
    check has to happen HERE, at the call site.
    """
    from .adapters import base
    needed = base.CAP_SHAPE_CONVEX if convex else base.CAP_SHAPE_TRIMESH
    return needed in adapter.capabilities()


def _cooked_available(adapter, cooked):
    """Is there a cooked mesh AND a backend that can attach one?

    Both halves, at the call site. `importer` only builds `cooked_mesh_ids`
    for a backend advertising CAP_SHAPE_MESH_COOKED, so the capability half
    looks redundant -- but `_mesh_collider_supported` records exactly why it
    is not: `negotiate()` is advisory and a guard that lives in the caller is
    a guard that a second caller does not have. Handing an asset id to a
    backend that cannot use it raises out of the adapter and aborts the whole
    import.
    """
    return (cooked is not None
            and base.CAP_SHAPE_MESH_COOKED in adapter.capabilities())


def _cooked_usable(cooked, body, physics):
    """May THIS body carry this cooked mesh, given the backend's own limits?

    A cooked convex hull is usable everywhere. A cooked TRIANGLE MESH is not,
    and both restrictions are the backend's rather than ours:

      * a simulated dynamic actor rejects triangle-mesh geometry outright
        (PhysX logs an error and never attaches the shape);
      * a TRIGGER cannot be a triangle mesh either -- PhysX refuses to raise
        the trigger flag on trimesh geometry, so the entity keeps a collider
        that reports healthy and never fires an overlap.

    The trigger half is here because it was missing: the first version of this
    route gated on `body != "dynamic"` alone, which admits `static+trigger`,
    and a UE overlap volume over a complex-as-simple mesh would have been
    authored as a dead trigger. Worse than the box it replaced, and worse than
    the gap it replaced -- the previous code REPORTED that entity, and this
    silently claimed success. `Tests/perf/test_pxmesh.py` pins all four
    combinations.
    """
    if cooked.get("method") == "convex":
        return True
    return body != "dynamic" and not physics.get("is_trigger")


def _cooked_blocker(cooked, body, physics):
    """Why `_cooked_usable` said no, in words a report can print."""
    if cooked is None or cooked.get("method") == "convex":
        return None
    if physics.get("is_trigger"):
        return "a trigger (PhysX refuses the trigger flag on trimesh geometry)"
    if body == "dynamic":
        return "a simulated dynamic body"
    return None


def _report_mesh_gap(adapter, report, subject, convex, why, cooked=None,
                     blocker=None):
    """Say what was lost, in the vocabulary the report already uses."""
    if cooked is not None and cooked.get("method") == "trimesh" and blocker:
        # Be specific: a cooked mesh EXISTS and was deliberately not used.
        # "the backend cannot build a collider from a render mesh" would send
        # the reader looking for a missing asset that is sitting right there.
        report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                    "%s, and its cooked physics mesh is a TRIANGLE MESH, which "
                    "the %r backend cannot use on %s; the body is authored "
                    "WITHOUT a collider and will not collide. Give the mesh "
                    "simple collision in UE, or make it a convex cook."
                    % (why, adapter.name(), blocker))
        return
    report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                "%s, and the %r backend cannot build a %s collider from a "
                "render mesh; the body is authored WITHOUT a collider and "
                "will not collide"
                % (why, adapter.name(), "convex" if convex else "triangle-mesh"))


def negotiate(adapter, document, report):
    """Compare needs against capabilities; report the gaps once, up front."""
    needed = required_capabilities(document)
    capabilities = adapter.capabilities()
    missing = needed - capabilities
    for capability in sorted(missing):
        if capability in (base.CAP_SHAPE_CONVEX, base.CAP_SHAPE_TRIMESH) \
                and base.CAP_SHAPE_MESH_COOKED in capabilities:
            report.warn("PHYS_SHAPE_APPROXIMATED", adapter.name(),
                        "backend lacks %r; cooked physics mesh assets are "
                        "authored where the Asset Processor produced one, "
                        "AABB boxes elsewhere" % capability)
        else:
            report.warn("PHYS_SHAPE_APPROXIMATED", adapter.name(),
                        "backend lacks %r; affected shapes will be substituted"
                        % capability)
    return missing


def _author_shape(adapter, entity_id, shape, scale, subject, report, missing,
                  cooked=None):
    kind = shape["type"]
    offset = _scaled(shape.get("offset", [0.0, 0.0, 0.0]), scale) \
        if shape.get("offset") else None
    rotation = shape.get("rotation")

    if kind == "box":
        half = _clamped(_scaled(shape["half_extents"], scale), subject, "box collider", report)
        adapter.add_box_collider(entity_id, half, offset, rotation)
    elif kind == "sphere":
        radius = shape["radius"] * _uniformish(scale, subject, "sphere collider", report)
        adapter.add_sphere_collider(entity_id, max(radius, MIN_DIMENSION), offset)
    elif kind == "capsule":
        factor = _uniformish(scale, subject, "capsule collider", report)
        adapter.add_capsule_collider(
            entity_id,
            max(shape["radius"] * factor, MIN_DIMENSION),
            max(shape["total_height"] * factor, 2.5 * MIN_DIMENSION),
            offset, rotation)
    elif kind == "convex":
        # The manifest carries the convex element's AABB, not its vertices; the
        # faithful route is the backend's convex hull of the render mesh --
        # baked live (Jolt) or cooked into a .pxmesh product at asset-process
        # time (PhysX + a sidecar mesh group). If neither exists, a box over
        # the AABB substitutes.
        if (_cooked_available(adapter, cooked)
                and cooked.get("method") == "convex"):
            # COOKED FIRST, on either backend. A gem that can do both (Jolt,
            # since its mesh colliders moved to assets) should use the product
            # the Asset Processor already built: no tick-time bake to settle
            # for, and one shared asset instead of a copy of the geometry
            # serialized into every instance.
            adapter.add_mesh_collider(entity_id, convex=True,
                                      asset_id=cooked["asset_id"])
            report.count("mesh_asset_colliders")
        elif base.CAP_SHAPE_CONVEX in adapter.capabilities():
            adapter.add_mesh_collider(entity_id, convex=True)
            report.count("mesh_colliders")
        else:
            aabb_min = _scaled(shape["aabb_min"], scale)
            aabb_max = _scaled(shape["aabb_max"], scale)
            half = _clamped([(aabb_max[i] - aabb_min[i]) * 0.5 for i in range(3)],
                            subject, "convex collider", report)
            center = [(aabb_max[i] + aabb_min[i]) * 0.5 for i in range(3)]
            report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                        "convex hull unsupported; AABB box substituted")
            adapter.add_box_collider(entity_id, half, center, None)
    else:
        report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                    "shape %r has no authoring path; skipped" % kind)


def _collapse_convex(shapes, subject, report, adapter, cooked=False):
    """N convex elements -> one, but ONLY where they are genuinely identical.

    `_author_shape` has two kinds of answer for a `convex` element and they
    differ in exactly the way that decides this:

      * a WHOLE-MESH answer: a backend with CAP_SHAPE_CONVEX (Jolt) gets
        `add_mesh_collider(convex=True)`, which hulls the entity's WHOLE RENDER
        MESH and ignores the element entirely -- its offset, its rotation,
        which part of the model it covers. A cooked `.pxmesh` (PhysX, when
        `cooked` says one exists for THIS asset) is the same answer arrived at
        earlier: the Asset Processor cooked the whole render mesh. Either way
        N elements produce N byte-identical colliders, and the union of N
        identical hulls is one hull.
      * a PER-ELEMENT answer: a backend with neither gets a box over THAT
        ELEMENT'S OWN AABB, at that element's own centre. N elements produce
        N DIFFERENT boxes that together trace the shape of the decomposition.

    So the collapse is free on the first and destructive on the second. An
    earlier version of this function did not check, and it was wrong: a
    scaffold tower's 340 convex pieces would have become a single small box
    covering only the first one, on a backend where each of the 340 was
    carrying distinct geometry. Jolt's suites stayed green because the fixture
    has no multi-convex asset, and the level that exposed it was imported to
    Jolt. Hence the `adapter` argument -- the answer depends on the backend and
    cannot be decided from the shapes alone. `cooked` is per-ASSET, not
    per-backend: on PhysX the same import collapses entities whose mesh got a
    cooked product and boxes per element for entities whose mesh did not.

    Measured on a 4.27-era siege map: one `Scaf_Tower` carries **340** convex
    elements, five towers like it, 12,147 mesh colliders across the level where
    3,425 do exactly the same job. The import peaked at 24 GB for 3,677
    entities -- five times what a 3,709-entity slice of a modern city needed --
    and died inside `idle_wait_frames`.

    The approximation this reports was ALREADY happening; it just happened
    silently. UE decomposes concave collision into convex pieces, and replacing
    that decomposition with a single whole-mesh hull fills every concavity
    between them. A tower you could walk inside becomes solid. That is worth a
    warning whether or not it is worth 340 copies.
    """
    if base.CAP_SHAPE_CONVEX not in adapter.capabilities() and not cooked:
        # Every element becomes its own AABB box; they are not interchangeable.
        return shapes
    convex = [shape for shape in shapes if shape.get("type") == "convex"]
    if len(convex) <= 1:
        return shapes
    kept = []
    seen = False
    for shape in shapes:
        if shape.get("type") == "convex":
            if seen:
                continue
            seen = True
        kept.append(shape)
    report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                "UE decomposes this collision into %d convex pieces; this "
                "backend's convex collider covers the whole render mesh, so "
                "all %d would be identical and one is authored. Concavities "
                "between the pieces are filled in." % (len(convex), len(convex)))
    return kept


def author_entity_physics(adapter, entity_id, item, assets_by_guid, report,
                          profile_map, cooked_mesh_ids=None):
    """Author one manifest entity's physics through the adapter.

    `cooked_mesh_ids` maps a static-mesh asset guid to
    `{"asset_id": <catalog id of its .pxmesh product>, "method":
    "convex"|"trimesh"}` for CAP_SHAPE_MESH_COOKED backends; empty/None on
    backends that bake from the render mesh.

    Returns a summary string for the log, or None when the entity carries no
    physics.
    """
    physics = item.get("physics")
    if physics is None or not physics.get("has_collision"):
        return None
    cooked_mesh_ids = cooked_mesh_ids or {}

    subject = item["name"]
    scale = item["transform"]["world"]["scale"]

    profile = physics.get("collision_profile") or ""
    if profile and profile not in profile_map:
        report.warn("PHYS_PROFILE_FALLBACK", subject,
                    "UE collision profile %r has no mapping; fallback layer used"
                    % profile)

    # --- body ---
    if physics["is_trigger"]:
        adapter.add_static_body(entity_id)
        body = "static+trigger"
    elif physics["simulates_physics"] or physics["kinematic"]:
        mass = physics.get("mass_kg") if physics.get("mass_override") else None
        if mass is None and physics["simulates_physics"]:
            report.warn("MASS_FROM_DENSITY", subject,
                        "no UE mass override; backend derives mass from shape "
                        "volume and default density")
        adapter.add_dynamic_body(
            entity_id,
            mass=mass,
            linear_damping=physics.get("linear_damping"),
            angular_damping=physics.get("angular_damping"),
            gravity_enabled=physics.get("enable_gravity", True),
            ccd=physics.get("ccd", False),
            kinematic=physics["kinematic"])
        body = "kinematic" if physics["kinematic"] else "dynamic"
    else:
        adapter.add_static_body(entity_id)
        body = "static"

    # --- shapes ---
    authored = 0
    for shape in _collapse_convex(physics.get("shapes") or [], subject, report,
                                  adapter):
        _author_shape(adapter, entity_id, shape, scale, subject, report, None)
        authored += 1

    source_guid = physics.get("shapes_from_asset")
    if source_guid:
        asset = assets_by_guid.get(source_guid)
        collision = (asset or {}).get("collision") or {}
        cooked = cooked_mesh_ids.get(source_guid)
        if collision.get("source") == "simple":
            for shape in _collapse_convex(
                    collision.get("shapes") or [], subject, report, adapter,
                    cooked=bool(cooked and cooked.get("method") == "convex")):
                _author_shape(adapter, entity_id, shape, scale, subject, report,
                              None, cooked=cooked)
                authored += 1
        elif asset is not None:
            # No simple collision: fall back to the render mesh (plan M3:
            # complex-as-simple -> triangle mesh, static bodies only; a
            # dynamic body gets a convex hull instead).
            convex = body in ("dynamic", "kinematic")
            if (_cooked_available(adapter, cooked)
                    and _cooked_usable(cooked, body, physics)):
                # COOKED FIRST. A cooked triangle mesh is legal on static and
                # kinematic bodies but NOT on a simulated dynamic one, and NOT
                # as a trigger; a cooked convex hull is fine everywhere. See
                # `_cooked_usable` -- the restrictions are the backend's, and
                # authoring through them produces a collider that looks
                # healthy and does nothing.
                convex_cook = cooked.get("method") == "convex"
                adapter.add_mesh_collider(entity_id, convex=convex_cook,
                                          asset_id=cooked["asset_id"])
                report.count("mesh_asset_colliders")
                report.warn("PHYS_MESH_FROM_RENDER", subject,
                            "no simple collision on %s; %s collider from the "
                            "cooked physics mesh asset"
                            % (asset["ue_path"],
                               "convex" if convex_cook else "triangle-mesh"))
                authored += 1
            elif _mesh_collider_supported(adapter, convex):
                adapter.add_mesh_collider(entity_id, convex=convex)
                report.count("mesh_colliders")
                report.warn("PHYS_MESH_FROM_RENDER", subject,
                            "no simple collision on %s; %s collider baked from "
                            "the render mesh" % (asset["ue_path"],
                                                 "convex" if convex else "triangle-mesh"))
                authored += 1
            else:
                _report_mesh_gap(adapter, report, subject, convex,
                                 "no simple collision on %s" % asset["ue_path"],
                                 cooked=cooked,
                                 blocker=_cooked_blocker(cooked, body, physics))

    if authored == 0:
        # A body with no shape is invisible to the solver -- give it the
        # entity's render mesh if there is one, otherwise report and bail.
        convex = body in ("dynamic", "kinematic")
        mesh_info = item.get("mesh")
        cooked = cooked_mesh_ids.get((mesh_info or {}).get("asset_guid"))
        if (mesh_info and _cooked_available(adapter, cooked)
                and _cooked_usable(cooked, body, physics)):
            # COOKED FIRST, as above.
            adapter.add_mesh_collider(
                entity_id, convex=cooked.get("method") == "convex",
                asset_id=cooked["asset_id"])
            report.count("mesh_asset_colliders")
            report.warn("PHYS_MESH_FROM_RENDER", subject,
                        "no collision shapes anywhere; cooked physics mesh of "
                        "the render mesh used")
            authored += 1
        elif mesh_info and _mesh_collider_supported(adapter, convex):
            adapter.add_mesh_collider(entity_id, convex=convex)
            report.count("mesh_colliders")
            report.warn("PHYS_MESH_FROM_RENDER", subject,
                        "no collision shapes anywhere; render mesh used")
            authored += 1
        elif mesh_info:
            _report_mesh_gap(adapter, report, subject, convex,
                             "no collision shapes anywhere", cooked=cooked,
                             blocker=_cooked_blocker(cooked, body, physics))
        else:
            report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                        "collidable entity has no shapes and no mesh; body "
                        "authored without a collider")

    if physics["is_trigger"]:
        adapter.make_trigger(entity_id)

    return "%s, %d shape(s)" % (body, authored)

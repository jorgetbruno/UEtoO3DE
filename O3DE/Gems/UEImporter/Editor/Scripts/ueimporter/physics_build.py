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
in the manifest). WHO applies the entity's scale to them is a per-backend
question with a measured answer, not an assumption:

  * A backend advertising `CAP_SCALE_ENGINE_APPLIED` scales its own colliders
    -- dimensions and offsets, primitives and cooked mesh assets -- from the
    entity's world uniform scale times any non-uniform scale component. The
    importer then authors UNSCALED numbers, because multiplying them by the
    same scale again would square the collision (a 2x actor colliding at 4x).
    Both shipped backends do this today; `probe_scale_matrix.py` measures it
    by reading each collider's world AABB and reporting the scaled/unscaled
    ratio, and every cell reads 2.000.
  * A backend that does NOT advertise it gets the scale baked in here instead,
    which is what this module did unconditionally until the Jolt gem started
    honouring entity scale. `UEO3DE_BAKE_SCALE=1` forces that older behaviour
    back on for a gem build that predates it -- nothing in the component set
    distinguishes the two, so it cannot be detected.
  * When the scale IS baked here: a sphere under non-uniform scale has no
    exact image, so the largest axis wins and `PHYS_SHAPE_APPROXIMATED` is
    reported. Left to the engine, both backends handle it natively.
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

import os

from .adapters import base

MIN_DIMENSION = 0.01  # meters; clamp for degenerate collider dimensions
IDENTITY_SCALE = [1.0, 1.0, 1.0]

_BAKE_SCALE_ON = ("1", "on", "true", "yes", "enabled")
_BAKE_SCALE_OFF = ("0", "off", "false", "no", "none", "disabled")


def bake_scale_override(value=None):
    """UEO3DE_BAKE_SCALE -> True (force baking), False (forbid), or None.

    Unrecognised values RAISE rather than defaulting: `UEO3DE_PHYSX_DECOMPOSE`
    once mapped every unparseable string onto "on", so "off" turned the feature
    ON. A knob that silently means its opposite is worse than no knob.
    """
    if value is None:
        value = os.environ.get("UEO3DE_BAKE_SCALE", "")
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _BAKE_SCALE_ON:
        return True
    if text in _BAKE_SCALE_OFF:
        return False
    raise ValueError(
        "UEO3DE_BAKE_SCALE=%r is not one of %s"
        % (value, ", ".join(_BAKE_SCALE_ON + _BAKE_SCALE_OFF)))


def collider_scale(adapter, world_scale, override=None):
    """The scale to MULTIPLY authored collider dimensions by.

    Identity when the backend scales its own colliders, because then the
    entity's scale is applied twice otherwise -- see CAP_SCALE_ENGINE_APPLIED.
    """
    if override is None:
        override = bake_scale_override()
    if override is None:
        override = base.CAP_SCALE_ENGINE_APPLIED not in adapter.capabilities()
    return list(world_scale) if override else list(IDENTITY_SCALE)


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


# A whole-mesh convex hull replaces UE's decomposition with one solid lump.
# Below this many elements the import authors a box PER ELEMENT instead, which
# keeps the decomposition's spatial structure. The cap exists because the
# collapse it overrides was put there for a real reason: one `Scaf_Tower`
# carries 340 elements and five of them helped push an import to 24 GB. Boxes
# are far cheaper than hulls, but "far cheaper" times 340 is still worth
# refusing, and past a certain count the shape is better served by V-HACD
# (UEO3DE_DECOMPOSE) than by hundreds of AABBs.
CONVEX_PER_ELEMENT_MAX = 16
# How much thicker UE's collision may be than the render mesh before a
# whole-mesh hull is the wrong answer. A hull CANNOT be thicker than the mesh
# it hulls, so when UE's own collision is substantially bigger on an axis, the
# hull silently loses that volume.
#
# NOT a flatness test, and that is a correction. The obvious rule -- "the
# render mesh has a zero extent" -- cannot be evaluated from the manifest:
# measured on SiegeOfPonthus, `SM_Floor`'s `bounds_local` reports a Z extent of
# 0.15 m while its exported geometry is genuinely 0.0 m thick (1089 vertices,
# all at the same Y in the glTF basis). `bounds_local` is UE's asset bounding
# box and does not describe what the exporter wrote. The RATIO survives that
# disagreement: 0.51 m of collision against 0.15 m of mesh is 3.4x either way.
CONVEX_THICKNESS_RATIO = 2.0
MIN_MEASURABLE_EXTENT = 1e-4


def _convex_as_box(shape):
    """A convex element's own AABB, as a box shape dict.

    UE's convex VERTICES are not reachable from Python (DIVERGENCES.md), but
    every element carries its AABB, and a box over that AABB is a real,
    localised volume. Returned in the same shape-dict form the box path
    already understands, so nothing downstream needs to know.
    """
    low = shape["aabb_min"]
    high = shape["aabb_max"]
    return {
        "type": "box",
        "half_extents": [(high[i] - low[i]) * 0.5 for i in range(3)],
        "offset": [(high[i] + low[i]) * 0.5 for i in range(3)],
        "rotation": shape.get("rotation"),
    }


def _hull_would_under_cover(shape, mesh_bounds):
    """Is UE's collision substantially thicker than the mesh a hull would use?

    The whole-mesh convex answer hulls the RENDER mesh, and a hull cannot
    exceed the mesh it hulls. Measured on SiegeOfPonthus: `SM_Floor`'s UE
    convex is 5.0 x 5.0 x 0.51 m while the exported mesh is 5.0 x 5.0 x 0.0 m
    (and `bounds_local` claims 0.15 -- see CONVEX_THICKNESS_RATIO). 179
    entities placed it, and every one collided as an infinitely thin sheet a
    fast body can pass straight through.
    """
    if not mesh_bounds:
        return False
    low, high = mesh_bounds.get("min"), mesh_bounds.get("max")
    if not low or not high or len(low) != 3 or len(high) != 3:
        return False
    element_low, element_high = shape.get("aabb_min"), shape.get("aabb_max")
    if not element_low or not element_high:
        return False
    for axis in range(3):
        mesh_extent = abs(high[axis] - low[axis])
        element_extent = abs(element_high[axis] - element_low[axis])
        if element_extent < MIN_MEASURABLE_EXTENT:
            continue
        if element_extent > CONVEX_THICKNESS_RATIO * max(mesh_extent,
                                                         MIN_MEASURABLE_EXTENT):
            return True
    return False


def convex_per_element(environ=None):
    """UEO3DE_CONVEX_PER_ELEMENT -> author a box per convex piece.

    Off by default; see `_collapse_convex` for why the default stands.
    Unrecognised values RAISE, like every other switch in this module.
    """
    source = os.environ if environ is None else environ
    text = str(source.get("UEO3DE_CONVEX_PER_ELEMENT", "")).strip().lower()
    if not text:
        return False
    if text in _BAKE_SCALE_ON:
        return True
    if text in _BAKE_SCALE_OFF:
        return False
    raise ValueError(
        "UEO3DE_CONVEX_PER_ELEMENT=%r is not one of %s"
        % (text, ", ".join(_BAKE_SCALE_ON + _BAKE_SCALE_OFF)))


def _collapse_convex(shapes, subject, report, adapter, cooked=False,
                     mesh_bounds=None, per_element=None):
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
    if per_element is None:
        per_element = convex_per_element()
    if base.CAP_SHAPE_CONVEX not in adapter.capabilities() and not cooked:
        # Every element becomes its own AABB box; they are not interchangeable.
        return shapes
    convex = [shape for shape in shapes if shape.get("type") == "convex"]

    # A whole-mesh hull is only the better answer when the render mesh
    # actually contains the element. Where it is FLAT on an axis the element
    # has, the hull is a sheet and the element's own AABB is strictly more
    # faithful -- so take the box and say so.
    if len(convex) == 1 and _hull_would_under_cover(convex[0], mesh_bounds):
        report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                    "UE's convex collision is much thicker than the render mesh on "
                    "at least one axis, and a hull cannot exceed the mesh it "
                    "hulls -- it would lose that volume. A box over the "
                    "element's own AABB is authored instead")
        return [_convex_as_box(shape) if shape.get("type") == "convex" else shape
                for shape in shapes]

    if len(convex) <= 1:
        return shapes

    # UE decomposed this, and one whole-mesh hull fills every concavity
    # between the pieces. One box per piece keeps the decomposition's shape --
    # but it is OPT-IN, not the default, and that is deliberate.
    #
    # The collapse it would override is a MEASURED decision, not an oversight:
    # a cooked convex product is one shared asset per mesh, where per-instance
    # geometry cost the same level 315.7 MB against 22.0 MB. Replacing a
    # cooked hull with N AABBs also trades tighter geometry for looser on
    # every asset, and "looser everywhere" is not obviously better than
    # "concavities filled" -- it depends on the shape, and I have not measured
    # which wins. So the choice is offered and named, and the default stands.
    if per_element and len(convex) <= CONVEX_PER_ELEMENT_MAX:
        report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                    "UE decomposes this collision into %d convex pieces; "
                    "UEO3DE_CONVEX_PER_ELEMENT is on, so one BOX PER PIECE is "
                    "authored from each piece's own AABB. Boxes are looser "
                    "than UE's hulls (whose vertices are not reachable from "
                    "Python) but the concavities between pieces survive"
                    % len(convex))
        return [_convex_as_box(shape) if shape.get("type") == "convex" else shape
                for shape in shapes]

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
    # NOT the entity's world scale unless this backend leaves collider scaling
    # to the importer. Both shipped backends apply it themselves.
    scale = collider_scale(adapter, item["transform"]["world"]["scale"])

    # Reported against the PROFILE, not the entity, for two reasons. It is a
    # fact about the profile -- naming an entity implies this one is special
    # when every body with that profile is affected -- and `Report.warn`
    # dedupes on (code, subject, detail), so the profile as subject collapses
    # one record per body into one record per profile: 3,434 records became 3
    # on a converted siege map.
    #
    # It fires for MAPPED profiles too, which the previous version did not,
    # because the mapping currently changes nothing: `profile_map` is read here
    # and nowhere else, no `layer` argument is passed to any adapter call
    # below, and no adapter implements the `layer` parameter it accepts. Saying
    # "the fallback layer was used" was worse than saying nothing -- it implied
    # the mapped profiles had got a layer.
    profile = physics.get("collision_profile") or ""
    if profile:
        report.warn("PHYS_PROFILE_FALLBACK", profile,
                    "collision filtering is not applied on either backend: no "
                    "layer reaches the collider, so every body collides with "
                    "everything. UE's per-channel Block/Overlap/Ignore "
                    "responses are lost%s"
                    % ("" if profile in profile_map
                       else " (and this profile has no entry in "
                            "collision_profiles.json either)"))

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
                    cooked=bool(cooked and cooked.get("method") == "convex"),
                    mesh_bounds=(asset or {}).get("bounds_local")):
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

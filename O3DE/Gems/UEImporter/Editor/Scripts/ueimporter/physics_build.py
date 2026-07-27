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


def negotiate(adapter, document, report):
    """Compare needs against capabilities; report the gaps once, up front."""
    needed = required_capabilities(document)
    missing = needed - adapter.capabilities()
    for capability in sorted(missing):
        report.warn("PHYS_SHAPE_APPROXIMATED", adapter.name(),
                    "backend lacks %r; affected shapes will be substituted"
                    % capability)
    return missing


def _author_shape(adapter, entity_id, shape, scale, subject, report, missing):
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
        # faithful route is the backend's convex hull of the render mesh. If
        # the backend cannot, a box over the AABB substitutes.
        if base.CAP_SHAPE_CONVEX in adapter.capabilities():
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


def author_entity_physics(adapter, entity_id, item, assets_by_guid, report,
                          profile_map):
    """Author one manifest entity's physics through the adapter.

    Returns a summary string for the log, or None when the entity carries no
    physics.
    """
    physics = item.get("physics")
    if physics is None or not physics.get("has_collision"):
        return None

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
    for shape in physics.get("shapes") or []:
        _author_shape(adapter, entity_id, shape, scale, subject, report, None)
        authored += 1

    source_guid = physics.get("shapes_from_asset")
    if source_guid:
        asset = assets_by_guid.get(source_guid)
        collision = (asset or {}).get("collision") or {}
        if collision.get("source") == "simple":
            for shape in collision.get("shapes") or []:
                _author_shape(adapter, entity_id, shape, scale, subject, report, None)
                authored += 1
        elif asset is not None:
            # No simple collision: fall back to the render mesh (plan M3:
            # complex-as-simple -> triangle mesh, static bodies only; a
            # dynamic body gets a convex hull instead).
            convex = body in ("dynamic", "kinematic")
            adapter.add_mesh_collider(entity_id, convex=convex)
            report.count("mesh_colliders")
            report.warn("PHYS_MESH_FROM_RENDER", subject,
                        "no simple collision on %s; %s collider baked from the "
                        "render mesh" % (asset["ue_path"],
                                         "convex" if convex else "triangle-mesh"))
            authored += 1

    if authored == 0:
        # A body with no shape is invisible to the solver -- give it the
        # entity's render mesh if there is one, otherwise report and bail.
        if item.get("mesh"):
            convex = body in ("dynamic", "kinematic")
            adapter.add_mesh_collider(entity_id, convex=convex)
            report.count("mesh_colliders")
            report.warn("PHYS_MESH_FROM_RENDER", subject,
                        "no collision shapes anywhere; render mesh used")
            authored += 1
        else:
            report.warn("PHYS_SHAPE_APPROXIMATED", subject,
                        "collidable entity has no shapes and no mesh; body "
                        "authored without a collider")

    if physics["is_trigger"]:
        adapter.make_trigger(entity_id)

    return "%s, %d shape(s)" % (body, authored)

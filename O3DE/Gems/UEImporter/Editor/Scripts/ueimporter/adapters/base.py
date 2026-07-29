"""
base.py — the `PhysicsBackendAdapter` interface (plan M3, global constraint 5).

The importer authors ALL physics through this interface and never names a
physics component: no string outside `adapters/` may contain a component name,
and CI greps for exactly that (`Tests/m3/test_seam_guard.py`). The seam exists
now, while there is one implementation, because retrofitting it after M3-M7
have grown inline component names is roughly a week of work (plan v2.1 note).

The interface is shaped around **shapes**, not around "a collider component
with a shape enum": Jolt has one component per shape while PhysX has one
collider component with a shape property, and modelling it the PhysX way makes
the Jolt adapter awkward and leaks the enum upward (plan M3).

Contracts every implementation must honour:

  * `resolve_components()` resolves every component name the adapter needs to
    type IDs at import start and RAISES on any miss. A silent no-op produces a
    prefab with no physics, which no other assertion catches early and which
    is indistinguishable from a physics bug (plan, Known Hard Spot 4).
  * `capabilities()` names what the backend can author. The caller compares
    the manifest's needs against it BEFORE authoring and reports any
    substitution as `PHYS_SHAPE_APPROXIMATED` -- the same UE level legitimately
    produces slightly different geometry per backend, and that must be visible
    in the report rather than silent.
  * `contact_offset()` is the backend's resting-position tolerance, READ from
    the backend, never hard-coded: acceptance tests derive their tolerances
    from it so a second backend does not require rewriting them (plan M3).
  * Editor components only: the importer runs in the Editor, and both gems
    spawn their runtime components via BuildGameEntity.
"""


class AdapterError(Exception):
    """A backend adapter could not honour a request. Always loud, never a no-op."""


# Capability names shared across adapters. An adapter advertises a subset.
CAP_SHAPE_BOX = "shape.box"
CAP_SHAPE_SPHERE = "shape.sphere"
CAP_SHAPE_CAPSULE = "shape.capsule"
CAP_SHAPE_CYLINDER = "shape.cylinder"
CAP_SHAPE_CONVEX = "shape.convex"
CAP_SHAPE_TRIMESH = "shape.trimesh"
CAP_COMPOUND_STATIC = "compound.static"
CAP_TRIGGER = "trigger"
CAP_KINEMATIC = "kinematic"
CAP_CCD = "ccd"
# The backend can author a mesh collider FROM A COOKED PHYSICS MESH ASSET
# passed as `asset_id` (a `.pxmesh` the Asset Processor produced because the
# source FBX's sidecar carries a PhysX mesh group). Distinct from
# CAP_SHAPE_CONVEX / CAP_SHAPE_TRIMESH, which promise a collider from the
# entity's RENDER mesh with no asset at all -- PhysX has the first and not the
# second, Jolt the second and not the first, and conflating them is how a
# backend ends up authoring a fully-configured collider with no geometry.
CAP_SHAPE_MESH_COOKED = "shape.mesh_cooked"


class PhysicsBackendAdapter:
    """Intent-level physics authoring. One subclass per backend."""

    def name(self):
        """Stable backend id: 'jolt' or 'physx'."""
        raise NotImplementedError

    def resolve_components(self):
        """Resolve every needed component name -> type ID. Raise AdapterError
        on any miss. Must be called (and succeed) before any authoring call."""
        raise NotImplementedError

    def capabilities(self):
        """Set of CAP_* strings this backend can author."""
        raise NotImplementedError

    def contact_offset(self):
        """Backend contact offset in meters; tests derive tolerances from it."""
        raise NotImplementedError

    # --- bodies ---

    def add_static_body(self, entity_id, layer=None):
        raise NotImplementedError

    def add_dynamic_body(self, entity_id, mass=None, linear_damping=None,
                         angular_damping=None, gravity_enabled=True,
                         ccd=False, kinematic=False):
        """`mass=None` means 'backend decides' (density-derived); the caller
        reports MASS_FROM_DENSITY when it passes None for a UE body without an
        explicit mass override."""
        raise NotImplementedError

    # --- colliders (offsets/rotations in entity-local space, meters, xyzw) ---

    def add_box_collider(self, entity_id, half_extents, local_offset=None,
                         local_rotation=None, material=None, layer=None):
        raise NotImplementedError

    def add_sphere_collider(self, entity_id, radius, local_offset=None,
                            material=None, layer=None):
        raise NotImplementedError

    def add_capsule_collider(self, entity_id, radius, height, local_offset=None,
                             local_rotation=None, material=None, layer=None):
        """`height` is the TOTAL height including caps."""
        raise NotImplementedError

    def add_cylinder_collider(self, entity_id, radius, height, local_offset=None,
                              local_rotation=None, material=None, layer=None):
        raise NotImplementedError

    def add_mesh_collider(self, entity_id, convex, material=None, layer=None,
                          asset_id=None):
        """Collision from a mesh. Two routes, per the two capabilities:

        CAP_SHAPE_CONVEX / CAP_SHAPE_TRIMESH backends (Jolt) build from the
        entity's own render mesh (the Mesh component must already carry a
        loaded model) and IGNORE `asset_id`. `convex=True` -> convex hull
        (valid on dynamic bodies); `convex=False` -> triangle mesh (static and
        kinematic bodies only).

        CAP_SHAPE_MESH_COOKED backends (PhysX) REQUIRE `asset_id`: the cooked
        physics mesh product's asset id, resolved through the catalog by the
        caller. The cooked asset itself fixes the geometry type, so `convex`
        is advisory there; the static-only restriction on triangle meshes
        still applies at runtime."""
        raise NotImplementedError

    # --- modifiers ---

    def make_trigger(self, entity_id):
        """Mark every collider on the entity as a trigger (sensor)."""
        raise NotImplementedError

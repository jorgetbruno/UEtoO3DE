"""
jolt.py — the JoltPhysics implementation of `PhysicsBackendAdapter` (plan M3).

Every component name and property path below was resolved and dumped live
against the JoltPhysics gem in O3DE 26.05 before this file was written
(`Tests/o3de/results/probe_m3_jolt_result.txt`). The names are `Jolt`-prefixed
by the gem on purpose, so a PhysX-authored level cannot silently switch
backend -- respect that and never mix prefixes within one import (constraint 5).

Findings this implementation depends on:

  * Colliders share a base: `Collider Configuration|{Trigger, Offset,
    Rotation, Contact offset, Rest offset, Collision Layer, ...}` plus a
    per-shape `Shape Configuration|{Dimensions | Radius | Height}`.
    Box `Dimensions` are FULL extents.
  * `Jolt Rigid Body` carries `Configuration|{Kinematic, Mass, Linear damping,
    Angular damping, Gravity enabled, CCD enabled}`. Mass is a plain float --
    there is no density property, so "no mass override" maps to leaving the
    gem's own default (the caller reports MASS_FROM_DENSITY).
  * The Mesh Collider bakes collision from the entity's own render mesh
    (`VisibleGeometryRequestBus`, implemented by the Mesh component),
    automatically on activation once the model asset loads -- the component
    keeps a TickBus retry running until then (gem source,
    EditorJoltMeshColliderComponent.h). So the sequencing contract is:
    `wait_for_asset` first, add the collider after the model is set, pump
    frames. Shape type is the `MeshType` field (TriangleMesh | Convex).
  * Contact offset: read from a live collider's default, never hard-coded.
    The current gem build rests bodies at the analytic height (measured:
    a 1 m cube on a 1 m-thick floor rests at z = 1.0000 exactly), while
    older builds rested `contact offset` low -- exactly why tests take the
    tolerance from here instead of assuming 0.02.
"""

from . import base
from .base import AdapterError


_BODY_DYNAMIC = "Jolt Rigid Body"
_BODY_STATIC = "Jolt Static Rigid Body"
_COLLIDER_BOX = "Jolt Box Collider"
_COLLIDER_SPHERE = "Jolt Sphere Collider"
_COLLIDER_CAPSULE = "Jolt Capsule Collider"
_COLLIDER_CYLINDER = "Jolt Cylinder Collider"
_COLLIDER_MESH = "Jolt Mesh Collider"
_COLLIDER_COMPOUND = "Jolt Static Compound Collider"

_ALL_COMPONENTS = (
    _BODY_DYNAMIC, _BODY_STATIC,
    _COLLIDER_BOX, _COLLIDER_SPHERE, _COLLIDER_CAPSULE,
    _COLLIDER_CYLINDER, _COLLIDER_MESH, _COLLIDER_COMPOUND,
)

# Verified property paths (probe_m3_jolt). A miss at set time raises.
_P_TRIGGER = "Collider Configuration|Trigger"
_P_OFFSET = "Collider Configuration|Offset"
_P_ROTATION = "Collider Configuration|Rotation"
_P_CONTACT_OFFSET = "Collider Configuration|Contact offset"
_P_BOX_DIMENSIONS = "Shape Configuration|Dimensions"
_P_RADIUS = "Shape Configuration|Radius"
_P_HEIGHT = "Shape Configuration|Height"
_P_KINEMATIC = "Configuration|Kinematic"
_P_MASS = "Configuration|Mass"
_P_LINEAR_DAMPING = "Configuration|Linear damping"
_P_ANGULAR_DAMPING = "Configuration|Angular damping"
_P_GRAVITY = "Configuration|Gravity enabled"
_P_CCD = "Configuration|CCD enabled"

# The MeshType combo was seen in the gem's edit reflection; its property-path
# label is verified at set time against these candidates (resolve-or-fail).
_P_MESH_TYPE_CANDIDATES = ("Shape Configuration|Mesh Type", "Mesh Type",
                           "Configuration|Mesh Type")
# Physics::CookedMeshShapeConfiguration::MeshType enum values.
_MESH_TYPE_TRIANGLE = 0
_MESH_TYPE_CONVEX = 1


class JoltBackendAdapter(base.PhysicsBackendAdapter):

    def __init__(self):
        self._type_ids = {}
        self._contact_offset = None
        # Collider component pairs added per entity, so make_trigger can reach
        # them without re-querying by name.
        self._colliders_by_entity = {}

    # -- infrastructure ----------------------------------------------------

    def name(self):
        return "jolt"

    def _bus(self):
        import azlmbr.bus as bus
        import azlmbr.editor as editor
        return bus, editor

    def resolve_components(self):
        import azlmbr.bus as bus
        import azlmbr.editor as editor
        from azlmbr.entity import EntityType

        instance = EntityType()
        game_type = instance.Game() if callable(instance.Game) else instance.Game
        names = list(_ALL_COMPONENTS)
        type_ids = editor.EditorComponentAPIBus(
            bus.Broadcast, 'FindComponentTypeIdsByEntityType', names, game_type)
        if not type_ids or len(type_ids) != len(names):
            raise AdapterError("Jolt component lookup returned %r for %r"
                               % (type_ids, names))
        misses = [name for name, type_id in zip(names, type_ids)
                  if type_id is None or type_id.IsNull()]
        if misses:
            raise AdapterError(
                "Jolt components did not resolve: %r. A silent miss here would "
                "produce a prefab with no physics, so this is fatal." % misses)
        self._type_ids = dict(zip(names, type_ids))

        # Read the backend's contact offset from a real collider's default.
        self._contact_offset = self._read_contact_offset()

    def _read_contact_offset(self):
        import azlmbr.bus as bus
        import azlmbr.editor as editor
        import azlmbr.entity as entity_module

        scratch = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', scratch, '__jolt_offset_probe')
        pair = self._add_component(scratch, _COLLIDER_BOX)
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, _P_CONTACT_OFFSET)
        value = outcome.GetValue() if outcome and outcome.IsSuccess() else None
        editor.ToolsApplicationRequestBus(bus.Broadcast, 'DeleteEntityById', scratch)
        if not isinstance(value, (int, float)):
            raise AdapterError(
                "could not read the Jolt contact offset (%r); tests derive "
                "their tolerances from it" % (value,))
        return float(value)

    def capabilities(self):
        return {
            base.CAP_SHAPE_BOX, base.CAP_SHAPE_SPHERE, base.CAP_SHAPE_CAPSULE,
            base.CAP_SHAPE_CYLINDER, base.CAP_SHAPE_CONVEX, base.CAP_SHAPE_TRIMESH,
            base.CAP_COMPOUND_STATIC, base.CAP_TRIGGER, base.CAP_KINEMATIC,
            base.CAP_CCD,
        }

    def contact_offset(self):
        if self._contact_offset is None:
            raise AdapterError("resolve_components() has not run")
        return self._contact_offset

    # -- shared plumbing ---------------------------------------------------

    def _add_component(self, entity_id, component_name):
        bus, editor = self._bus()
        type_id = self._type_ids.get(component_name)
        if type_id is None:
            raise AdapterError(
                "component %r requested before resolve_components()" % component_name)
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not outcome or not outcome.IsSuccess():
            raise AdapterError("AddComponentsOfType(%s) failed: %s"
                               % (component_name, self._outcome_error(outcome)))
        pair_outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id)
        if not pair_outcome or not pair_outcome.IsSuccess():
            raise AdapterError("component %r vanished after add" % component_name)
        return pair_outcome.GetValue()

    @staticmethod
    def _outcome_error(outcome):
        if outcome is None:
            return "no outcome returned"
        try:
            return repr(outcome.GetError())
        except Exception:
            return "outcome reported failure with no readable error"

    def _set(self, pair, path, value, what):
        bus, editor = self._bus()
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', pair, path, value)
        if not outcome or not outcome.IsSuccess():
            raise AdapterError("setting %s (%s) failed: %s"
                               % (what, path, self._outcome_error(outcome)))

    def _set_first(self, pair, candidate_paths, value, what):
        bus, editor = self._bus()
        for path in candidate_paths:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'SetComponentProperty', pair, path, value)
            if outcome and outcome.IsSuccess():
                return path
        paths = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair)
        raise AdapterError("no candidate path for %s matched; component offers %r"
                           % (what, sorted(paths or [])))

    def _vector3(self, values):
        import azlmbr.math as math
        return math.Vector3(float(values[0]), float(values[1]), float(values[2]))

    def _quaternion(self, xyzw):
        import azlmbr.math as math
        return math.Quaternion(float(xyzw[0]), float(xyzw[1]),
                               float(xyzw[2]), float(xyzw[3]))

    def _register_collider(self, entity_id, pair):
        self._colliders_by_entity.setdefault(entity_id.ToString(), []).append(pair)

    def _apply_placement(self, pair, local_offset, local_rotation, what):
        if local_offset is not None and any(abs(v) > 1e-9 for v in local_offset):
            self._set(pair, _P_OFFSET, self._vector3(local_offset), what + " offset")
        if local_rotation is not None:
            x, y, z, w = local_rotation
            if abs(x) > 1e-9 or abs(y) > 1e-9 or abs(z) > 1e-9 or abs(w - 1.0) > 1e-9:
                self._set(pair, _P_ROTATION, self._quaternion(local_rotation),
                          what + " rotation")

    # -- bodies ------------------------------------------------------------

    def add_static_body(self, entity_id, layer=None):
        self._add_component(entity_id, _BODY_STATIC)

    def add_dynamic_body(self, entity_id, mass=None, linear_damping=None,
                         angular_damping=None, gravity_enabled=True,
                         ccd=False, kinematic=False):
        pair = self._add_component(entity_id, _BODY_DYNAMIC)
        if kinematic:
            self._set(pair, _P_KINEMATIC, True, "kinematic flag")
        if mass is not None:
            self._set(pair, _P_MASS, float(mass), "mass")
        if linear_damping is not None:
            self._set(pair, _P_LINEAR_DAMPING, float(linear_damping), "linear damping")
        if angular_damping is not None:
            self._set(pair, _P_ANGULAR_DAMPING, float(angular_damping), "angular damping")
        if not gravity_enabled:
            self._set(pair, _P_GRAVITY, False, "gravity flag")
        if ccd:
            self._set(pair, _P_CCD, True, "CCD flag")

    # -- colliders ---------------------------------------------------------

    def add_box_collider(self, entity_id, half_extents, local_offset=None,
                         local_rotation=None, material=None, layer=None):
        pair = self._add_component(entity_id, _COLLIDER_BOX)
        # Jolt's Dimensions are FULL extents (verified: the default 1m box
        # reports 1.0, and UE's KBoxElem X/Y/Z are full extents too).
        full = [2.0 * float(v) for v in half_extents]
        self._set(pair, _P_BOX_DIMENSIONS, self._vector3(full), "box dimensions")
        self._apply_placement(pair, local_offset, local_rotation, "box")
        self._register_collider(entity_id, pair)
        return pair

    def add_sphere_collider(self, entity_id, radius, local_offset=None,
                            material=None, layer=None):
        pair = self._add_component(entity_id, _COLLIDER_SPHERE)
        self._set(pair, _P_RADIUS, float(radius), "sphere radius")
        self._apply_placement(pair, local_offset, None, "sphere")
        self._register_collider(entity_id, pair)
        return pair

    def add_capsule_collider(self, entity_id, radius, height, local_offset=None,
                             local_rotation=None, material=None, layer=None):
        pair = self._add_component(entity_id, _COLLIDER_CAPSULE)
        self._set(pair, _P_RADIUS, float(radius), "capsule radius")
        self._set(pair, _P_HEIGHT, float(height), "capsule height")
        self._apply_placement(pair, local_offset, local_rotation, "capsule")
        self._register_collider(entity_id, pair)
        return pair

    def add_cylinder_collider(self, entity_id, radius, height, local_offset=None,
                              local_rotation=None, material=None, layer=None):
        pair = self._add_component(entity_id, _COLLIDER_CYLINDER)
        self._set(pair, _P_RADIUS, float(radius), "cylinder radius")
        self._set(pair, _P_HEIGHT, float(height), "cylinder height")
        self._apply_placement(pair, local_offset, local_rotation, "cylinder")
        self._register_collider(entity_id, pair)
        return pair

    def add_mesh_collider(self, entity_id, convex, material=None, layer=None,
                          asset_id=None):
        # `asset_id` is the cooked-asset route (CAP_SHAPE_MESH_COOKED, PhysX);
        # this backend bakes from the render mesh and ignores it.
        pair = self._add_component(entity_id, _COLLIDER_MESH)
        mesh_type = _MESH_TYPE_CONVEX if convex else _MESH_TYPE_TRIANGLE
        # The bake itself runs on the component's own activation/tick once the
        # entity's render model is loaded; sequencing is the caller's job
        # (wait_for_asset happened before any component was authored).
        self._set_first(pair, _P_MESH_TYPE_CANDIDATES, mesh_type, "mesh type")
        self._register_collider(entity_id, pair)
        return pair

    # -- modifiers ---------------------------------------------------------

    def make_trigger(self, entity_id):
        pairs = self._colliders_by_entity.get(entity_id.ToString())
        if not pairs:
            raise AdapterError(
                "make_trigger on an entity with no adapter-authored colliders")
        for pair in pairs:
            self._set(pair, _P_TRIGGER, True, "trigger flag")

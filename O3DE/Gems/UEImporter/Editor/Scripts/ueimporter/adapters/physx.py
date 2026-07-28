"""
physx.py — the PhysX implementation of `PhysicsBackendAdapter` (plan M3b).

Every name, path and enum below was resolved live against PhysX5 in
UEtoO3DETest-PhysX before this file was written
(`Tests/o3de/results/probe_m3b_physx_result.txt` and `..._physx2_result.txt`).
PhysX is NOT a renamed Jolt; three differences shape this adapter:

  * ONE COLLIDER, MANY SHAPES. Jolt ships a component per shape; PhysX ships
    a single `PhysX Primitive Collider` whose `Shape Configuration|Shape`
    enum selects among sub-configs that ALL exist simultaneously
    (`...|Sphere|Radius`, `...|Box|Dimensions`, `...|Capsule|{Radius,Height}`,
    `...|Cylinder|{Radius,Height}` — measured). Setting the enum wrong yields
    a collider of the WRONG SHAPE that still simulates, so the shape numbers
    are behaviourally asserted by the M3b acceptance (a sphere and a box of
    known size must rest at their own analytic heights), not merely written
    down here.

  * MASS HAS A WRITE ORDER. `Configuration|Compute Mass` defaults TRUE and
    recomputes the mass, so a `Mass` written first is DISCARDED — measured:
    Mass=42 with Compute Mass on reads back 1.0, and only
    `Compute Mass=False` *then* `Mass=42` sticks. Same shape of trap as M5's
    light intensity-before-mode. `_MASS_ORDER_IS_LOAD_BEARING` marks it.

  * NO RENDER-MESH BAKE. Jolt's Mesh Collider builds collision from the
    entity's own render mesh on activation. PhysX's wants a COOKED asset
    (`Shape Configuration|Asset|PhysX Mesh`), which the Asset Processor
    produces only when the source FBX carries a PhysX mesh group. Until that
    pipeline exists this adapter does NOT advertise trimesh/convex, so
    `physics_build.negotiate` reports the gap up front instead of authoring a
    collider with no geometry — an empty collider is indistinguishable from a
    physics bug (constraint 5's whole point).

Also measured: `PhysX Static Rigid Body` has NO configurable properties;
kinematic is `Configuration|Type` (a Simulated/Kinematic combo) and accepts
BOOLS ONLY — ints are rejected; contact offset default is 0.02, read live
here the same way Jolt's is, never hard-coded.
"""

from . import base
from .base import AdapterError


_BODY_DYNAMIC = "PhysX Dynamic Rigid Body"
_BODY_STATIC = "PhysX Static Rigid Body"
_COLLIDER_PRIMITIVE = "PhysX Primitive Collider"
_COLLIDER_MESH = "PhysX Mesh Collider"

_ALL_COMPONENTS = (_BODY_DYNAMIC, _BODY_STATIC, _COLLIDER_PRIMITIVE,
                   _COLLIDER_MESH)

# Collider configuration (shared by every PhysX collider component).
_P_TRIGGER = "Collider Configuration|Trigger"
_P_OFFSET = "Collider Configuration|Offset"
_P_ROTATION = "Collider Configuration|Rotation"
_P_CONTACT_OFFSET = "Collider Configuration|Contact offset"

# Shape selection + per-shape sub-configs (all present regardless of enum).
_P_SHAPE = "Shape Configuration|Shape"
_P_SPHERE_RADIUS = "Shape Configuration|Sphere|Radius"
_P_BOX_DIMENSIONS = "Shape Configuration|Box|Dimensions"
_P_CAPSULE_RADIUS = "Shape Configuration|Capsule|Radius"
_P_CAPSULE_HEIGHT = "Shape Configuration|Capsule|Height"
_P_CYLINDER_RADIUS = "Shape Configuration|Cylinder|Radius"
_P_CYLINDER_HEIGHT = "Shape Configuration|Cylinder|Height"

# Physics::ShapeType. The collider ACCEPTS every value 0..9 and echoes it
# back, so a readback cannot tell these apart -- the M3b acceptance pins
# them by dropping known shapes and measuring where they rest.
_SHAPE_SPHERE = 0
_SHAPE_BOX = 1
_SHAPE_CAPSULE = 2
_SHAPE_CYLINDER = 3

# Rigid body configuration.
_P_KINEMATIC = "Configuration|Type"          # Simulated/Kinematic combo
_P_COMPUTE_MASS = "Configuration|Compute Mass"
_P_MASS = "Configuration|Mass"
_P_LINEAR_DAMPING = "Configuration|Linear damping"
_P_ANGULAR_DAMPING = "Configuration|Angular damping"
_P_GRAVITY = "Configuration|Gravity enabled"
_P_CCD = "Configuration|Continuous Collision Detection|CCD enabled"

# Set Compute Mass False BEFORE Mass or the value is recomputed away.
_MASS_ORDER_IS_LOAD_BEARING = True


class PhysXBackendAdapter(base.PhysicsBackendAdapter):

    def __init__(self):
        self._type_ids = {}
        self._contact_offset = None
        self._colliders_by_entity = {}

    # -- infrastructure ----------------------------------------------------

    def name(self):
        return "physx"

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
            raise AdapterError("PhysX component lookup returned %r for %r"
                               % (type_ids, names))
        misses = [name for name, type_id in zip(names, type_ids)
                  if type_id is None or type_id.IsNull()]
        if misses:
            raise AdapterError(
                "PhysX components did not resolve: %r. A silent miss here "
                "would produce a prefab with no physics, so this is fatal."
                % misses)
        self._type_ids = dict(zip(names, type_ids))
        self._contact_offset = self._read_contact_offset()

    def _read_contact_offset(self):
        import azlmbr.bus as bus
        import azlmbr.editor as editor
        import azlmbr.entity as entity_module

        scratch = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', scratch,
                                  '__physx_offset_probe')
        pair = self._add_component(scratch, _COLLIDER_PRIMITIVE)
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair, _P_CONTACT_OFFSET)
        value = outcome.GetValue() if outcome and outcome.IsSuccess() else None
        editor.ToolsApplicationRequestBus(bus.Broadcast, 'DeleteEntityById', scratch)
        if not isinstance(value, (int, float)):
            raise AdapterError(
                "could not read the PhysX contact offset (%r); tests derive "
                "their tolerances from it" % (value,))
        return float(value)

    def capabilities(self):
        # Trimesh/convex are DELIBERATELY absent: PhysX needs a cooked
        # `.pxmesh` asset and there is no render-mesh fallback (measured).
        # Advertising them would let physics_build author mesh colliders with
        # no geometry. Compound-static is absent too -- PhysX ships no
        # equivalent of Jolt's Static Compound Collider in this build.
        return {
            base.CAP_SHAPE_BOX, base.CAP_SHAPE_SPHERE, base.CAP_SHAPE_CAPSULE,
            base.CAP_SHAPE_CYLINDER, base.CAP_TRIGGER, base.CAP_KINEMATIC,
            base.CAP_CCD,
        }

    def contact_offset(self):
        if self._contact_offset is None:
            raise AdapterError("resolve_components() has not run")
        return self._contact_offset

    # -- shared plumbing (mirrors jolt.py deliberately) --------------------

    def _add_component(self, entity_id, component_name):
        """Add one component and return THE PAIR THAT WAS JUST ADDED.

        Jolt's adapter re-resolves the pair with `GetComponentOfType`, which
        is safe there because every Jolt shape is a DISTINCT component type.
        PhysX puts all four primitives on one `PhysX Primitive Collider`
        type, and GetComponentOfType documents itself as returning only the
        FIRST component of a type -- so on a two-collider entity the second
        add handed back collider #1, the second shape overwrote the first,
        and collider #2 kept its default shape (PhysicsAsset with a null
        mesh: no geometry at all) while `make_trigger` flagged only #1.
        Silent, and only on multi-shape bodies. Use the ADD outcome instead.
        """
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
        added = outcome.GetValue()
        # The outcome carries the new component id pair(s); take the last so
        # repeated adds of one type each return their own component.
        if isinstance(added, (list, tuple)):
            if not added:
                raise AdapterError(
                    "AddComponentsOfType(%s) reported success but returned no "
                    "component" % component_name)
            return added[-1]
        return added

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

    def _primitive(self, entity_id, shape_enum, what):
        pair = self._add_component(entity_id, _COLLIDER_PRIMITIVE)
        self._set(pair, _P_SHAPE, int(shape_enum), what + " shape selector")
        return pair

    # -- bodies ------------------------------------------------------------

    def add_static_body(self, entity_id, layer=None):
        # Measured: this component exposes no properties at all.
        self._add_component(entity_id, _BODY_STATIC)

    def add_dynamic_body(self, entity_id, mass=None, linear_damping=None,
                         angular_damping=None, gravity_enabled=True,
                         ccd=False, kinematic=False):
        pair = self._add_component(entity_id, _BODY_DYNAMIC)
        if kinematic:
            # Bools only: ints are rejected outright (measured).
            self._set(pair, _P_KINEMATIC, True, "kinematic flag")
        if mass is not None:
            # ORDER IS LOAD-BEARING (see module docstring): with Compute Mass
            # left on, the mass written here is recomputed away silently.
            self._set(pair, _P_COMPUTE_MASS, False, "compute-mass flag")
            self._set(pair, _P_MASS, float(mass), "mass")
        if linear_damping is not None:
            self._set(pair, _P_LINEAR_DAMPING, float(linear_damping),
                      "linear damping")
        if angular_damping is not None:
            self._set(pair, _P_ANGULAR_DAMPING, float(angular_damping),
                      "angular damping")
        if not gravity_enabled:
            self._set(pair, _P_GRAVITY, False, "gravity flag")
        if ccd:
            self._set(pair, _P_CCD, True, "CCD flag")

    # -- colliders ---------------------------------------------------------

    def add_box_collider(self, entity_id, half_extents, local_offset=None,
                         local_rotation=None, material=None, layer=None):
        pair = self._primitive(entity_id, _SHAPE_BOX, "box")
        # Dimensions are FULL extents, as in Jolt and in UE's KBoxElem.
        full = [2.0 * float(v) for v in half_extents]
        self._set(pair, _P_BOX_DIMENSIONS, self._vector3(full), "box dimensions")
        self._apply_placement(pair, local_offset, local_rotation, "box")
        self._register_collider(entity_id, pair)
        return pair

    def add_sphere_collider(self, entity_id, radius, local_offset=None,
                            material=None, layer=None):
        pair = self._primitive(entity_id, _SHAPE_SPHERE, "sphere")
        self._set(pair, _P_SPHERE_RADIUS, float(radius), "sphere radius")
        self._apply_placement(pair, local_offset, None, "sphere")
        self._register_collider(entity_id, pair)
        return pair

    def add_capsule_collider(self, entity_id, radius, height, local_offset=None,
                             local_rotation=None, material=None, layer=None):
        pair = self._primitive(entity_id, _SHAPE_CAPSULE, "capsule")
        self._set(pair, _P_CAPSULE_RADIUS, float(radius), "capsule radius")
        self._set(pair, _P_CAPSULE_HEIGHT, float(height), "capsule height")
        self._apply_placement(pair, local_offset, local_rotation, "capsule")
        self._register_collider(entity_id, pair)
        return pair

    def add_cylinder_collider(self, entity_id, radius, height, local_offset=None,
                              local_rotation=None, material=None, layer=None):
        pair = self._primitive(entity_id, _SHAPE_CYLINDER, "cylinder")
        self._set(pair, _P_CYLINDER_RADIUS, float(radius), "cylinder radius")
        self._set(pair, _P_CYLINDER_HEIGHT, float(height), "cylinder height")
        self._apply_placement(pair, local_offset, local_rotation, "cylinder")
        self._register_collider(entity_id, pair)
        return pair

    def add_mesh_collider(self, entity_id, convex, material=None, layer=None):
        # Not advertised in capabilities(), so physics_build should never get
        # here; if it does, say why rather than authoring an empty collider.
        raise AdapterError(
            "PhysX mesh colliders need a COOKED PhysX mesh asset "
            "(Shape Configuration|Asset|PhysX Mesh) produced by the Asset "
            "Processor from a source FBX carrying a PhysX mesh group. There "
            "is no bake-from-render-mesh path as there is on Jolt (measured, "
            "probe_m3b_physx2). Until that asset pipeline exists this backend "
            "does not advertise trimesh/convex, and negotiate() reports the "
            "gap up front.")

    # -- modifiers ---------------------------------------------------------

    def make_trigger(self, entity_id):
        pairs = self._colliders_by_entity.get(entity_id.ToString())
        if not pairs:
            raise AdapterError(
                "make_trigger on an entity with no adapter-authored colliders")
        for pair in pairs:
            self._set(pair, _P_TRIGGER, True, "trigger flag")

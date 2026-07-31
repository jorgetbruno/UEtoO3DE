"""probe_gltf_basis.py -- what basis does a glTF land in?

Lane B measured the FBX chain end to end (LANE_B.md): our exporter bakes
`scale_mesh(-1,-1,1)`, UE's FBX writer negates Y, O3DE's SceneAPI applies a
180 degree yaw, and units go cm -> m. **NONE of that carries over to glTF.**
glTF is Y-up right-handed in metres and O3DE ingests it through a different
import context provider (`AssImp`, named in the job log) than the FBX path.
So the basis is a MEASUREMENT, not an adaptation, and this probe makes it.

WHY THE COOKED PHYSICS AABB AND NOT THE MODEL'S BOUNDS. The obvious instrument
is `BoundsRequestBus.GetEntityLocalBoundsUnion`, which lane_b_measure.py used.
**It does not exist in this build** -- `probe_bounds_api.py` scanned every
azlmbr module and found only `MeshComponentNotificationBus`, with
`BoundsRequestBus` bound to None in components, entity and framework alike. The
physics AABB is a working, already-proven instrument here, and it measures the
geometry the COLLIDER pipeline actually gets, which is the basis question that
matters. It needs a cooked product, and glTF produces one.

The subject is `SM_LetterF`, which is in the fixture precisely because it is
asymmetric on two axes -- so a flip, a swap or a sign error shows up in the
numbers instead of hiding behind symmetry:

    UE asset space (cm, Z-up, left-handed)
        min (-50.0, -12.5,   0.0)
        max ( 50.0,  37.5, 200.0)
        -> extents (100, 50, 200) cm = (1.0, 0.5, 2.0) m
        -> centre  (0, 12.5, 100) cm = (0.0, 0.125, 1.0) m from the origin

Three bodies are measured in ONE game-mode session, so no cross-run drift can
be mistaken for a basis difference:

  1. a box collider   -- analytic control; a wrong reading here means the query
                         is broken and NOTHING below is evidence
  2. the glTF product -- raw UE .glb export, none of our bake
  3. the FBX product  -- the shipping path, bake included

This probe ASSERTS NOTHING about which basis is "right". It reports the mapping
it finds, because the compensation the importer needs is whatever that mapping
says -- and inventing the expected answer first is exactly how the four
rejected node-path guesses happened.

Run:
  Tests/o3de/run_o3de_python.bat Tests/o3de/probe_gltf_basis.py <result> <project>
"""

import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
for _path in (os.path.join(REPO_ROOT, "Tests", "lib"), GEM_SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import editor_physics  # noqa: E402

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_gltf_basis_result.txt'))
JSON_OUT = os.path.join(SCRIPT_DIR, 'results', 'probe_gltf_basis.json')

BOX_HALF = [1.0, 1.0, 0.45]
ASSET_LOAD_FRAMES = int(os.environ.get('UEO3DE_PROBE_LOAD_FRAMES', '300'))

# UE asset space, centimetres, Z-up left-handed. From
# Exports/LaneB/SM_LetterF.ue_reference.json.
UE_MIN_CM = (-50.0, -12.5, 0.0)
UE_MAX_CM = (50.0, 37.5, 200.0)

lines = []
failures = []


def log(message=""):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def describe(minimum, maximum, origin_x):
    size = [maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z]
    centre = [(maximum.x + minimum.x) * 0.5 - origin_x,
              (maximum.y + minimum.y) * 0.5,
              (maximum.z + minimum.z) * 0.5]
    return {'size': size, 'centre': centre,
            'min': [minimum.x, minimum.y, minimum.z],
            'max': [maximum.x, maximum.y, maximum.z]}


def axis_mapping(want_extents, got_extents, tolerance=0.02):
    """Which measured axis each UE axis became, matched by extent length.

    Returns [(ue_axis, product_axis, want, got), ...] or None when the extents
    cannot be matched one-to-one -- which is itself the finding, because it
    means the mesh is not a rigid re-basing of the UE asset.
    """
    names = 'XYZ'
    mapping = []
    used = set()
    for i, want in enumerate(want_extents):
        best, best_err = None, None
        for j, got in enumerate(got_extents):
            if j in used:
                continue
            err = abs(got - want)
            if best_err is None or err < best_err:
                best, best_err = j, err
        if best is None or best_err > tolerance:
            return None
        used.add(best)
        mapping.append((names[i], names[best], want, got_extents[best]))
    return mapping


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import asset_wait
    from ueimporter.adapters import base, detect_in_editor, make_adapter

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    detection = detect_in_editor(explicit=os.environ.get("UEO3DE_BACKEND") or None)
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    backend = adapter.name()
    suffix = "pxmesh" if backend == "physx" else "joltmesh"
    log("backend: %s (cooked product suffix .%s)" % (backend, suffix))

    if base.CAP_SHAPE_MESH_COOKED not in adapter.capabilities():
        fail("%s does not advertise cooked mesh colliders; this probe needs one"
             % backend)
        return

    ue_ext_cm = [b - a for a, b in zip(UE_MIN_CM, UE_MAX_CM)]
    ue_ext_m = [v / 100.0 for v in ue_ext_cm]
    ue_centre_m = [(a + b) * 0.5 / 100.0 for a, b in zip(UE_MIN_CM, UE_MAX_CM)]
    log("UE reference: extents %s m, centre %s m"
        % ([round(v, 4) for v in ue_ext_m], [round(v, 4) for v in ue_centre_m]))

    subjects = [
        ("gltf", "assets/uetoo3de/glbprobe/sm_letterf.glb." + suffix, 40.0),
        ("fbx", "assets/uetoo3de/game/meshes/sm_letterf.fbx." + suffix, 80.0),
    ]

    prefix = "GB_"

    def spawn(name, x):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, prefix + name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(float(x), 0.0, 0.0))
        return entity_id

    control = spawn("box", 0.0)
    adapter.add_static_body(control)
    adapter.add_box_collider(control, BOX_HALF)

    placed = []
    log("")
    for label, product, origin_x in subjects:
        asset_id = asset_wait.resolve(product)
        log("  %-5s %-58s %s" % (label, product,
                                 "resolved" if asset_id else "NOT IN CATALOG"))
        if asset_id is None:
            continue
        subject = spawn(label, origin_x)
        adapter.add_static_body(subject)
        adapter.add_mesh_collider(subject, convex=True, asset_id=asset_id)
        placed.append((label, origin_x))

    if not any(label == "gltf" for label, _ in placed):
        fail("no cooked glTF product in the catalog -- stage "
             "Tests/ue/data/SM_LetterF.glb into <project>/Assets/uetoo3de/glbprobe "
             "with a sidecar and run AssetProcessorBatch first")

    general.idle_wait_frames(ASSET_LOAD_FRAMES)
    general.enter_game_mode()
    general.idle_wait_frames(60)
    if not general.is_in_game_mode():
        fail("editor did not enter game mode")
        return

    readings = {}
    for label, origin_x in [("box", 0.0)] + placed:
        game_id = general.find_game_entity(prefix + label)
        if game_id is None or not game_id.IsValid():
            fail("no game entity for %s" % label)
            continue
        minimum, maximum, how = editor_physics.body_aabb_with_source(game_id)
        if minimum is None:
            readings[label] = None
            log("")
            log("=== %s: NO AABB (bus %s) ===" % (label, how))
            continue
        readings[label] = describe(minimum, maximum, origin_x)
        entry = readings[label]
        log("")
        log("=== %s (entity at x=%.1f, bus %s) ===" % (label, origin_x, how))
        log("  size   (%8.4f, %8.4f, %8.4f)" % tuple(entry['size']))
        log("  centre (%8.4f, %8.4f, %8.4f)  relative to the entity"
            % tuple(entry['centre']))

    general.exit_game_mode()
    general.idle_wait_frames(10)

    # CONTROL FIRST. The box's AABB is analytic; a wrong reading here means the
    # query is the problem and every mesh number above says nothing at all.
    box = readings.get("box")
    if not box:
        fail("the box control returned no AABB -- this probe cannot measure "
             "anything on this build, and the readings above are not evidence")
        return
    expected = [2.0 * half for half in BOX_HALF]
    if max(abs(box['size'][i] - expected[i]) for i in range(3)) > 0.15:
        fail("the box control's AABB size is %r, not its authored %r; the query "
             "is not measuring what it claims"
             % ([round(v, 3) for v in box['size']], expected))
        return
    log("")
    log("  control OK: box AABB size %r matches the authored %r"
        % ([round(v, 3) for v in box['size']], expected))

    out = {'backend': backend, 'ue_extents_m': ue_ext_m,
           'ue_centre_m': ue_centre_m, 'box_control': box}

    log("")
    log("=== what basis did each land in? ===")
    for label in ("gltf", "fbx"):
        entry = readings.get(label)
        out[label] = entry
        if not entry:
            log("  %s: not measured" % label)
            continue
        size = entry['size']
        biggest = max(size)
        if biggest > 20.0:
            unit = 'cm_kept'
            note = "CENTIMETRES kept 1:1 -- no unit conversion (mesh 100x too big)"
        elif abs(biggest - 2.0) < 0.05:
            unit = 'metres'
            note = "METRES -- cm -> m applied"
        else:
            unit = 'unknown'
            note = "matches NEITHER cm nor m"
        log("  %s: largest extent %.4f -> %s" % (label, biggest, note))
        mapping = axis_mapping(ue_ext_m, size)
        if mapping is None:
            log("    axis mapping: NOT one-to-one by extent -- this is not a "
                "rigid re-basing of the UE asset (scale or shear present)")
        else:
            for ue_axis, product_axis, want, got in mapping:
                log("    UE %s (%.4f m) -> measured %s (%.4f)"
                    % (ue_axis, want, product_axis, got))
        out[label + '_unit'] = unit
        out[label + '_axis_mapping'] = [list(m) for m in mapping] if mapping else None

    gltf, fbx = readings.get("gltf"), readings.get("fbx")
    if gltf and fbx:
        log("")
        log("=== does the FBX basis carry over to glTF? ===")
        same_size = max(abs(a - b) for a, b in zip(gltf['size'], fbx['size'])) < 0.01
        same_centre = max(abs(a - b) for a, b in zip(gltf['centre'], fbx['centre'])) < 0.01
        out['same_size_as_fbx'] = same_size
        out['same_centre_as_fbx'] = same_centre
        if same_size and same_centre:
            log("  IDENTICAL size and centre. The two formats land in the same "
                "basis, so the importer needs no per-format compensation.")
        else:
            log("  DIFFERENT. The importer needs a glTF-specific basis:")
            for i, axis in enumerate('XYZ'):
                log("    %s  size %8.4f -> %8.4f    centre %+8.4f -> %+8.4f"
                    % (axis, fbx['size'][i], gltf['size'][i],
                       fbx['centre'][i], gltf['centre'][i]))
            log("  (left column FBX, right column glTF)")

    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, 'w') as handle:
        json.dump(out, handle, indent=2)
    log("")
    log("wrote " + JSON_OUT)


try:
    main()
except Exception:
    fail('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if not failures else 'FAIL (%d)' % len(failures)))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if not failures:
    _general.exit_no_prompt()
else:
    os._exit(1)

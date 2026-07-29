"""
probe_collider_scale.py — does a collider follow the entity's transform scale?

The importer BAKES the entity's world scale into every collider dimension
(`physics_build._scaled`), on the stated grounds that "collider components
live outside the transform's scale". That premise was never measured. Two
questions follow from it, and both are answerable by simulation:

  Q1 PRIMITIVES. If a box collider DOES follow the transform, then baking the
     scale in as well makes every scaled entity's collision scale SQUARED --
     a shipped, silent, backend-independent error on 1,924 of 3,290 collidable
     entities in one real level.

  Q2 COOKED MESHES (the new PhysX path). A `.pxmesh` is asset-space and shared
     by every instance, and `add_mesh_collider` passes no dimensions at all,
     so there is nothing to bake a scale into. If the transform does not scale
     it, a 2x instance collides at 1x -- and the AABB boxes this path replaced
     WERE scaled, making it a fidelity regression on the majority of a level.

METHOD. Ratios, not absolutes: the same collider at scale 1 and at scale 2,
each under a small sphere dropped from above, resting height read in game
mode. Geometry cancels out -- only the RATIO is interpreted, so no analytic
model of a rock's convex hull is needed.

    rest(scale 2) / rest(scale 1)  ~= 2  -> the transform scales the collider
                                   ~= 1  -> it does not

A third case sets the mesh collider's own Asset Scale property to 2 on an
unscaled entity, which says whether that property is the lever to use if the
transform is not.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_collider_scale.py \
         <result> C:/Users/jorge/O3DE/Projects/UEtoO3DETest-PhysX
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_collider_scale_result.txt'))

# Any cooked convex product in the project under test; its actual shape is
# deliberately irrelevant (ratios only). Override with UEO3DE_PXMESH.
PXMESH = (os.environ.get("UEO3DE_PXMESH", "").strip()
          or "assets/uetoo3de/game/meshes/cylinder.fbx.pxmesh")
BOX_HALF_Z = 0.45
DROP_Z = 8.0
SIM_FRAMES = 60
SETTLE_TICKS = 6

lines = []
failures = []


def log(message):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import asset_wait
    from ueimporter.adapters import detect_in_editor, make_adapter

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    detection = detect_in_editor(explicit="physx")
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    log("adapter: %s" % adapter.name())

    from ueimporter.adapters import base as _base
    cooked_capable = _base.CAP_SHAPE_MESH_COOKED in adapter.capabilities()
    asset_id = asset_wait.resolve(PXMESH) if cooked_capable else None
    if cooked_capable and asset_id is None:
        fail("cooked mesh %s is not in the catalog; stage the Ponthus export "
             "into this project and run AssetProcessorBatch first" % PXMESH)
        return
    log("cooked-mesh capable: %s" % cooked_capable)

    def spawn(name, position, scale=None):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, name)
        components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                                azmath.Vector3(*[float(v) for v in position]))
        if scale is not None:
            # Uniform transform scale, the only kind AZ::Transform carries.
            components.TransformBus(bus.Event, 'SetLocalUniformScale',
                                    entity_id, float(scale))
        return entity_id

    floor = spawn('P_Floor', (0.0, 0.0, -0.5))
    adapter.add_static_body(floor)
    adapter.add_box_collider(floor, [40.0, 40.0, 0.5])

    subjects = []

    # --- Q1: a primitive box, same dimensions, scale 1 vs 2 ---
    for label, x, scale in (('box_s1', 0.0, 1.0), ('box_s2', 10.0, 2.0)):
        target = spawn('P_' + label, (x, 0.0, 0.0), scale=scale)
        adapter.add_static_body(target)
        adapter.add_box_collider(target, [1.0, 1.0, BOX_HALF_Z])
        ball = spawn('P_' + label + '_ball', (x, 0.0, DROP_Z))
        adapter.add_dynamic_body(ball)
        adapter.add_sphere_collider(ball, 0.2)
        subjects.append((label, 'P_' + label + '_ball'))

    # --- Q1b: NON-uniform scale, which the importer carries on a separate
    # component (EditorNonUniformScaleComponent) with the transform left at
    # 1.0. DIVERGENCES.md records its interaction with colliders as
    # uncontracted; that is exactly what this measures. Z is what the rest
    # height reads, so scale Z and leave X/Y alone.
    from ueimporter import prefab_build as _pb
    target = spawn('P_box_nonuni', (50.0, 0.0, 0.0))
    outcome = editor.EditorComponentAPIBus(
        bus.Broadcast, 'AddComponentsOfType', target,
        [_pb._uuid_from_string(_pb.NON_UNIFORM_SCALE_TYPE_ID)])
    nonuniform_ok = bool(outcome and outcome.IsSuccess())
    if nonuniform_ok:
        entity_module.NonUniformScaleRequestBus(
            bus.Event, 'SetScale', target, azmath.Vector3(1.0, 1.0, 2.0))
        read = entity_module.NonUniformScaleRequestBus(bus.Event, 'GetScale', target)
        log("  non-uniform scale component added; reads back z=%.3f" % read.z)
    else:
        log("  could NOT add the non-uniform scale component; that leg is blank")
    adapter.add_static_body(target)
    adapter.add_box_collider(target, [1.0, 1.0, BOX_HALF_Z])
    ball = spawn('P_box_nonuni_ball', (50.0, 0.0, DROP_Z))
    adapter.add_dynamic_body(ball)
    adapter.add_sphere_collider(ball, 0.2)
    subjects.append(('box_nonuni', 'P_box_nonuni_ball'))

    if cooked_capable:
        # --- Q2: the cooked mesh, same asset, scale 1 vs 2 ---
        for label, x, scale in (('mesh_s1', 20.0, 1.0), ('mesh_s2', 30.0, 2.0)):
            target = spawn('P_' + label, (x, 0.0, 0.0), scale=scale)
            adapter.add_static_body(target)
            adapter.add_mesh_collider(target, convex=True, asset_id=asset_id)
            ball = spawn('P_' + label + '_ball', (x, 0.0, DROP_Z))
            adapter.add_dynamic_body(ball)
            adapter.add_sphere_collider(ball, 0.2)
            subjects.append((label, 'P_' + label + '_ball'))

        # --- Q3: unscaled entity, Asset Scale property set to 2 ---
        target = spawn('P_mesh_assetscale', (40.0, 0.0, 0.0))
        adapter.add_static_body(target)
        pair = adapter.add_mesh_collider(target, convex=True, asset_id=asset_id)
        asset_scale_ok = False
        try:
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'SetComponentProperty', pair,
                'Shape Configuration|Asset|Configuration|Asset Scale',
                azmath.Vector3(2.0, 2.0, 2.0))
            asset_scale_ok = bool(outcome and outcome.IsSuccess())
        except Exception as exc:
            log("  Asset Scale write raised: %s" % exc)
        log("  Asset Scale property writable: %s" % asset_scale_ok)
        ball = spawn('P_mesh_assetscale_ball', (40.0, 0.0, DROP_Z))
        adapter.add_dynamic_body(ball)
        adapter.add_sphere_collider(ball, 0.2)
        subjects.append(('mesh_assetscale', 'P_mesh_assetscale_ball'))

    general.idle_wait_frames(60)
    general.enter_game_mode()
    general.idle_wait_frames(30)
    if not check_game_mode(general):
        return
    for _ in range(SETTLE_TICKS):
        general.idle_wait_frames(SIM_FRAMES)

    rest = {}
    for label, ball_name in subjects:
        game_id = general.find_game_entity(ball_name)
        if game_id is None or not game_id.IsValid():
            rest[label] = None
            continue
        rest[label] = components.TransformBus(
            bus.Event, 'GetWorldTranslation', game_id).z
    general.exit_game_mode()
    general.idle_wait_frames(10)

    log("")
    log("=== resting heights (ball radius 0.2, floor top z=0) ===")
    for label, _ball in subjects:
        log("  %-18s %s" % (label, "None" if rest[label] is None
                            else "%.4f" % rest[label]))

    def ratio(a, b):
        if not rest.get(a) or not rest.get(b):
            return None
        # Subtract the ball radius so the ratio is of the SURFACE heights.
        top_a, top_b = rest[a] - 0.2, rest[b] - 0.2
        return None if abs(top_a) < 1e-6 else top_b / top_a

    log("")
    log("=== verdicts ===")
    box_ratio = ratio('box_s1', 'box_s2')
    nonuni_ratio = ratio('box_s1', 'box_nonuni')
    mesh_ratio = ratio('mesh_s1', 'mesh_s2')
    asset_ratio = ratio('mesh_s1', 'mesh_assetscale')
    log("  primitive box   transform scale2/scale1     = %s"
        % ("n/a" if box_ratio is None else "%.3f" % box_ratio))
    log("  primitive box   NON-uniform z=2 / scale1    = %s"
        % ("n/a" if nonuni_ratio is None else "%.3f" % nonuni_ratio))
    log("  cooked mesh     transform scale2/scale1     = %s"
        % ("n/a" if mesh_ratio is None else "%.3f" % mesh_ratio))
    log("  cooked mesh     assetScale2/scale1          = %s"
        % ("n/a" if asset_ratio is None else "%.3f" % asset_ratio))
    log("")
    for name, value in (("primitive box (transform scale)", box_ratio),
                        ("primitive box (non-uniform component)", nonuni_ratio),
                        ("cooked mesh (transform scale)", mesh_ratio),
                        ("cooked mesh via Asset Scale", asset_ratio)):
        if value is None:
            log("  %s: NO READING" % name)
        elif abs(value - 2.0) < 0.15:
            log("  %s: FOLLOWS the scale (ratio ~2)" % name)
        elif abs(value - 1.0) < 0.15:
            log("  %s: IGNORES the scale (ratio ~1)" % name)
        else:
            log("  %s: ratio %.3f -- neither 1 nor 2, look closer" % (name, value))

    log("")
    log("Interpretation. A primitive ratio of ~2 means the transform scales "
        "colliders and the importer's own scale-baking DOUBLES it. A cooked-mesh "
        "ratio of ~1 with a primitive ratio of ~1 means the cooked path needs "
        "Asset Scale set explicitly (third row says whether that lever works).")


def check_game_mode(general):
    if not general.is_in_game_mode():
        fail("editor did not enter game mode; no rest heights measurable")
        return False
    return True


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

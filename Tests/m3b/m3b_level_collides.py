"""
m3b_level_collides.py -- does an IMPORTED level actually collide?

Everything else in this repo that checks physics on a real import checks that
the right components, asset references and dimensions were WRITTEN. That is a
claim about a file. It is not the claim anyone cares about, and the two came
apart twice this month:

  * a mesh collider whose bake had not finished when the prefab was serialized
    is written out fully configured with NO geometry -- it collides with
    nothing, the file saves cleanly, and every counter reports it as authored
    (15 of 2501 on one level, invisible to every suite);
  * a cooked mesh collider was believed dead for a week because a drop test
    could not stop a ball on it, while the collider was in fact fine.

So this test loads the SAVED prefab in a fresh session, enters game mode, and
asks the physics system what it actually built:

  every entity carrying a collider AND a body must have a simulated body whose
  world AABB is non-degenerate, finite, and sitting on that entity.

That is deliberately a weak assertion per entity -- it does not check sizes,
which `m3b_scale_acceptance.py` does -- and a strong one across the level: it
is the difference between "3290 colliders were written" and "3290 colliders
exist in the running world".

CONTROLS, in both directions, because either one alone is worthless:
  * POSITIVE: a box collider built in this same session, alongside the prefab,
    must report a body with its authored size. Without this, "no prefab entity
    has a body" is indistinguishable from "this bus never answers" -- which is
    exactly what the first run of this test could not tell apart.
  * NEGATIVE: entities with NO collider must have NO body. Without this, a bus
    that answers for everything would make every positive reading meaningless.
  * the prefab must contain at least one collidable entity, or the whole run
    is vacuous and fails rather than passing quietly.

Env: UEO3DE_PREFAB         prefab to load (default: the M3b import's output)
     UEO3DE_COLLIDE_SAMPLE max entities to check (default 40; the count
                           checked and the count skipped are both reported --
                           a silent cap reads as full coverage)
Run: Tests/o3de/run_o3de_python.bat Tests/m3b/m3b_level_collides.py \
         <result> <project>
"""

import json
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
               else os.path.join(SCRIPT_DIR, 'results', 'm3b_level_collides_result.txt'))

SAMPLE = int(os.environ.get("UEO3DE_COLLIDE_SAMPLE", "40"))
MIN_EXTENT = 0.005      # metres; `physics_build` clamps its own shapes at 0.01
MAX_EXTENT = 10000.0    # metres; a runaway shape rather than a real one

lines = []
failures = []


def log(message):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def walk_entities(node):
    """Every serialized entity in a prefab document."""
    if isinstance(node, dict):
        if "Name" in node and "Components" in node:
            yield node
        for value in node.values():
            for found in walk_entities(value):
                yield found
    elif isinstance(node, list):
        for value in node:
            for found in walk_entities(value):
                yield found


def classify(prefab_path):
    """(names with collider+body, names with neither) out of the saved prefab.

    Matched on the component type NAME, which is the one thing both gems spell
    the same way -- `Editor(Jolt)?...ColliderComponent` and
    `Editor(Jolt)?(Static)?RigidBodyComponent`. This test is about whether a
    body exists at all, so it never needs to know which backend wrote it.
    """
    with open(prefab_path, "r", encoding="utf-8") as handle:
        document = json.load(handle)

    collidable, inert = [], []
    for entity in walk_entities(document):
        types = [str(component.get("$type", ""))
                 for component in (entity.get("Components") or {}).values()
                 if isinstance(component, dict)]
        has_collider = any("ColliderComponent" in name for name in types)
        has_body = any("RigidBodyComponent" in name for name in types)
        name = str(entity.get("Name", ""))
        if not name:
            continue
        if has_collider and has_body:
            collidable.append(name)
        elif not has_collider and not has_body:
            inert.append(name)
    return collidable, inert


def spread(names, limit):
    """`limit` names spread across the list, not the first `limit` of them."""
    if len(names) <= limit:
        return list(names)
    step = len(names) / float(limit)
    return [names[int(index * step)] for index in range(limit)]


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath
    import azlmbr.prefab as prefab

    from ueimporter.adapters import detect_in_editor, make_adapter

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    expected_backend = os.environ.get("UEO3DE_EXPECT_BACKEND", "").strip() or None
    detection = detect_in_editor(explicit=os.environ.get("UEO3DE_BACKEND") or None)
    adapter = make_adapter(detection["backend"])
    backend = adapter.name()
    log("backend: %s" % backend)
    if expected_backend and backend != expected_backend:
        fail("this project resolved %r, not the expected %r"
             % (backend, expected_backend))
        return

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = os.environ.get("UEO3DE_PREFAB", "").strip() or \
        "%s/Prefabs/Fixture_01_M3b_%s.prefab" % (project_root, backend)
    log("prefab: %s" % prefab_path)
    if not check(os.path.isfile(prefab_path),
                 "no prefab at %s -- run the M3b manifest import first; this "
                 "test refuses to pass without a real imported level"
                 % prefab_path):
        return

    collidable, inert = classify(prefab_path)
    log("  %d entities carry a collider AND a body | %d carry neither"
        % (len(collidable), len(inert)))
    if not check(collidable,
                 "the prefab contains no collidable entity at all; this run "
                 "would assert nothing"):
        return

    checked = spread(collidable, SAMPLE)
    controls = spread(inert, 3)
    log("  checking %d of them%s"
        % (len(checked),
           "" if len(checked) == len(collidable)
           else " (SAMPLED; %d not checked)" % (len(collidable) - len(checked))))

    outcome = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_path,
        entity_module.EntityId(), azmath.Vector3(0.0, 0.0, 0.0))
    if not check(outcome is not None and outcome.IsSuccess(),
                 "InstantiatePrefab failed for " + prefab_path):
        return
    general.idle_wait_frames(120)

    # POSITIVE CONTROL, built in this session next to the instantiated prefab
    # and read by the identical code path. It is the only thing that separates
    # "the prefab's colliders are dead" from "this query never answers".
    adapter.resolve_components()
    control_name = "LC_PositiveControl"
    control_half = [1.5, 1.0, 0.5]
    control_id = editor.ToolsApplicationRequestBus(
        bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', control_id, control_name)
    components.TransformBus(bus.Event, 'SetWorldTranslation', control_id,
                            azmath.Vector3(500.0, 500.0, 0.0))
    adapter.add_static_body(control_id)
    adapter.add_box_collider(control_id, control_half)
    general.idle_wait_frames(30)

    general.enter_game_mode()
    general.idle_wait_frames(90)
    if not check(general.is_in_game_mode(), "editor did not enter game mode"):
        return

    def body_aabb(game_id):
        """(min, max) of the entity's simulated body, or (None, None).

        The Aabb comes back as a PythonProxyObject whose corners are reached
        as `min`/`max` ATTRIBUTES on this build, not `GetMin()`/`GetMax()`
        methods -- the method names exist and are None. An earlier version of
        this function checked only for the methods, so it reported "no
        simulated body" for every entity in the level, including a control
        collider built two lines earlier. Both spellings are tried here.
        """
        import azlmbr.physics as physics
        for handler_name in ('SimulatedBodyComponentRequestBus',
                             'SimulatedBodyComponentRequestsBus',
                             'RigidBodyRequestBus'):
            handler = getattr(physics, handler_name, None)
            if handler is None:
                continue
            try:
                aabb = handler(bus.Event, 'GetAabb', game_id)
            except Exception:  # noqa: BLE001 - try the next binding
                continue
            if aabb is None:
                continue
            if all(callable(getattr(aabb, name, None)) for name in ('GetMin', 'GetMax')):
                return aabb.GetMin(), aabb.GetMax()
            minimum = getattr(aabb, 'min', None)
            maximum = getattr(aabb, 'max', None)
            if minimum is not None and maximum is not None and hasattr(minimum, 'x'):
                return minimum, maximum
        return None, None

    def measured_size(game_id):
        """The body's AABB size, or None when there is no real body.

        `GetAabb` answers for ANY valid entity -- a light and a prefab container
        answer it too -- so "the bus replied" is not evidence of collision. What
        distinguishes a body is a NON-DEGENERATE, finite box: AZ::Aabb's null
        value has min above max, which reads here as a negative extent. The
        negative control below is what caught this: the first version treated
        any reply as a body and would have called every entity in the level
        collidable.
        """
        minimum, maximum = body_aabb(game_id)
        if minimum is None:
            return None
        size = [maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z]
        if min(size) < MIN_EXTENT or max(size) > MAX_EXTENT:
            return None
        centre = [(maximum.x + minimum.x) * 0.5, (maximum.y + minimum.y) * 0.5,
                  (maximum.z + minimum.z) * 0.5]
        return size, centre

    control_game_id = general.find_game_entity(control_name)
    control_measured = None
    if control_game_id is not None and control_game_id.IsValid():
        control_measured = measured_size(control_game_id)

    results = []
    for name in checked:
        game_id = general.find_game_entity(name)
        if game_id is None or not game_id.IsValid():
            results.append((name, "NO GAME ENTITY", None, None))
            continue
        position = components.TransformBus(bus.Event, 'GetWorldTranslation', game_id)
        measured = measured_size(game_id)
        if measured is None:
            results.append((name, "NO SIMULATED BODY", position, None))
            continue
        size, centre = measured
        drift = max(abs(centre[0] - position.x), abs(centre[1] - position.y),
                    abs(centre[2] - position.z))
        results.append((name, None, position, (size, centre, drift)))

    control_bodies = []
    for name in controls:
        game_id = general.find_game_entity(name)
        if game_id is None or not game_id.IsValid():
            continue
        control_bodies.append((name, measured_size(game_id)))

    general.exit_game_mode()
    general.idle_wait_frames(10)

    # POSITIVE CONTROL FIRST: if a collider built in this session does not
    # answer either, nothing below is about the prefab.
    log("")
    log("=== control: a collider built in this session must report a body ===")
    if control_measured is None:
        fail("the positive control (a %r box authored through the adapter in "
             "this session) reports no simulated body. This run says nothing "
             "about the imported prefab -- the query itself is not working "
             "here, and the prefab entities' silence is that, not a defect in "
             "the import" % control_half)
        return
    control_size = control_measured[0]
    expected = [2.0 * half for half in control_half]
    log("  control AABB %r (authored %r)"
        % ([round(v, 3) for v in control_size], expected))
    if not check(max(abs(control_size[i] - expected[i]) for i in range(3)) < 0.05,
                 "the positive control's AABB is %r, not its authored %r; the "
                 "query is not measuring collider geometry"
                 % ([round(v, 3) for v in control_size], expected)):
        return

    # NEGATIVE CONTROL: `GetAabb` replies for every valid entity, so if a
    # REAL box came back for an entity with no collider, this test would be
    # calling lights and container entities collidable.
    log("")
    log("=== control: entities with no collider must have no real body ===")
    if not control_bodies:
        log("  none available in this prefab (not fatal, but the positive "
            "readings below rest on one control)")
    for name, measured in control_bodies:
        log("  %-40s %s"
            % (name, "REAL BODY %r" % [round(v, 3) for v in measured[0]]
               if measured else "no body OK"))
        check(measured is None,
              "%r carries no collider yet reports a real simulated body %r; "
              "the query is not measuring collision"
              % (name, measured and [round(v, 3) for v in measured[0]]))

    log("")
    log("=== collidable entities ===")
    bad = 0
    for name, problem, position, measured in results:
        if problem:
            bad += 1
            fail("%s: %s -- it was written into the prefab with a collider and "
                 "a body, so the file says it collides and the running world "
                 "says it does not" % (name, problem))
            continue
        size, centre, drift = measured
        smallest, largest = min(size), max(size)
        if smallest < MIN_EXTENT:
            bad += 1
            fail("%s: collider geometry is degenerate %r -- a fully configured "
                 "collider carrying no shape, which is exactly what an unfinished "
                 "bake serializes as" % (name, [round(v, 4) for v in size]))
            continue
        if largest > MAX_EXTENT:
            bad += 1
            fail("%s: collider AABB spans %.0f m" % (name, largest))
            continue
        if drift > max(largest, 1.0):
            bad += 1
            fail("%s: collision sits %.2f m from the entity (AABB centre %r, "
                 "entity at %.2f, %.2f, %.2f) -- the shape is real but not "
                 "where the entity is"
                 % (name, drift, [round(v, 2) for v in centre],
                    position.x, position.y, position.z))
            continue
    log("  %d of %d checked entities have real collision in the running world"
        % (len(results) - bad, len(results)))
    if bad == 0 and results:
        widest = max(max(m[0]) for _n, p, _pos, m in results if not p)
        log("  (largest collider AABB extent seen: %.2f m)" % widest)


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

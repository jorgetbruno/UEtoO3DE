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
for _path in (os.path.join(REPO_ROOT, "Tests", "lib"), GEM_SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import editor_physics  # noqa: E402

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'm3b_level_collides_result.txt'))

SAMPLE = int(os.environ.get("UEO3DE_COLLIDE_SAMPLE", "40"))

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
    # Honour the scratch-level env: on a project whose DefaultLevel holds the
    # user's real scene, opening it here discards their unsaved edits with no
    # prompt. Test projects keep the stock default.
    general.open_level_no_prompt(
        os.environ.get("UEO3DE_SCRATCH_LEVEL", "").strip() or "DefaultLevel")
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

    # `editor_physics.body_extents` is the predicate, not a convenience: it
    # knows that the Aabb proxy spells its corners `min`/`max` on this build,
    # and that `GetAabb` REPLIES FOR EVERY VALID ENTITY, so a reply is not a
    # body. Both facts were learned by getting them wrong here.
    measured_size = editor_physics.body_extents

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
            # Two different defects reach here and they are worth separating in
            # the report: no body was created at all, or a body exists whose
            # shape carries no geometry -- which is precisely what a collider
            # serialized before its bake finished looks like.
            raw_min, raw_max = editor_physics.body_aabb(game_id)
            problem = ("NO SIMULATED BODY" if raw_min is None else
                       "A BODY WITH NO GEOMETRY (AABB %r)"
                       % [round(raw_max.x - raw_min.x, 4),
                          round(raw_max.y - raw_min.y, 4),
                          round(raw_max.z - raw_min.z, 4)])
            results.append((name, problem, position, None))
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
    flat = []
    for name, problem, position, measured in results:
        if problem:
            bad += 1
            fail("%s: %s -- it was written into the prefab with a collider and "
                 "a body, so the file says it collides and the running world "
                 "does not agree" % (name, problem))
            continue
        # Null, point and line extents were already rejected by
        # `body_extents`, and arrive above as a named problem. What is left to
        # check is placement.
        size, centre, drift = measured
        if editor_physics.is_flat(size):
            # NOT a failure: a zero-thickness triangle mesh is a surface and
            # collides. It is reported because it is also what a solid shape
            # collapses to when the importer fell back from a convex to the
            # cooked RENDER mesh -- fidelity worth seeing, not a defect.
            flat.append((name, size))
        largest = max(size)
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
    if flat:
        # Reported, never failed. A flat collider is a real surface, but it is
        # also the shape a solid convex collapses to when the importer had to
        # fall back to the cooked RENDER mesh (UE's convex vertices are not
        # reachable from Python -- DIVERGENCES.md). Measured on SiegeOfPonthus:
        # SM_Floor is a genuinely flat 5.0 x 5.0 x 0.0 m plane in BOTH the FBX
        # and glb exports, so its floors collide as surfaces and nothing is
        # wrong with them.
        log("  %d of them are FLAT (zero thickness on one axis) -- real "
            "surfaces that collide, but also what a convex collapses to when "
            "the collider came from the render mesh:" % len(flat))
        for name, size in flat[:5]:
            log("      %-42s %r" % (name, [round(v, 3) for v in size]))
        if len(flat) > 5:
            log("      ... and %d more" % (len(flat) - 5))
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

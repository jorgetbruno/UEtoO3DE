"""
m2_acceptance.py — the M2 acceptance test (plan v2.2), editor half.

    "Headless script imports Fixture_01, opens the resulting prefab, asserts
     entity count and each entity's world transform against expected values
     (within 1 cm / 0.1 deg)."

Deliberately a separate editor session from `m2_import.py`: it instantiates the
`.prefab` **from disk** and asserts against it. Asserting inside the importing
session would test the in-memory entities the importer just created, which
proves nothing about the file that actually ships -- and the prefab save path
in 26.05 is a template scan, which is exactly the kind of thing that can
succeed in memory and write something subtly different.

Expected values come from the manifest, which M1's golden file already pins.
The prefab is instantiated at the origin with no parent, so entity world
transforms should equal the manifest's `transform.world` directly.

The artifact-level checks (mirror, dedup, `.assetinfo` contents) live in
`test_m2_artifacts.py`, which needs no editor.

Run:  Tests/o3de/run_o3de_python.bat Tests/m2/m2_acceptance.py
"""

import json
import math as pymath
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm2_acceptance_result.txt')

MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
PREFAB_REL_PATH = "Prefabs/Fixture_01.prefab"

TRANSLATION_TOLERANCE_M = 0.01      # 1 cm, per the plan
ROTATION_TOLERANCE_DEG = 0.1        # 0.1 degrees, per the plan
SCALE_TOLERANCE = 1e-4

NON_UNIFORM_SCALE_EPSILON = 1e-6

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def quaternion_angle_degrees(a, b):
    """Smallest rotation angle between two quaternions, in degrees.

    Both are normalized first. The manifest stores components rounded to 6
    decimals, which leaves the quaternion very slightly non-unit; feeding that
    straight into acos reports a fictitious ~0.15 degrees of error that has
    nothing to do with the imported rotation.
    """
    def normalized(quaternion):
        length = pymath.sqrt(sum(component * component for component in quaternion))
        if length == 0.0:
            raise ValueError("zero-length quaternion")
        return [component / length for component in quaternion]

    a = normalized(a)
    b = normalized(b)
    # q and -q are the same rotation, hence abs().
    dot = abs(sum(x * y for x, y in zip(a, b)))
    dot = max(-1.0, min(1.0, dot))
    return pymath.degrees(2.0 * pymath.acos(dot))


def main():
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    with open(MANIFEST_PATH, 'r') as handle:
        document = json.load(handle)

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = os.path.join(project_root, *PREFAB_REL_PATH.split('/')).replace(os.sep, '/')
    if not check(os.path.exists(prefab_path), 'prefab not found: ' + prefab_path):
        return

    general.idle_enable(True)
    general.open_level_no_prompt('DefaultLevel')
    general.idle_wait_frames(30)

    log('=== instantiating %s ===' % prefab_path)
    import azlmbr.prefab as prefab
    outcome = prefab.PrefabPublicRequestBus(
        bus.Broadcast, 'InstantiatePrefab', prefab_path, entity_module.EntityId(),
        azmath.Vector3(0.0, 0.0, 0.0))
    # GetError() throws on a successful outcome, so it must not be evaluated
    # while building the message -- Python evaluates arguments eagerly.
    if outcome is None or not outcome.IsSuccess():
        reason = 'no outcome returned'
        if outcome is not None:
            try:
                reason = repr(outcome.GetError())
            except Exception:
                reason = 'outcome reported failure with no readable error'
        fail('InstantiatePrefab failed: ' + reason)
        return
    container = outcome.GetValue()
    general.idle_wait_frames(60)  # let instance propagation drain

    # --- collect every entity under the container, by name ---
    def children_of(entity_id):
        found = editor.EditorEntityInfoRequestBus(bus.Event, 'GetChildren', entity_id)
        return list(found) if found else []

    def name_of(entity_id):
        return editor.EditorEntityInfoRequestBus(bus.Event, 'GetName', entity_id)

    by_name = {}
    stack = list(children_of(container))
    while stack:
        entity_id = stack.pop()
        by_name[name_of(entity_id)] = entity_id
        stack.extend(children_of(entity_id))

    expected = {item['name']: item for item in document['entities']}

    # The importer adds one level root at identity that every manifest root
    # hangs off, so the level moves as a unit and the prefab container lands at
    # the origin. It is not a manifest entity.
    level_root_name = document['level']['name']
    level_root = by_name.pop(level_root_name, None)

    log('')
    log('=== 1. entity count ===')
    log('  manifest %d, prefab %d (+ level root %r)'
        % (len(expected), len(by_name), level_root_name))
    check(level_root is not None,
          'prefab has no level root entity named %r' % level_root_name)
    check(len(by_name) == len(expected),
          'prefab has %d entities, manifest has %d (missing: %r, extra: %r)'
          % (len(by_name), len(expected),
             sorted(set(expected) - set(by_name)), sorted(set(by_name) - set(expected))))

    if level_root is not None:
        # If the level root drifts from identity every entity below it is
        # offset by the same constant, which reads as a coordinate bug.
        root_translation = components.TransformBus(
            bus.Event, 'GetWorldTranslation', level_root)
        check(max(abs(v) for v in (root_translation.x, root_translation.y,
                                   root_translation.z)) < TRANSLATION_TOLERANCE_M,
              'level root is not at the origin: (%.5f, %.5f, %.5f)'
              % (root_translation.x, root_translation.y, root_translation.z))

    log('')
    log('=== 2. world transforms (%.0f cm / %.1f deg) ==='
        % (TRANSLATION_TOLERANCE_M * 100, ROTATION_TOLERANCE_DEG))
    for name in sorted(expected):
        entity_id = by_name.get(name)
        if entity_id is None:
            fail('%s is missing from the prefab' % name)
            continue
        want = expected[name]['transform']['world']

        translation = components.TransformBus(bus.Event, 'GetWorldTranslation', entity_id)
        actual_translation = [translation.x, translation.y, translation.z]
        delta = max(abs(actual_translation[i] - want['translation'][i]) for i in range(3))
        if delta > TRANSLATION_TOLERANCE_M:
            fail('%s translation %r != manifest %r (delta %.5f m)'
                 % (name, [round(v, 5) for v in actual_translation],
                    want['translation'], delta))

        rotation = components.TransformBus(bus.Event, 'GetWorldRotationQuaternion', entity_id)
        actual_rotation = [rotation.x, rotation.y, rotation.z, rotation.w]
        angle = quaternion_angle_degrees(actual_rotation, want['rotation'])
        if angle > ROTATION_TOLERANCE_DEG:
            fail('%s rotation is %.4f deg from the manifest value (%r vs %r)'
                 % (name, angle, [round(v, 5) for v in actual_rotation], want['rotation']))

        # Scale: uniform in the transform, or on the non-uniform scale component.
        want_scale = want['scale']
        is_uniform = (abs(want_scale[0] - want_scale[1]) < NON_UNIFORM_SCALE_EPSILON
                      and abs(want_scale[1] - want_scale[2]) < NON_UNIFORM_SCALE_EPSILON)
        if is_uniform:
            actual_scale = components.TransformBus(bus.Event, 'GetWorldUniformScale', entity_id)
            if abs(actual_scale - want_scale[0]) > SCALE_TOLERANCE:
                fail('%s uniform scale %r != manifest %r' % (name, actual_scale, want_scale[0]))
        else:
            vector = entity_module.NonUniformScaleRequestBus(bus.Event, 'GetScale', entity_id)
            actual_scale = [vector.x, vector.y, vector.z] if vector else None
            if actual_scale is None or any(
                    abs(actual_scale[i] - want_scale[i]) > SCALE_TOLERANCE for i in range(3)):
                fail('%s non-uniform scale %r != manifest %r'
                     % (name, actual_scale, want_scale))
            uniform = components.TransformBus(bus.Event, 'GetWorldUniformScale', entity_id)
            if abs(uniform - 1.0) > SCALE_TOLERANCE:
                fail('%s carries both a non-uniform scale and a uniform scale of %r; '
                     'they would multiply' % (name, uniform))
    log('  checked %d entities' % len(expected))

    log('')
    log('=== 3. hierarchy ===')
    for name, item in sorted(expected.items()):
        entity_id = by_name.get(name)
        if entity_id is None:
            continue
        parent_id = editor.EditorEntityInfoRequestBus(bus.Event, 'GetParent', entity_id)
        parent_name = name_of(parent_id) if parent_id and parent_id.IsValid() else None
        if item['parent_id'] is None:
            # Roots hang off the prefab container, which is not a manifest entity.
            check(parent_name not in expected,
                  '%s should be a root but is parented to %r' % (name, parent_name))
        else:
            want_parent = next(e['name'] for e in document['entities']
                               if e['id'] == item['parent_id'])
            check(parent_name == want_parent,
                  '%s is parented to %r, expected %r' % (name, parent_name, want_parent))
    log('  ok')

    log('')
    log('=== 4. mesh components and model assets ===')
    import azlmbr.asset as asset
    from azlmbr.entity import EntityType

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game
    mesh_types = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', ['Mesh'], game_type)
    if not check(mesh_types and not mesh_types[0].IsNull(), 'Mesh component type id missing'):
        return
    mesh_type = mesh_types[0]

    assets_by_guid = {a['guid']: a for a in document['assets']}
    checked = 0
    for name, item in sorted(expected.items()):
        entity_id = by_name.get(name)
        if entity_id is None:
            continue
        count = editor.EditorComponentAPIBus(
            bus.Broadcast, 'CountComponentsOfType', entity_id, mesh_type)
        if 'mesh' not in item:
            check(count == 0, '%s has %d Mesh components but no mesh in the manifest'
                  % (name, count))
            continue
        if not check(count == 1, '%s has %d Mesh components, expected 1' % (name, count)):
            continue

        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, mesh_type).GetValue()
        value = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair,
            'Controller|Configuration|Model Asset')
        asset_id = value.GetValue() if value and value.IsSuccess() else None
        path_back = asset.AssetCatalogRequestBus(
            bus.Broadcast, 'GetAssetPathById', asset_id) if asset_id else ''
        want_relative = assets_by_guid[item['mesh']['asset_guid']]['o3de_relative_path']
        want_product = ('assets/%s.azmodel' % want_relative).lower()
        check(path_back == want_product,
              '%s model asset is %r, expected %r' % (name, path_back, want_product))
        checked += 1
    log('  %d mesh entities carry the expected model asset' % checked)

    log('')
    log('=== 5. the mesh actually loaded (guards against a dangling asset id) ===')
    # A Mesh component pointing at an unprocessed asset resolves to nothing and
    # renders nothing, which is exactly what wait_for_asset exists to prevent.
    # Model Stats is the one readable signal that the model is really there.
    for name in ('SM_LetterF', 'Prim_Box'):
        entity_id = by_name.get(name)
        if entity_id is None:
            continue
        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, mesh_type).GetValue()
        value = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', pair,
            'Model Stats|Mesh Stats|LOD 0|Vert Count')
        vertices = value.GetValue() if value and value.IsSuccess() else None
        log('  %-14s LOD0 vertex count: %r' % (name, vertices))
        check(isinstance(vertices, int) and vertices > 0,
              '%s reports %r vertices; the model did not load' % (name, vertices))


try:
    main()
except Exception:
    fail('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if not failures else 'FAIL'))
if failures:
    log('%d failure(s)' % len(failures))

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if not failures:
    _general.exit_no_prompt()
else:
    os._exit(1)

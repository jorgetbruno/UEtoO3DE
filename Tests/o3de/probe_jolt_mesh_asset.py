"""
probe_jolt_mesh_asset.py — what the asset-based Jolt mesh collider is called,
and which property carries its `.joltmesh`.

The gem moved to the PhysX layout: `Jolt Mesh Collider` now REFERENCES a cooked
`.joltmesh` asset, and the old bake-from-render-mesh component became
`Jolt Baked Mesh Collider`. The importer's adapter resolves components by
display name and sets properties by path, so both names and the asset path have
to be read from the running editor rather than inferred from headers -- the
same rule that caught the PhysX shape enum: a property write the editor accepts
is not proof that the property means what the name suggests.

Prints, for every collider component the adapter might use:
  * whether the display name resolves to a type id at all;
  * the FULL reflected property list of a freshly added instance, so the exact
    asset path (and any asset-scale sibling) is visible rather than guessed;
  * whether an asset id assigned through that path reads back.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_jolt_mesh_asset.py \
         <result> C:/Users/jorge/O3DE/Projects/UEtoO3DETest-Jolt
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
               else os.path.join(SCRIPT_DIR, 'results', 'probe_jolt_mesh_asset_result.txt'))

CANDIDATES = [
    "Jolt Mesh Collider",
    "Jolt Baked Mesh Collider",
    "Jolt Box Collider",
]

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
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', CANDIDATES, game_type)

    log("=== component name resolution ===")
    resolved = {}
    for name, type_id in zip(CANDIDATES, type_ids or []):
        ok = type_id is not None and not type_id.IsNull()
        log("  %-26s %s" % (name, "resolved" if ok else "NOT FOUND"))
        if ok:
            resolved[name] = type_id
    if "Jolt Mesh Collider" not in resolved:
        fail("'Jolt Mesh Collider' does not resolve; the adapter cannot author it")
        return

    for name, type_id in resolved.items():
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, 'Probe_' + name.replace(' ', '_'))
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        if not (outcome and outcome.IsSuccess()):
            fail("could not add %r" % name)
            continue
        pair = outcome.GetValue()[-1]
        listing = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair)
        log("")
        log("=== %s: %d reflected properties ===" % (name, len(listing or [])))
        for path in sorted(listing or []):
            log("    " + str(path))

    # The asset assignment itself, through whichever path the listing showed.
    log("")
    log("=== asset assignment round-trip ===")
    from ueimporter import asset_wait
    product = os.environ.get("UEO3DE_JOLTMESH", "").strip()
    if not product:
        log("  UEO3DE_JOLTMESH not set; skipping (no cooked product to try yet)")
    else:
        asset_id = asset_wait.resolve(product)
        log("  %s -> %s" % (product, "resolved" if asset_id is not None else "NOT IN CATALOG"))
        if asset_id is not None:
            entity_id = editor.ToolsApplicationRequestBus(
                bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
            editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, 'Probe_Assign')
            outcome = editor.EditorComponentAPIBus(
                bus.Broadcast, 'AddComponentsOfType', entity_id,
                [resolved["Jolt Mesh Collider"]])
            pair = outcome.GetValue()[-1]
            for path in ("Shape Configuration|Asset|Jolt Mesh",
                         "Shape Configuration|Jolt Mesh",
                         "Shape Configuration|Asset"):
                try:
                    set_outcome = editor.EditorComponentAPIBus(
                        bus.Broadcast, 'SetComponentProperty', pair, path, asset_id)
                    ok = bool(set_outcome and set_outcome.IsSuccess())
                except Exception as exc:
                    ok = False
                    log("    %-42s raised %s" % (path, str(exc)[:60]))
                    continue
                log("    %-42s %s" % (path, "SET OK" if ok else "rejected"))
                if ok:
                    read = editor.EditorComponentAPIBus(
                        bus.Broadcast, 'GetComponentProperty', pair, path)
                    value = read.GetValue() if read and read.IsSuccess() else None
                    log("      reads back: %r" % (value,))


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

"""
probe_m9_components.py -- M9: Decal + Camera authoring surfaces, measured.

  1. component display names matching decal/camera;
  2. Decal (Atom): property paths; does 'Material' accept a STANDARD PBR
     azmaterial (SetComponentProperty outcome + readback)? Atom decals
     normally want the decal material TYPE -- if a StandardPBR asset is
     accepted silently, v1 must still warn that the look is approximate;
  3. Camera: property paths; set FOV/near/far and read back.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_m9_components.py
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m9_components_result.txt')

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
_handle = open(RESULT_PATH, 'w')
_failures = []


def log(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()
    print(msg)


def main():
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general

    from ueimporter import asset_wait, prefab_build

    log("=== 1. component display names ===")
    from azlmbr.entity import EntityType
    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game
    names = editor.EditorComponentAPIBus(
        bus.Broadcast, 'BuildComponentTypeNameListByEntityType', game_type) or []
    interesting = [n for n in names if any(
        key in n.lower() for key in ("decal", "camera"))]
    log("candidates: %r" % sorted(interesting))

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    def add_and_list(entity_name, component_name):
        entity_id = editor.ToolsApplicationRequestBus(
            bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
        editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, entity_name)
        type_id = prefab_build.resolve_component_type(component_name)
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'AddComponentsOfType', entity_id, [type_id])
        log("add %r: %s" % (component_name,
                            outcome.IsSuccess() if outcome else None))
        pair = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id).GetValue()
        props = editor.EditorComponentAPIBus(
            bus.Broadcast, 'BuildComponentPropertyList', pair) or []
        log("%s properties:" % component_name)
        for p in sorted(props):
            log("  " + str(p))
        return entity_id, pair, props

    log("")
    log("=== 2. Decal component + StandardPBR material acceptance ===")
    decal_id, decal_pair, decal_props = add_and_list("M9_Decal", "Decal")
    material_id = asset_wait.resolve(
        "assets/uetoo3de/game/materials/m_fixture_pbr.azmaterial")
    log("m_fixture_pbr.azmaterial -> %s" % (material_id is not None))
    material_prop = next((p for p in decal_props if "material" in p.lower()), None)
    if material_prop and material_id:
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', decal_pair, material_prop,
            material_id)
        log("set %r with StandardPBR: %s" % (
            material_prop, result.IsSuccess() if result else None))
        readback = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', decal_pair, material_prop)
        log("readback: %s" % (
            readback.GetValue() if readback and readback.IsSuccess() else "FAILED"))
    for key, value in (("sort key", 7), ("opacity", 0.8)):
        path = next((p for p in decal_props if key in p.lower()), None)
        if path is None:
            log("no %r property" % key)
            continue
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', decal_pair, path, value)
        log("set %r = %r: %s" % (path, value,
                                 result.IsSuccess() if result else None))

    log("")
    log("=== 3. Camera component ===")
    camera_id, camera_pair, camera_props = add_and_list("M9_Camera", "Camera")
    for key, value in (("fov", 55.0), ("near", 0.15), ("far", 1500.0)):
        path = next((p for p in camera_props if key in p.lower()), None)
        if path is None:
            log("no %r property" % key)
            continue
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', camera_pair, path, value)
        readback = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', camera_pair, path)
        log("set %r = %r: %s readback %s" % (
            path, value, result.IsSuccess() if result else None,
            readback.GetValue() if readback and readback.IsSuccess() else "?"))

    for entity_id in (decal_id, camera_id):
        editor.ToolsApplicationRequestBus(bus.Broadcast, 'DeleteEntityById', entity_id)


try:
    main()
    log("")
    log("RESULT: PASS")
except Exception:
    log("FATAL: " + traceback.format_exc())
    log("")
    log("RESULT: FAIL")
    _failures.append("fatal")

_handle.close()

import azlmbr.legacy.general as _general
if not _failures:
    _general.exit_no_prompt()
else:
    os._exit(1)

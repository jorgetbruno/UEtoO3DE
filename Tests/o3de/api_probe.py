# One-off probe: find any Python-accessible way to read mesh/model bounds or stats.
import os
import sys
import traceback

RESULT_PATH = (
    sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
    else r"D:/Gamedev/UEtoO3DE/Tests/o3de/results/api_probe_result.txt"
)
lines = []


def log(m):
    lines.append(str(m))
    print(m)


def main():
    import azlmbr
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.asset as asset
    import azlmbr.math as math
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    # 1) modules that might carry model/mesh introspection
    import importlib
    for mod_name in ("render", "atom", "scene", "mesh", "model"):
        try:
            mod = importlib.import_module("azlmbr." + mod_name)
            attrs = [a for a in dir(mod) if not a.startswith("_")]
            log(f"azlmbr.{mod_name}: {attrs}")
        except ImportError as e:
            log(f"azlmbr.{mod_name}: <no module> {e}")

    # 2) EditorComponentAPIBus event names containing Property
    #    (confirm GetComponentProperty exists next to SetComponentProperty)
    # 3) Model Stats value via the Mesh component
    entity_type_instance = EntityType()
    game_entity_type = entity_type_instance.Game() if callable(entity_type_instance.Game) else entity_type_instance.Game
    import azlmbr.entity as entity
    type_ids = editor.EditorComponentAPIBus(bus.Broadcast, "FindComponentTypeIdsByEntityType", ["Mesh"], game_entity_type)
    probe_id = editor.ToolsApplicationRequestBus(bus.Broadcast, "CreateNewEntity", entity.EntityId())
    editor.EditorEntityAPIBus(bus.Event, "SetName", probe_id, "ApiProbe")
    editor.EditorComponentAPIBus(bus.Broadcast, "AddComponentsOfType", probe_id, [type_ids[0]])
    asset_id = asset.AssetCatalogRequestBus(bus.Broadcast, "GetAssetIdByPath",
                                            "assets/uetoo3de/sm_letterf.fbx.azmodel", math.Uuid(), False)
    mesh_pair = editor.EditorComponentAPIBus(bus.Broadcast, "GetComponentOfType", probe_id, type_ids[0]).GetValue()
    editor.EditorComponentAPIBus(bus.Broadcast, "SetComponentProperty", mesh_pair,
                                 "Controller|Configuration|Model Asset", asset_id)
    general.idle_wait_frames(60)

    for path in ("Model Stats", "Model Stats|Mesh Stats"):
        try:
            out = editor.EditorComponentAPIBus(bus.Broadcast, "GetComponentProperty", mesh_pair, path)
            log(f"GetComponentProperty({path!r}) -> success={out.IsSuccess()} value={out.GetValue()!r}")
        except Exception as e:
            log(f"GetComponentProperty({path!r}) raised {e!r}")


ok = True
try:
    main()
except Exception:
    ok = False
    log("EXCEPTION: " + traceback.format_exc())

log("RESULT: " + ("PASS" if ok else "FAIL"))
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as f:
    f.write("\n".join(lines))

import azlmbr.legacy.general as g

if ok:
    g.exit_no_prompt()
else:
    os._exit(1)

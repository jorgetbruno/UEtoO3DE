# Probe #2: unwrap the Mesh component's "Model Stats" property to reach the model AABB.
import os
import sys
import traceback

RESULT_PATH = (
    sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
    else r"D:/Gamedev/UEtoO3DE/Tests/o3de/results/model_stats_probe_result.txt"
)
lines = []


def log(m):
    lines.append(str(m))
    print(m)


def main():
    import azlmbr.legacy.general as general
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    import azlmbr.entity as entity
    import azlmbr.asset as asset
    import azlmbr.math as math
    from azlmbr.entity import EntityType

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)

    et = EntityType()
    game_type = et.Game() if callable(et.Game) else et.Game
    mesh_type = editor.EditorComponentAPIBus(
        bus.Broadcast, "FindComponentTypeIdsByEntityType", ["Mesh"], game_type)[0]
    probe_id = editor.ToolsApplicationRequestBus(bus.Broadcast, "CreateNewEntity", entity.EntityId())
    editor.EditorEntityAPIBus(bus.Event, "SetName", probe_id, "StatsProbe")
    editor.EditorComponentAPIBus(bus.Broadcast, "AddComponentsOfType", probe_id, [mesh_type])
    asset_id = asset.AssetCatalogRequestBus(bus.Broadcast, "GetAssetIdByPath",
                                            "assets/uetoo3de/sm_letterf.fbx.azmodel", math.Uuid(), False)
    mesh_pair = editor.EditorComponentAPIBus(bus.Broadcast, "GetComponentOfType", probe_id, mesh_type).GetValue()
    editor.EditorComponentAPIBus(bus.Broadcast, "SetComponentProperty", mesh_pair,
                                 "Controller|Configuration|Model Asset", asset_id)
    general.idle_wait_frames(120)  # let the model stream in

    out = editor.EditorComponentAPIBus(bus.Broadcast, "GetComponentProperty", mesh_pair, "Model Stats")
    log("GetComponentProperty success: %s" % out.IsSuccess())
    stats = out.GetValue()
    log("stats type: %s" % type(stats))
    try:
        log("stats.typename: %s" % stats.typename())
    except Exception as e:
        log("typename raised: %r" % e)
    for label, obj in (("Model Stats", stats),):
        try:
            log("%s.to_json(): %s" % (label, obj.to_json()))
        except Exception as e:
            log("%s to_json raised: %r" % (label, e))
        try:
            log("%s invoke('GetAabb'): %r" % (label, obj.invoke("GetAabb")))
        except Exception as e:
            log("%s invoke GetAabb raised: %r" % (label, e))
    # LOD 0 proxy too (per-LOD stats may carry the AABB)
    try:
        lod0 = editor.EditorComponentAPIBus(
            bus.Broadcast, "GetComponentProperty", mesh_pair, "Model Stats|Mesh Stats|LOD 0").GetValue()
        log("LOD 0.typename: %s" % lod0.typename())
        log("LOD 0.to_json(): %s" % lod0.to_json())
    except Exception as e:
        log("LOD 0 raised: %r" % e)

    # and the full property tree under Model Stats, for sub-path property reads
    prop_paths = editor.EditorComponentAPIBus(bus.Broadcast, "BuildComponentPropertyList", mesh_pair)
    stats_paths = [p for p in prop_paths if "Stat" in p or "Aabb" in p or "Bound" in p]
    log("stats-ish property paths: %s" % stats_paths)
    for p in stats_paths:
        try:
            o = editor.EditorComponentAPIBus(bus.Broadcast, "GetComponentProperty", mesh_pair, p)
            log("  GetComponentProperty(%r) -> %r" % (p, o.GetValue()))
        except Exception as e:
            log("  GetComponentProperty(%r) raised %r" % (p, e))


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

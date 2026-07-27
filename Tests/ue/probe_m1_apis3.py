"""
probe_m1_apis3.py — M1 API reconnaissance, round 3: World Partition detection only.

Round 2 ruled out the easy routes: no asset-registry tag, no ULevel.world_partition,
no UWorld.persistent_level. Two candidates remain, both probed here against the
known-NON-partitioned Fixture_01 so the negative case is measured, not assumed:

  1. UWorld.get_world_settings() -> AWorldSettings, which owns the UWorldPartition
     subobject in UE5.
  2. WorldPartitionBlueprintLibrary.get_actor_descs(), which should be empty or
     raise on a non-partitioned world.

The M1 guard must be conservative: it aborts when WP is detected OR when detection
itself fails, because a silently near-empty actor list is the failure this exists
to prevent (plan, Known Hard Spot 8).

Run:  run_ue_python.bat probe_m1_apis3.py
Output: Tests/ue/results/probe_m1_apis3.txt
"""

import os
import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results"
OUT_PATH = OUT_DIR + "/probe_m1_apis3.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M1C] " + str(msg))


def try_prop(obj, name):
    try:
        return repr(obj.get_editor_property(name))
    except Exception as exc:
        return "<ERR " + str(exc)[:130] + ">"


def try_call(obj, name, *args):
    fn = getattr(obj, name, None)
    if fn is None:
        return "<no attr>"
    try:
        return repr(fn(*args))
    except Exception as exc:
        return "<ERR " + type(exc).__name__ + ": " + str(exc)[:130] + ">"


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()

    out("=== 1. WorldSettings route ===")
    ws = world.get_world_settings()
    out("world.get_world_settings() = " + repr(ws))
    out("  class: " + ws.get_class().get_name())
    out("  members matching 'partition': "
        + str([n for n in dir(ws) if "partition" in n.lower()]))
    out("  members matching 'world': "
        + str([n for n in dir(ws) if "world" in n.lower()]))
    for name in ("world_partition", "enable_world_partition", "default_world_partition_settings",
                 "is_partitioned_world", "world_partition_settings"):
        out("  ws." + name + " = " + try_prop(ws, name))
    out("  ws.export_text() (truncated 4000):")
    text = try_call(ws, "export_text")
    out("    " + text[:4000])

    out()
    out("=== 2. WorldPartitionBlueprintLibrary route (non-WP level = negative case) ===")
    lib = unreal.WorldPartitionBlueprintLibrary
    out("  get_actor_descs(): " + try_call(lib, "get_actor_descs"))
    out("  get_editor_world_bounds(): " + try_call(lib, "get_editor_world_bounds"))
    out("  get_runtime_world_bounds(): " + try_call(lib, "get_runtime_world_bounds"))

    out()
    out("=== 3. ULevel route (recap + neighbours) ===")
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_sub.get_all_level_actors()
    level = actors[0].get_level()
    out("  level.use_external_actors = " + try_prop(level, "use_external_actors"))
    out("  level members (non-dunder): " + str([n for n in dir(level) if not n.startswith("_")]))

    out()
    out("=== 4. Actor count sanity (what the guard protects) ===")
    out("  get_all_level_actors() count = " + str(len(actors)))


status = "PASS"
try:
    main()
except Exception:
    out("FATAL:")
    out(traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT_PATH, "w") as f:
    f.write("\n".join(_lines) + "\n")

print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)

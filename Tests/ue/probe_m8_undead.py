"""
probe_m8_undead.py — M8: how the UndeadPack showcase maps place skeletal
content, measured in place.

Runs against EasternProvince / Showcase_Ghoul (and Showcase_EnemyGoblin as a
cross-check). Questions:

  1. actor census: every actor's class; which carry SkeletalMeshComponents
     (SkeletalMeshActor vs Blueprint), labels, transforms (scale!);
  2. per skeletal component: mesh asset path, animation_mode, anim_to_play,
     anim_class (an AnimBlueprint has no v1 mapping), bone count, material
     slots, physics flags;
  3. the pack's AnimSequences: enable_root_motion / length for the warning
     design (ANIM_ROOT_MOTION_DROPPED).

Output: Tests/ue/results/probe_m8_undead.txt
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m8_undead.txt"
MAPS = ("/Game/UndeadPack/Ghoul/Maps/Showcase_Ghoul",
        "/Game/UndeadPack/EnemyGoblin/Map/Showcase_EnemyGoblin")
ANIM_DIRS = ("/Game/UndeadPack/Ghoul/Animations",)

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def _try_get(obj, name):
    try:
        return repr(obj.get_editor_property(name))
    except Exception as exc:
        return "<unreadable: %s>" % str(exc)[:60]


def _vec(v):
    return "(%.1f, %.1f, %.1f)" % (v.x, v.y, v.z)


def probe_map(map_path):
    out("")
    out("=== map: %s ===" % map_path)
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(map_path):
        out("FAILED TO LOAD")
        return

    actors = actor_sub.get_all_level_actors() or []
    by_class = {}
    skeletal_actors = []
    for actor in actors:
        cls = actor.get_class().get_name()
        by_class[cls] = by_class.get(cls, 0) + 1
        components = actor.get_components_by_class(unreal.SkeletalMeshComponent) or []
        if components:
            skeletal_actors.append((actor, list(components)))
    out("actors: %d; by class: %s" % (len(actors), sorted(by_class.items())))
    out("actors with SkeletalMeshComponents: %d" % len(skeletal_actors))

    for actor, components in skeletal_actors:
        transform = actor.get_actor_transform()
        out("")
        out("  actor %r class=%s" % (actor.get_actor_label(),
                                     actor.get_class().get_name()))
        out("    loc=%s rot=%s scale=%s" % (
            _vec(transform.translation),
            repr(transform.rotation.rotator()),
            _vec(transform.scale3d)))
        for component in components:
            out("    component %s:" % component.get_name())
            mesh = None
            try:
                mesh = component.get_editor_property("skeletal_mesh_asset")
            except Exception:
                try:
                    mesh = component.get_skinned_asset()
                except Exception:
                    pass
            out("      mesh: %s" % (mesh.get_path_name() if mesh else None))
            out("      animation_mode: %s" % _try_get(component, "animation_mode"))
            out("      anim_to_play:   %s" % _try_get(component, "anim_to_play"))
            out("      anim_class:     %s" % _try_get(component, "anim_class"))
            try:
                out("      num bones: %d" % component.get_num_bones())
            except Exception as exc:
                out("      num bones failed: %s" % str(exc)[:80])
            try:
                world = component.get_world_transform()
                out("      component world loc=%s scale=%s" % (
                    _vec(world.translation), _vec(world.scale3d)))
            except Exception:
                pass
            if mesh is not None:
                try:
                    materials = mesh.get_editor_property("materials") or []
                    out("      material slots: %s" % [
                        (str(s.get_editor_property("material_slot_name")),
                         s.get_editor_property("material_interface").get_name()
                         if s.get_editor_property("material_interface") else None)
                        for s in materials])
                except Exception as exc:
                    out("      materials unreadable: %s" % str(exc)[:80])
            body = None
            try:
                body = component.get_editor_property("body_instance")
                out("      collision_enabled: %s; simulate: %s" % (
                    body.get_editor_property("collision_enabled"),
                    body.get_editor_property("simulate_physics")))
            except Exception:
                pass


def probe_anims():
    out("")
    out("=== UndeadPack Ghoul AnimSequences ===")
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for anim_dir in ANIM_DIRS:
        for data in registry.get_assets_by_path(anim_dir, recursive=True) or []:
            cls = str(data.asset_class_path.asset_name)
            if cls != "AnimSequence":
                continue
            seq = data.get_asset()
            out("  %s: root_motion=%s len=%s" % (
                seq.get_name(),
                _try_get(seq, "enable_root_motion"),
                _try_get(seq, "sequence_length")))


def main():
    for map_path in MAPS:
        probe_map(map_path)
    probe_anims()


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)

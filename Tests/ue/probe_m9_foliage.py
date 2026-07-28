"""
probe_m9_foliage.py -- M9: InstancedFoliageActor anatomy, measured in place.

Runs against EasternProvince / Showcase_Ghoul (the level with a real
InstancedFoliageActor). Questions:

  1. component classes on the actor (FoliageInstancedStaticMeshComponent?);
  2. per component: mesh asset, instance COUNT, per-instance transforms
     (get_instance_transform local vs world), materials, mobility, collision;
  3. scale of the problem: total instances across the level -- the plan wants
     the entity-count ceiling documented.

Output: Tests/ue/results/probe_m9_foliage.txt (incremental)
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m9_foliage.txt"
MAP_PATH = "/Game/UndeadPack/Ghoul/Maps/Showcase_Ghoul"

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
_handle = open(OUT_PATH, "w")


def out(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()


def _vec(v):
    return "(%.1f, %.1f, %.1f)" % (v.x, v.y, v.z)


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    foliage_actors = [a for a in actor_sub.get_all_level_actors()
                      if a.get_class().get_name() == "InstancedFoliageActor"]
    out("InstancedFoliageActors: %d" % len(foliage_actors))
    total_instances = 0
    for actor in foliage_actors:
        out("")
        out("actor %r at %s" % (actor.get_actor_label(),
                                _vec(actor.get_actor_location())))
        components = actor.get_components_by_class(
            unreal.InstancedStaticMeshComponent) or []
        out("  ISM-family components: %d" % len(components))
        for component in components:
            cls = component.get_class().get_name()
            mesh = component.get_editor_property("static_mesh")
            count = component.get_instance_count()
            total_instances += count
            out("  component %s (%s):" % (component.get_name(), cls))
            out("    mesh: %s" % (mesh.get_path_name() if mesh else None))
            out("    instances: %d" % count)
            out("    mobility: %s" % component.get_editor_property("mobility"))
            try:
                body = component.get_editor_property("body_instance")
                out("    collision_enabled: %s"
                    % body.get_editor_property("collision_enabled"))
            except Exception as exc:
                out("    body unreadable: %s" % str(exc)[:60])
            for index in range(min(3, count)):
                result = component.get_instance_transform(index, True)
                transform = result[1] if isinstance(result, tuple) else result
                ok = result[0] if isinstance(result, tuple) else True
                out("    instance %d (world): ok=%s loc=%s rot=%s scale=%s" % (
                    index, ok, _vec(transform.translation),
                    transform.rotation.rotator(), _vec(transform.scale3d)))
                local = component.get_instance_transform(index, False)
                transform_l = local[1] if isinstance(local, tuple) else local
                out("    instance %d (local):  loc=%s" % (
                    index, _vec(transform_l.translation)))
            try:
                materials = [component.get_material(i)
                             for i in range(component.get_num_materials())]
                out("    materials: %s" % [m.get_name() if m else None
                                           for m in materials])
            except Exception as exc:
                out("    materials unreadable: %s" % str(exc)[:60])
    out("")
    out("TOTAL instances in level: %d" % total_instances)


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"

out("RESULT: " + status)
_handle.close()
print("RESULT: " + status)
try:
    unreal.SystemLibrary.quit_editor()
except Exception:
    pass
if status != "PASS":
    raise SystemExit(1)

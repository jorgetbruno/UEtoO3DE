"""
probe_m1_apis.py — M1 API reconnaissance against UE 5.8 + Fixture_01.

The plan's rule is "verify, never assume", so this dumps every API surface the
manifest exporter depends on BEFORE the exporter is written:

  1. engine version
  2. World Partition detection (which property/API actually answers it)
  3. actor iteration + attachment hierarchy + root component
  4. StaticMeshComponent: mobility, body_instance (simulate/mass/damping/gravity/CCD/profile)
  5. StaticMesh: body_setup.agg_geom element arrays (the M3 collider source)
  6. TriggerBox: collision component + extent
  7. Lights: intensity, intensity_units enum, color, attenuation, cone angles
  8. Asset identity: package name / path name / asset registry fields usable as a GUID source

Every section is independently guarded — one failure must not cost the whole run.

Run:  run_ue_python.bat probe_m1_apis.py
Output: Tests/ue/results/probe_m1_apis.txt
"""

import os
import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results"
OUT_PATH = OUT_DIR + "/probe_m1_apis.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M1] " + str(msg))


def section(title):
    out()
    out("=" * 70)
    out(title)
    out("=" * 70)


def guarded(title, fn):
    section(title)
    try:
        fn()
    except Exception:
        out("EXCEPTION:")
        out(traceback.format_exc())


def attrs(obj, needle=None):
    names = sorted(dir(obj))
    if needle:
        low = needle.lower()
        names = [n for n in names if low in n.lower()]
    return names


def try_prop(obj, name):
    """get_editor_property that reports failure instead of raising."""
    try:
        return repr(obj.get_editor_property(name))
    except Exception as exc:
        return "<ERR " + type(exc).__name__ + ": " + str(exc)[:120] + ">"


# ---------------------------------------------------------------------------

def probe_engine():
    out("engine version: " + unreal.SystemLibrary.get_engine_version())


def probe_world_partition():
    ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = ues.get_editor_world()
    out("editor world: " + repr(world))
    out("world class: " + world.get_class().get_name())
    out("world attrs matching 'partition': " + str(attrs(world, "partition")))
    out("world attrs matching 'level': " + str(attrs(world, "level")))
    for name in ("world_partition", "is_partitioned_world", "persistent_level"):
        out("world.get_editor_property('" + name + "') = " + try_prop(world, name))

    try:
        level = world.get_editor_property("persistent_level")
        out("persistent level: " + repr(level))
        out("level attrs matching 'partition': " + str(attrs(level, "partition")))
        for name in ("world_partition", "is_using_external_actors", "use_external_actors"):
            out("level.get_editor_property('" + name + "') = " + try_prop(level, name))
    except Exception as exc:
        out("persistent_level unavailable: " + repr(exc))

    out("unreal module names matching 'WorldPartition': "
        + str([n for n in dir(unreal) if "WorldPartition" in n]))

    # AssetRegistry tags on the .umap are another (engine-version-stable) signal.
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        data = registry.get_asset_by_object_path(MAP_PATH + "." + MAP_PATH.rsplit("/", 1)[-1])
        out("map AssetData: " + repr(data))
        out("map AssetData attrs: " + str(attrs(data)))
        try:
            tags = data.get_editor_property("tags_and_values")
            out("map tags_and_values: " + repr(tags))
        except Exception as exc:
            out("tags_and_values unavailable: " + repr(exc))
    except Exception as exc:
        out("asset registry map lookup failed: " + repr(exc))


def probe_actors():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_sub.get_all_level_actors()
    out("actor count: " + str(len(actors)))
    for actor in actors:
        label = actor.get_actor_label()
        cls = actor.get_class().get_name()
        root = actor.root_component
        root_cls = root.get_class().get_name() if root else "<none>"
        parent = actor.get_attach_parent_actor()
        parent_label = parent.get_actor_label() if parent else ""
        out("  %-22s class=%-22s root=%-24s parent=%s"
            % (label, cls, root_cls, parent_label))

    out()
    out("attachment API check on RotatedChild_Sphere:")
    for actor in actors:
        if actor.get_actor_label() != "RotatedChild_Sphere":
            continue
        root = actor.root_component
        out("  relative_location  = " + repr(root.get_editor_property("relative_location")))
        out("  relative_rotation  = " + repr(root.get_editor_property("relative_rotation")))
        out("  relative_scale3d   = " + repr(root.get_editor_property("relative_scale3d")))
        out("  world location     = " + repr(actor.get_actor_location()))
        out("  world rotation     = " + repr(actor.get_actor_rotation()))
        out("  world quat         = " + repr(actor.get_actor_rotation().quaternion()))
        out("  world scale3d      = " + repr(actor.get_actor_scale3d()))
        out("  get_actor_transform= " + repr(actor.get_actor_transform()))
        out("  socket name        = " + repr(root.get_attach_socket_name()))


def probe_static_mesh_component():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    target = None
    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() == "Cube_Dynamic":
            target = actor
            break
    if target is None:
        out("Cube_Dynamic not found")
        return
    smc = target.static_mesh_component
    out("component class: " + smc.get_class().get_name())
    out("attrs matching 'mobility': " + str(attrs(smc, "mobility")))
    out("mobility property: " + try_prop(smc, "mobility"))
    out("attrs matching 'body': " + str(attrs(smc, "body")))

    body = smc.get_editor_property("body_instance")
    out("body_instance: " + repr(body))
    out("body_instance type: " + type(body).__name__)
    out("body_instance attrs: " + str(attrs(body)))
    for name in ("simulate_physics", "b_simulate_physics", "mass_in_kg", "override_mass",
                 "b_override_mass", "linear_damping", "angular_damping", "enable_gravity",
                 "b_enable_gravity", "use_ccd", "b_use_ccd", "collision_profile_name",
                 "collision_enabled", "collision_response_template", "mass_scale",
                 "generate_overlap_events", "b_generate_overlap_events"):
        out("  body." + name + " = " + try_prop(body, name))

    out("smc.get_collision_profile_name(): "
        + repr(getattr(smc, "get_collision_profile_name", lambda: "<no method>")()))
    for name in ("generate_overlap_events", "b_generate_overlap_events",
                 "collision_enabled", "cast_shadow", "visible"):
        out("  smc." + name + " = " + try_prop(smc, name))

    out("smc.is_simulating_physics(): "
        + repr(getattr(smc, "is_simulating_physics", lambda: "<no method>")()))
    out("smc.get_mass(): " + repr(getattr(smc, "get_mass", lambda: "<no method>")()))


def probe_body_setup():
    """UE simple collision lives on the mesh asset, not the actor (plan M3)."""
    for asset_path in ("/Engine/BasicShapes/Cube.Cube",
                       "/Engine/BasicShapes/Sphere.Sphere",
                       "/Engine/BasicShapes/Cylinder.Cylinder",
                       "/Engine/BasicShapes/Plane.Plane",
                       "/Game/Meshes/SM_LetterF.SM_LetterF"):
        out()
        out("--- " + asset_path)
        mesh = unreal.EditorAssetLibrary.load_asset(asset_path)
        if mesh is None:
            out("  <not loadable>")
            continue
        out("  mesh attrs matching 'body': " + str(attrs(mesh, "body")))
        body_setup = mesh.get_editor_property("body_setup")
        out("  body_setup: " + repr(body_setup))
        if body_setup is None:
            continue
        out("  body_setup attrs: " + str(attrs(body_setup)))
        for name in ("collision_trace_flag", "default_instance", "physics_type"):
            out("    body_setup." + name + " = " + try_prop(body_setup, name))
        agg = body_setup.get_editor_property("agg_geom")
        out("  agg_geom: " + repr(agg))
        out("  agg_geom attrs: " + str(attrs(agg)))
        for name in ("box_elems", "sphere_elems", "sphyl_elems", "convex_elems",
                     "tapered_capsule_elems", "level_set_elems"):
            value = None
            try:
                value = agg.get_editor_property(name)
                out("    " + name + ": count=" + str(len(value)))
                for elem in value:
                    out("      elem: " + repr(elem))
                    out("      elem attrs: " + str(attrs(elem)))
                    break
            except Exception as exc:
                out("    " + name + ": <ERR " + str(exc)[:100] + ">")


def probe_trigger():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() != "TriggerBox_01":
            continue
        out("class: " + actor.get_class().get_name())
        out("attrs matching 'collision': " + str(attrs(actor, "collision")))
        comp = actor.get_component_by_class(unreal.PrimitiveComponent)
        out("primitive component: " + repr(comp))
        if comp is None:
            return
        out("component class: " + comp.get_class().get_name())
        for name in ("box_extent", "generate_overlap_events", "collision_profile_name",
                     "collision_enabled"):
            out("  " + name + " = " + try_prop(comp, name))
        for meth in ("get_unscaled_box_extent", "get_scaled_box_extent",
                     "get_collision_profile_name", "get_collision_enabled"):
            fn = getattr(comp, meth, None)
            out("  " + meth + "(): " + (repr(fn()) if fn else "<no method>"))
        body = comp.get_editor_property("body_instance")
        out("  body_instance.collision_profile_name = " + try_prop(body, "collision_profile_name"))
        out("  body_instance.collision_enabled = " + try_prop(body, "collision_enabled"))


def probe_lights():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    wanted = {"Light_Point", "Light_Spot", "Light_Directional", "Atmo_SkyLight"}
    for actor in actor_sub.get_all_level_actors():
        label = actor.get_actor_label()
        if label not in wanted:
            continue
        out()
        out("--- " + label + " (" + actor.get_class().get_name() + ")")
        comp = actor.get_component_by_class(unreal.LightComponentBase)
        out("  component: " + (comp.get_class().get_name() if comp else "<none>"))
        if comp is None:
            continue
        for name in ("intensity", "light_color", "intensity_units", "attenuation_radius",
                     "inner_cone_angle", "outer_cone_angle", "source_radius",
                     "cast_shadows", "temperature", "use_temperature", "affects_world"):
            out("  " + name + " = " + try_prop(comp, name))
        try:
            units = comp.get_editor_property("intensity_units")
            out("  intensity_units type: " + type(units).__name__)
            out("  LightUnits enum values: "
                + str([n for n in dir(unreal.LightUnits) if not n.startswith("_")]))
        except Exception as exc:
            out("  intensity_units unavailable: " + repr(exc))


def probe_asset_identity():
    mesh = unreal.EditorAssetLibrary.load_asset("/Game/Meshes/SM_LetterF.SM_LetterF")
    out("get_path_name: " + unreal.SystemLibrary.get_path_name(mesh))
    out("outer package: " + repr(mesh.get_outer()))
    out("package name: " + mesh.get_outer().get_name())
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    data = registry.get_asset_by_object_path("/Game/Meshes/SM_LetterF.SM_LetterF")
    out("AssetData: " + repr(data))
    out("AssetData attrs: " + str(attrs(data)))
    for name in ("package_name", "package_path", "asset_name", "asset_class_path",
                 "object_path", "package_guid"):
        out("  " + name + " = " + try_prop(data, name))

    out()
    out("material slot / material reference readback on Prim_Box:")
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in actor_sub.get_all_level_actors():
        if actor.get_actor_label() != "Prim_Box":
            continue
        smc = actor.static_mesh_component
        out("  static_mesh: " + repr(smc.get_editor_property("static_mesh")))
        out("  num materials: " + str(smc.get_num_materials()))
        for i in range(smc.get_num_materials()):
            mat = smc.get_material(i)
            out("    slot %d -> %s" % (i, unreal.SystemLibrary.get_path_name(mat) if mat else "<none>"))
        out("  override_materials = " + try_prop(smc, "override_materials"))
        mesh_asset = smc.get_editor_property("static_mesh")
        try:
            slots = mesh_asset.get_editor_property("static_materials")
            out("  asset static_materials count: " + str(len(slots)))
            for slot in slots:
                out("    slot: " + repr(slot) + " attrs=" + str(attrs(slot)))
                break
        except Exception as exc:
            out("  static_materials unavailable: " + repr(exc))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    guarded("1. ENGINE", probe_engine)
    guarded("2. WORLD PARTITION DETECTION", probe_world_partition)
    guarded("3. ACTORS + HIERARCHY", probe_actors)
    guarded("4. STATIC MESH COMPONENT / BODY INSTANCE", probe_static_mesh_component)
    guarded("5. BODY SETUP / AGG GEOM", probe_body_setup)
    guarded("6. TRIGGER BOX", probe_trigger)
    guarded("7. LIGHTS", probe_lights)
    guarded("8. ASSET IDENTITY + MATERIALS", probe_asset_identity)


status = "PASS"
try:
    main()
except Exception:
    out("FATAL:")
    out(traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
try:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        f.write("\n".join(_lines) + "\n")
    unreal.log("[PROBE_M1] wrote " + OUT_PATH)
except Exception:
    unreal.log_error("[PROBE_M1] failed to write " + OUT_PATH)
    unreal.log_error(traceback.format_exc())
    status = "FAIL"

print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)

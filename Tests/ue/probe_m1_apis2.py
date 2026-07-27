"""
probe_m1_apis2.py — M1 API reconnaissance, round 2.

Round 1 left exactly two questions open:

  A. World Partition detection. UWorld exposes neither 'world_partition' nor
     'persistent_level' to Python in 5.8, so the M1 guard needs another route.
     Candidates tried here: asset-registry tags on the .umap, the
     WorldPartitionBlueprintLibrary surface, and the WorldPartition subsystem.
  B. Collision element field access. KBoxElem/KSphereElem/KConvexElem print as
     '{}' and expose no field attributes in dir(), but round 1 proved
     get_editor_property() still resolves UPROPERTYs that dir() omits
     (BodyInstance.override_mass). Probe to_dict() and named properties.

Run:  run_ue_python.bat probe_m1_apis2.py
Output: Tests/ue/results/probe_m1_apis2.txt
"""

import os
import traceback

import unreal

MAP_PATH = "/Game/Maps/Fixture_01"
OUT_DIR = "D:/Gamedev/UEtoO3DE/Tests/ue/results"
OUT_PATH = OUT_DIR + "/probe_m1_apis2.txt"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_M1B] " + str(msg))


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


def try_prop(obj, name):
    try:
        return repr(obj.get_editor_property(name))
    except Exception as exc:
        return "<ERR " + str(exc)[:110] + ">"


def try_call(obj, name, *args):
    fn = getattr(obj, name, None)
    if fn is None:
        return "<no attr>"
    try:
        return repr(fn(*args))
    except Exception as exc:
        return "<ERR " + type(exc).__name__ + ": " + str(exc)[:110] + ">"


# ---------------------------------------------------------------------------
# A. World Partition detection
# ---------------------------------------------------------------------------

def probe_wp_tags():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for map_path in (MAP_PATH,):
        data = registry.get_asset_by_object_path(map_path + "." + map_path.rsplit("/", 1)[-1])
        out("AssetData for " + map_path + ": valid=" + str(data.is_valid()))
        for tag in ("LevelIsPartitioned", "IsPartitioned", "WorldPartition",
                    "LevelIsUsingExternalActors", "LevelHasExternalActors",
                    "MainWorldPartition", "PackedLevelActor", "WorldPartitionRuntimeHash"):
            out("  get_tag_value('" + tag + "') = " + try_call(data, "get_tag_value", tag))


def probe_wp_library():
    lib = getattr(unreal, "WorldPartitionBlueprintLibrary", None)
    out("WorldPartitionBlueprintLibrary: " + repr(lib))
    if lib is not None:
        out("  members: " + str([n for n in dir(lib) if not n.startswith("_")]))
    sub = getattr(unreal, "WorldPartitionSubsystem", None)
    out("WorldPartitionSubsystem: " + repr(sub))
    if sub is not None:
        out("  members: " + str([n for n in dir(sub) if not n.startswith("_")]))
    out("get_editor_subsystem(WorldPartitionSubsystem): "
        + try_call(unreal, "get_editor_subsystem", sub) if sub else "<none>")

    # UWorld -> ULevel route: try the Actor's level, which Python does expose.
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_sub.get_all_level_actors()
    if actors:
        actor = actors[0]
        out("actor.get_level(): " + try_call(actor, "get_level"))
        level = getattr(actor, "get_level", lambda: None)()
        if level is not None:
            out("  level class: " + level.get_class().get_name())
            out("  level members matching 'partition': "
                + str([n for n in dir(level) if "partition" in n.lower()]))
            for name in ("world_partition", "is_using_external_objects",
                         "use_external_actors", "world_settings"):
                out("  level." + name + " = " + try_prop(level, name))

    # WorldSettings is reachable and in UE5 carries the partition-related setup.
    ws = try_call(unreal.GameplayStatics, "get_world_settings",
                  unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world())
    out("GameplayStatics.get_world_settings(): " + ws)

    out("unreal names matching 'ExternalActor': "
        + str([n for n in dir(unreal) if "ExternalActor" in n]))


def probe_wp_editor_world_props():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    out("world members (non-dunder): " + str([n for n in dir(world) if not n.startswith("_")]))


# ---------------------------------------------------------------------------
# B. Collision element fields
# ---------------------------------------------------------------------------

BOX_FIELDS = ("center", "rotation", "x", "y", "z", "name", "rest_offset", "contributes_to_mass")
SPHERE_FIELDS = ("center", "radius", "name", "rest_offset", "contributes_to_mass")
SPHYL_FIELDS = ("center", "rotation", "radius", "length", "name")
CONVEX_FIELDS = ("vertex_data", "index_data", "element_box", "transform", "name")


def dump_elem(label, elem, fields):
    out("  " + label + " repr: " + repr(elem))
    out("  " + label + " to_dict(): " + try_call(elem, "to_dict"))
    out("  " + label + " export_text(): " + try_call(elem, "export_text"))
    for name in fields:
        out("    ." + name + " = " + try_prop(elem, name))


def probe_collision_elems():
    for asset_path, kinds in (
            ("/Engine/BasicShapes/Cube.Cube", (("box_elems", BOX_FIELDS),)),
            ("/Engine/BasicShapes/Plane.Plane", (("box_elems", BOX_FIELDS),)),
            ("/Engine/BasicShapes/Sphere.Sphere", (("sphere_elems", SPHERE_FIELDS),)),
            ("/Engine/BasicShapes/Cylinder.Cylinder", (("convex_elems", CONVEX_FIELDS),)),
    ):
        out()
        out("--- " + asset_path)
        mesh = unreal.EditorAssetLibrary.load_asset(asset_path)
        agg = mesh.get_editor_property("body_setup").get_editor_property("agg_geom")
        out("  agg_geom to_dict(): " + try_call(agg, "to_dict"))
        for prop_name, fields in kinds:
            elems = agg.get_editor_property(prop_name)
            out("  " + prop_name + " count=" + str(len(elems)))
            for i, elem in enumerate(elems):
                dump_elem(prop_name + "[" + str(i) + "]", elem, fields)


def probe_bounds():
    """Bounds are the fallback collider source when agg_geom is empty (SM_LetterF)."""
    for asset_path in ("/Engine/BasicShapes/Cube.Cube", "/Game/Meshes/SM_LetterF.SM_LetterF"):
        mesh = unreal.EditorAssetLibrary.load_asset(asset_path)
        out()
        out("--- " + asset_path)
        out("  get_bounds(): " + try_call(mesh, "get_bounds"))
        out("  get_bounding_box(): " + try_call(mesh, "get_bounding_box"))
        out("  extended_bounds = " + try_prop(mesh, "extended_bounds"))
        out("  num_lods: " + try_call(mesh, "get_num_lods"))
        out("  num_sections(0): " + try_call(mesh, "get_num_sections", 0))


def probe_slot_names():
    for asset_path in ("/Engine/BasicShapes/Cube.Cube",
                       "/Engine/BasicShapes/Sphere.Sphere",
                       "/Game/Meshes/SM_LetterF.SM_LetterF"):
        mesh = unreal.EditorAssetLibrary.load_asset(asset_path)
        slots = mesh.get_editor_property("static_materials")
        names = []
        for slot in slots:
            names.append(str(slot.get_editor_property("material_slot_name")))
        out(asset_path + " slot names: " + str(names))


def probe_ppv_and_env():
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in actor_sub.get_all_level_actors():
        label = actor.get_actor_label()
        if label not in ("PPV_01", "Atmo_HeightFog", "Atmo_SkyLight", "TriggerBox_01"):
            continue
        out()
        out("--- " + label + " (" + actor.get_class().get_name() + ")")
        out("  actor transform: " + repr(actor.get_actor_transform()))
        out("  actor folder path: " + try_call(actor, "get_folder_path"))
        out("  actor tags = " + try_prop(actor, "tags"))
        out("  get_name(): " + actor.get_name())
        out("  get_path_name(): " + actor.get_path_name())


def main():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    guarded("A1. WORLD PARTITION — asset registry tags", probe_wp_tags)
    guarded("A2. WORLD PARTITION — library / subsystem / level", probe_wp_library)
    guarded("A3. WORLD — full member list", probe_wp_editor_world_props)
    guarded("B1. COLLISION ELEMENT FIELDS", probe_collision_elems)
    guarded("B2. MESH BOUNDS (fallback collider source)", probe_bounds)
    guarded("B3. MATERIAL SLOT NAMES", probe_slot_names)
    guarded("B4. ENVIRONMENT / TRIGGER ACTOR IDENTITY", probe_ppv_and_env)


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
unreal.log("[PROBE_M1B] wrote " + OUT_PATH)

print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)

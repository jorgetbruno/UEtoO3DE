# Probe: is there any PHYSICAL MATERIAL data in the source content to export?
#
# The Jolt gem now resolves a material slot index per triangle at contact time,
# and the importer writes no PhysicsMaterialSlots at all -- which reads like an
# importer gap. It is not: the exporter has no notion of physical materials
# either (no reference to PhysMaterial, friction or restitution anywhere), and
# the manifest carries only render material names. So the feature starts in UE,
# and its cost depends entirely on a question nobody has asked yet:
#
#   do the source materials actually CARRY physical materials, or would the
#   whole chain be plumbing for data that does not exist upstream?
#
# This reports, per static mesh: each material slot, its material, whether that
# material has a UPhysicalMaterial assigned, and the properties that would have
# to travel (friction, restitution, density, and the combine modes, which have
# no O3DE equivalent and would be a divergence).
#
# Asserts nothing. Run: Tests/ue/run_ue_python.bat Tests/ue/probe_phys_materials.py
import unreal

OUT = r"D:/Gamedev/UEtoO3DE/Tests/ue/results/phys_materials_probe.txt"

lines = []


def log(message):
    lines.append(str(message))
    unreal.log(str(message))


def describe(phys):
    if phys is None:
        return None
    fields = {}
    for name in ("friction", "restitution", "density",
                 "friction_combine_mode", "restitution_combine_mode",
                 "override_friction_combine_mode",
                 "override_restitution_combine_mode"):
        try:
            fields[name] = phys.get_editor_property(name)
        except Exception as error:  # noqa: BLE001 - report what is missing
            fields[name] = "<%s>" % type(error).__name__
    return fields


registry = unreal.AssetRegistryHelpers.get_asset_registry()
registry.wait_for_completion()


def assets_of(class_name):
    """UE 5.8 takes a TopLevelAssetPath here, not a class-name string."""
    path = unreal.TopLevelAssetPath("/Script/Engine", class_name)
    return registry.get_assets_by_class(path, search_sub_classes=True)


meshes = assets_of("StaticMesh")
log("static meshes in the project: %d" % len(meshes))

# Every physical material asset that exists at all -- if this is zero, the
# question is answered before looking at a single mesh.
phys_assets = assets_of("PhysicalMaterial")
log("PhysicalMaterial assets in the project: %d" % len(phys_assets))
for data in phys_assets:
    log("  %s" % data.get_editor_property("package_name"))

log("")
log("=== per static mesh, per material slot ===")
with_phys = 0
slots_total = 0
for data in meshes:
    asset = data.get_asset()
    if asset is None:
        continue
    path = str(data.get_editor_property("package_name"))
    if path.startswith("/Engine/"):
        continue
    try:
        materials = asset.get_editor_property("static_materials")
    except Exception as error:  # noqa: BLE001
        log("%s: static_materials unreadable (%s)" % (path, error))
        continue
    for index, slot in enumerate(materials):
        slots_total += 1
        interface = slot.get_editor_property("material_interface")
        slot_name = slot.get_editor_property("material_slot_name")
        phys = None
        if interface is not None:
            try:
                phys = interface.get_editor_property("phys_material")
            except Exception:  # noqa: BLE001 - MaterialInstance spells it elsewhere
                try:
                    parent = interface.get_editor_property("parent")
                    phys = parent.get_editor_property("phys_material") if parent else None
                except Exception:  # noqa: BLE001
                    phys = None
        described = describe(phys)
        if described:
            with_phys += 1
        log("  %-52s slot %d %-24s material=%-28s phys=%s"
            % (path, index, str(slot_name),
               interface.get_name() if interface else "<none>",
               described if described else "NONE"))

log("")
log("slots with a physical material: %d of %d" % (with_phys, slots_total))
log("")
log("Reading it: zero here means the per-face physics material feature would "
    "be plumbing with nothing to plumb -- the source content would have to be "
    "authored with physical materials first, which is a content decision, not "
    "an importer one.")

with open(OUT, "w") as handle:
    handle.write("\n".join(lines))
unreal.log("PROBE WROTE %s" % OUT)

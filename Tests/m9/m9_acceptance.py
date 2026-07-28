"""
m9_acceptance.py — M9 acceptance, editor half: the stretch features import.

Fresh import of Fixture_02 (the M3/M7/M8 pattern), then per feature:

  * counters: decals_created == 1, cameras_created == 1, entities == manifest;
  * Decal_01: Sort Key reads back 7 and Material resolves to the converted
    M_Fixture_Decal product;
  * Cam_Main: Field of view reads back as vertical_fov_deg(72, aspect) --
    the UE-horizontal -> O3DE-vertical conversion, within 0.05 deg;
  * one foliage instance entity's world translation matches the manifest
    (the expansion carried real per-instance transforms, not the component
    origin);
  * the spline entity's Mesh component references the '#spline' product.

Run: Tests/o3de/run_o3de_python.bat Tests/m9/m9_acceptance.py
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm9_acceptance_result.txt')

EXPORT_DIR = os.path.join(REPO_ROOT, "Exports", "Fixture_02")

lines = []
failures = []


def log(msg):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def main():
    import azlmbr.asset as asset_api
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general

    from ueimporter import camera_build, decal_build, importer, manifest_io, prefab_build

    manifest_path = os.path.join(EXPORT_DIR, "manifest.json")
    if not check(os.path.exists(manifest_path),
                 "manifest missing at %s -- export Fixture_02 first" % manifest_path):
        return
    document = manifest_io.load(manifest_path)

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/Fixture_02.prefab" % project_root

    log("importing Fixture_02 (fresh; the M9 chain is under test)")
    report, _saved = importer.import_level(
        manifest_path=manifest_path,
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        log=log)
    check(not report.has_errors(), "import report contains errors")
    check(report.counters.get("decals_created") == 1,
          "decals_created %r != 1" % report.counters.get("decals_created"))
    check(report.counters.get("cameras_created") == 1,
          "cameras_created %r != 1" % report.counters.get("cameras_created"))
    check(report.counters.get("entities_created") == len(document["entities"]),
          "entities_created %r != manifest %d"
          % (report.counters.get("entities_created"), len(document["entities"])))

    def find_entity(name):
        search = entity_module.SearchFilter()
        search.names = [name]
        found = entity_module.SearchBus(bus.Broadcast, 'SearchEntities', search)
        return found[0] if found else None

    def component_property(entity_id, component_name, path):
        type_id = prefab_build.resolve_component_type(component_name)
        outcome = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentOfType', entity_id, type_id)
        if not outcome or not outcome.IsSuccess():
            return None
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'GetComponentProperty', outcome.GetValue(), path)
        if not result or not result.IsSuccess():
            return None
        return result.GetValue()

    manifest_entities = {e["name"]: e for e in document["entities"]}
    assets_by_guid = manifest_io.assets_by_guid(document)

    log('')
    log('=== decal readback ===')
    decal_id = find_entity("Decal_01")
    if check(decal_id is not None, "Decal_01 not found"):
        sort_key = component_property(decal_id, decal_build.DECAL_COMPONENT,
                                      decal_build.SORT_KEY_PROPERTY)
        log('  Sort Key = %r' % sort_key)
        check(sort_key == 7, "decal Sort Key %r != 7" % sort_key)
        material_value = component_property(decal_id, decal_build.DECAL_COMPONENT,
                                            decal_build.MATERIAL_PROPERTY)
        path_back = asset_api.AssetCatalogRequestBus(
            bus.Broadcast, 'GetAssetPathById', material_value) \
            if material_value is not None else ""
        log('  Material -> %s' % path_back)
        check("m_fixture_decal" in (path_back or "").lower(),
              "decal Material resolves to %r, expected the converted "
              "m_fixture_decal product" % path_back)

    log('')
    log('=== camera readback ===')
    camera_id = find_entity("Cam_Main")
    if check(camera_id is not None, "Cam_Main not found"):
        fov = component_property(camera_id, camera_build.CAMERA_COMPONENT,
                                 camera_build.FOV_PROPERTY)
        block = manifest_entities["Cam_Main"]["camera"]
        expected = camera_build.vertical_fov_deg(block["fov_horizontal_deg"],
                                                 block["aspect_ratio"])
        log('  Field of view = %r (expected vertical %.4f)' % (fov, expected))
        check(fov is not None and abs(float(fov) - expected) < 0.05,
              "camera FOV %r is not the converted vertical %.4f" % (fov, expected))

    log('')
    log('=== one foliage instance transform ===')
    instance_names = [n for n in manifest_entities
                      if n.startswith("Foliage_ISM.") and n.endswith("#3")]
    if check(len(instance_names) == 1,
             "expected one #3 instance entity, got %r" % instance_names):
        name = instance_names[0]
        entity_id = find_entity(name)
        if check(entity_id is not None, "%s not found" % name):
            want = manifest_entities[name]["transform"]["world"]["translation"]
            got = components.TransformBus(bus.Event, 'GetWorldTranslation',
                                          entity_id)
            delta = max(abs(got.x - want[0]), abs(got.y - want[1]),
                        abs(got.z - want[2]))
            log('  %s at (%.3f, %.3f, %.3f), manifest %r' % (
                name, got.x, got.y, got.z, want))
            check(delta < 0.01,
                  "instance transform off by %.4f m; the expansion placed the "
                  "component origin, not the instance" % delta)

    log('')
    log('=== spline entity mesh ===')
    from ueimporter import staging
    spline_names = [n for n in manifest_entities
                    if n.startswith("SplineArch.")]
    if check(len(spline_names) == 1,
             "expected one SplineArch child, got %r" % spline_names):
        entity_id = find_entity(spline_names[0])
        if check(entity_id is not None, "%s not found" % spline_names[0]):
            value = component_property(entity_id, "Mesh",
                                       "Controller|Configuration|Model Asset")
            path_back = asset_api.AssetCatalogRequestBus(
                bus.Broadcast, 'GetAssetPathById', value) \
                if value is not None else ""
            log('  Model Asset -> %s' % path_back)
            # Identity, not naming: the sanitized stem of an ACTOR-derived
            # asset path collapses to the package stem (same as terrain);
            # what matters is that the entity references exactly the
            # manifest's '#spline' asset product.
            spline_asset = assets_by_guid[
                manifest_entities[spline_names[0]]["mesh"]["asset_guid"]]
            check(spline_asset["ue_path"].endswith("#spline"),
                  "the SplineArch child does not reference a #spline asset")
            expected = staging.product_path_for(
                spline_asset["o3de_relative_path"], "assets")
            check((path_back or "").lower() == expected,
                  "spline entity's model is %r, expected %r"
                  % (path_back, expected))


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

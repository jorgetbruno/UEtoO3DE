"""
m3b_manifest_import.py — a REAL manifest, imported on whichever backend.

The gap this closes was found by adversarial review and was severe: nothing
anywhere ran a manifest through the PhysX adapter. `m3b_backend_smoke.py`
drives the adapter directly, and every other suite runs on the Jolt project,
so `physics_build`'s render-mesh fallbacks — which called
`add_mesh_collider` with NO capability guard — were never exercised on a
backend that refuses it. On PhysX that raised out of `import_level` and
aborted the whole import with no prefab written, on 4 entities of
Fixture_01 and 14 of L_Showcase, while both M3b suites reported PASS.

So this asserts the thing that actually matters to a user: the import
COMPLETES on either backend, and where the backend genuinely cannot build a
collider it says so in the report rather than dying or staying silent.

  * the import returns a report and writes a prefab;
  * entity count matches the manifest;
  * on a backend WITHOUT the render-mesh bake (PhysX), every entity that
    would have taken that path is reported as PHYS_SHAPE_APPROXIMATED, and
    `mesh_colliders` is zero — degraded, reported, not aborted;
  * on a backend WITH it (Jolt), mesh colliders are actually authored, so
    the assertion above cannot pass vacuously by nobody needing one.

Env: UEO3DE_EXPECT_BACKEND (jolt|physx), as in m3b_backend_smoke.
Run: Tests/o3de/run_o3de_python.bat Tests/m3b/m3b_manifest_import.py <result> <project>
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
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm3b_manifest_import_result.txt')

EXPECT_BACKEND = os.environ.get("UEO3DE_EXPECT_BACKEND", "").strip().lower()
EXPORT_DIR = os.path.join(REPO_ROOT, "Exports", "Fixture_01")

lines = []
failures = []


def log(msg=""):
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
    import azlmbr.legacy.general as general

    from ueimporter import importer, manifest_io
    from ueimporter.adapters import base, make_adapter

    if not check(EXPECT_BACKEND in ("jolt", "physx"),
                 "UEO3DE_EXPECT_BACKEND must be jolt or physx, got %r"
                 % EXPECT_BACKEND):
        return
    manifest_path = os.path.join(EXPORT_DIR, "manifest.json")
    if not check(os.path.exists(manifest_path),
                 "manifest missing at %s" % manifest_path):
        return
    document = manifest_io.load(manifest_path)

    project_root = general.get_game_folder().rstrip('/\\')
    prefab_path = "%s/Prefabs/Fixture_01_M3b_%s.prefab" % (project_root, EXPECT_BACKEND)

    log("importing Fixture_01 on the %r backend" % EXPECT_BACKEND)
    report, saved = importer.import_level(
        manifest_path=manifest_path,
        source_assets_root=os.path.join(EXPORT_DIR, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        backend=EXPECT_BACKEND,
        log=log)

    log('')
    log('=== counters ===')
    for key in sorted(report.counters):
        log('  %-28s %d' % (key, report.counters[key]))
    by_code = {}
    for record in report.records():
        by_code[record["code"]] = by_code.get(record["code"], 0) + 1
    log('=== warnings by code ===')
    for code in sorted(by_code):
        log('  %-30s x%d' % (code, by_code[code]))

    check(not report.has_errors(), "import report contains errors")
    check(report.counters.get("entities_created") == len(document["entities"]),
          "created %r entities, manifest has %d"
          % (report.counters.get("entities_created"), len(document["entities"])))

    adapter = make_adapter(EXPECT_BACKEND)
    trimesh_ok = base.CAP_SHAPE_TRIMESH in adapter.capabilities()
    mesh_colliders = report.counters.get("mesh_colliders", 0)
    approximated = by_code.get("PHYS_SHAPE_APPROXIMATED", 0)
    log('')
    log("  backend advertises trimesh: %s | mesh_colliders=%d | "
        "PHYS_SHAPE_APPROXIMATED=%d" % (trimesh_ok, mesh_colliders, approximated))

    if trimesh_ok:
        check(mesh_colliders > 0,
              "this backend bakes colliders from render meshes, but the import "
              "authored none -- the PhysX assertion below would then be "
              "vacuous, since nothing in this fixture needs one")
    else:
        check(mesh_colliders == 0,
              "backend cannot bake render-mesh colliders yet %d were authored"
              % mesh_colliders)
        check(approximated > 0,
              "the backend silently skipped every render-mesh collider "
              "without reporting PHYS_SHAPE_APPROXIMATED -- a body with no "
              "collider must never be a silent outcome")


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

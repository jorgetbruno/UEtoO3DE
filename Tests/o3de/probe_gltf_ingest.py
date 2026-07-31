"""
probe_gltf_ingest.py -- does O3DE turn a UE-exported glTF into usable assets?

Question TWO of the two that decide whether the importer can speak glTF at all.
`Tests/ue/probe_gltf_export.py` answered question one: UE 5.8 exports .gltf,
.glb and skeletal meshes from Python. If the Asset Processor cannot turn those
into an .azmodel -- and, just as important, into the COOKED PHYSICS MESH the
collider pipeline now depends on -- then the feature is a different shape
entirely and no amount of importer work makes it exist.

Four things are asked, in the order that makes a failure diagnostic:

  1. does the Scene Builder claim .gltf/.glb at all (is there a builder
     pattern for them)?
  2. does a real UE-exported .gltf produce an .azmodel product?
  3. does a `.assetinfo` sidecar carrying a PHYSICS mesh group work on a glTF
     scene, or is that group FBX-only? The whole collider pipeline rests on
     it, and the group is SceneAPI-level in principle -- "in principle" is
     what this probe is for.
  4. what is the scene's node called? The sidecar's NodeSelectionList must
     name it exactly, and glTF node naming is not obliged to match FBX's.

Asserts nothing about geometry or orientation: glTF is Y-up right-handed in
metres where UE's FBX path is Z-up-ish in centimetres, so NONE of Lane B's
measured basis chain carries over. That is a separate measurement and it is
not worth making until this probe says the pipeline exists.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_gltf_ingest.py \
         <result> <project>
"""

import os
import shutil
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_gltf_ingest_result.txt'))

# Written by Tests/ue/probe_gltf_export.py.
SOURCE_DIR = os.path.join(REPO_ROOT, "Tests", "ue", "results", "gltf_probe")
STAGE_REL = "uetoo3de/gltfprobe"

lines = []
failures = []


def log(message=""):
    lines.append(str(message))
    print(message)


def fail(message):
    failures.append(str(message))
    log('FAIL: ' + str(message))


def main():
    import azlmbr.bus as bus
    import azlmbr.legacy.general as general

    from ueimporter import asset_wait, assetinfo

    general.idle_enable(True)
    project_root = general.get_game_folder().rstrip('/\\')
    log("project: %s" % project_root)

    log("")
    log("=== 1. does anything in this build claim glTF? ===")
    try:
        import azlmbr.asset.builder as builder  # noqa: F401
        log("  azlmbr.asset.builder imported")
    except Exception as error:  # noqa: BLE001
        log("  azlmbr.asset.builder not available (%s)" % error)
    scene_builder = os.path.join(os.path.dirname(project_root))
    log("  (builder patterns are not queryable from Python here; question 2 is "
        "the real test -- a product either appears or it does not)")

    log("")
    log("=== 2. stage a UE-exported .gltf and see what the AP makes of it ===")
    if not os.path.isdir(SOURCE_DIR):
        fail("no glTF probe output at %s -- run Tests/ue/probe_gltf_export.py "
             "first; this probe tests REAL UE output, not a hand-made file"
             % SOURCE_DIR)
        return

    staged_dir = os.path.join(project_root, "Assets", *STAGE_REL.split("/"))
    os.makedirs(staged_dir, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(SOURCE_DIR)):
        if name.lower().endswith((".gltf", ".glb", ".bin", ".png")):
            shutil.copy2(os.path.join(SOURCE_DIR, name),
                         os.path.join(staged_dir, name))
            copied.append(name)
    log("  staged into %s:" % staged_dir)
    for name in copied:
        log("    " + name)

    # A physics mesh group on the glTF scene, written by the SAME code the FBX
    # path uses -- if the group is FBX-only, this is where that shows.
    gltf_path = os.path.join(staged_dir, "SM_LetterF.gltf")
    if os.path.isfile(gltf_path):
        physics = {"method": "trimesh", "elements": 0, "decompose_hulls": None}
        try:
            sidecar = assetinfo.write(gltf_path, "SM_LetterF", physics=physics,
                                      backends=("jolt",))
            log("  wrote sidecar %s" % os.path.basename(sidecar))
        except Exception as error:  # noqa: BLE001
            fail("assetinfo.write refused a .gltf source: %s" % error)

    log("")
    log("  NOW RUN AssetProcessorBatch, then re-run this probe with "
        "UEO3DE_GLTF_CHECK=1 to read the catalog. Product names are only "
        "knowable after a processing pass.")

    if os.environ.get("UEO3DE_GLTF_CHECK", "").strip():
        log("")
        log("=== 3. what landed in the catalog ===")
        stem = "%s/sm_letterf" % STAGE_REL
        for suffix in (".gltf.azmodel", ".azmodel",
                       ".gltf.joltmesh", ".joltmesh",
                       ".gltf.pxmesh", ".pxmesh"):
            product = "assets/" + stem + suffix
            resolved = asset_wait.resolve(product)
            log("  %-46s %s" % (product,
                                "RESOLVED" if resolved else "absent"))
        if not any(asset_wait.resolve("assets/" + stem + s)
                   for s in (".gltf.azmodel", ".azmodel")):
            fail("no .azmodel for the staged glTF: O3DE's Scene Builder did "
                 "not ingest it, and the importer cannot speak glTF without "
                 "a model product")


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

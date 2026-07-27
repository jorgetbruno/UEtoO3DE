"""
m2_stage.py — stage the exported FBX files into the O3DE project (plan M2).

Plain Python, no editor. Copies every static mesh the manifest references into
`<project>/Assets/uetoo3de/...` and writes its `.assetinfo` sidecar, so
`AssetProcessorBatch` can be run to completion before the editor starts.

`--cold` deletes the previously staged tree AND the corresponding cache
products first. Plan constraint 10: "At least once nightly, run the full
pipeline with a deleted Cache/ so AP ordering bugs can't hide behind a warm
cache." A run that only ever passes on a warm cache is the exact failure
`wait_for_asset` exists to prevent, so the cold path is part of the suite
rather than a manual chore.

Usage:
    python Tests/m2/m2_stage.py [--cold] [--project <path>]
"""

import argparse
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

from ueimporter import importer, manifest_io, staging  # noqa: E402

DEFAULT_PROJECT = r"C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"
MANIFEST_PATH = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
SOURCE_ASSETS = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "Assets")
STAGED_SUBFOLDER = "uetoo3de"


def log(message):
    print(message)


def clear_cache_products(project, subfolder):
    """Delete the cached products for the staged tree (cold-cache runs).

    Scoped to the importer's own subfolder rather than removing Cache/ wholesale:
    rebuilding every engine and gem asset would add many minutes to each run
    without testing anything M2 owns. The AP ordering bug this guards against
    lives in the imported assets.
    """
    cache_root = os.path.join(project, "Cache")
    if not os.path.isdir(cache_root):
        return []
    removed = []
    for platform in sorted(os.listdir(cache_root)):
        target = os.path.join(cache_root, platform, "assets", subfolder)
        if os.path.isdir(target):
            shutil.rmtree(target)
            removed.append(target)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--source-assets", default=SOURCE_ASSETS)
    parser.add_argument("--cold", action="store_true",
                        help="delete the staged tree and its cache products first")
    args = parser.parse_args(argv)

    project_assets = os.path.join(args.project, "Assets")

    if args.cold:
        log("cold run: clearing staged sources and cache products")
        staging.clear(project_assets, STAGED_SUBFOLDER, log=log)
        for path in clear_cache_products(args.project, STAGED_SUBFOLDER):
            log("  cleared " + path)

    try:
        document, records = importer.stage_only(
            args.manifest, args.source_assets, project_assets, log=log)
    except (manifest_io.ManifestError, staging.StagingError) as exc:
        print("STAGE FAILED: %s" % exc)
        return 1

    mesh_assets = manifest_io.static_mesh_assets(document)
    if len(records) != len(mesh_assets):
        print("STAGE FAILED: staged %d files for %d mesh assets"
              % (len(records), len(mesh_assets)))
        return 1

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

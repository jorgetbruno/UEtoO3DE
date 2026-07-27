"""
m2_stage.py — stage the exported FBX files into the O3DE project (plan M2).

Plain Python, no editor. Copies every static mesh the manifest references into
`<project>/Assets/uetoo3de/...` and writes its `.assetinfo` sidecar, so
`AssetProcessorBatch` can be run to completion before the editor starts.

`--cold` deletes THIS manifest's previously staged files AND their cache
products first (never the shared staging tree -- other levels live there). Plan constraint 10: "At least once nightly, run the full
pipeline with a deleted Cache/ so AP ordering bugs can't hide behind a warm
cache." A run that only ever passes on a warm cache is the exact failure
`wait_for_asset` exists to prevent, so the cold path is part of the suite
rather than a manual chore.

Usage:
    python Tests/m2/m2_stage.py [--cold] [--project <path>]
"""

import argparse
import os
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


def clear_manifest_files(project, document):
    """Delete the staged sources and cache products of THIS manifest only.

    Scoped to the manifest's own files, never the whole `uetoo3de` tree: the
    staging area is shared by every imported level, and clearing the tree
    deletes the other levels' sources — after which AP removes their products
    and every previously imported prefab renders nothing. (That is not a
    hypothetical: an earlier revision cleared the whole tree and wiped a real
    level's 135 meshes during a fixture test run.)
    """
    project_assets = os.path.join(project, "Assets")
    removed = []

    clearable = [a for a in document["assets"]
                 if a["kind"] in ("static_mesh", "texture")
                 or (a["kind"] == "material" and a.get("material_data"))]
    for asset in clearable:
        relative = asset["o3de_relative_path"]
        for path in (os.path.join(project_assets, relative),
                     os.path.join(project_assets, relative + ".assetinfo")):
            if os.path.isfile(path):
                os.remove(path)
                removed.append(path)

        # Cache products: everything derived from this source file. Product
        # names share the source stem (model, lods, buffers, abdata).
        stem = os.path.splitext(os.path.basename(relative))[0]
        relative_dir = os.path.dirname(relative)
        cache_root = os.path.join(project, "Cache")
        if os.path.isdir(cache_root):
            for platform in sorted(os.listdir(cache_root)):
                folder = os.path.join(cache_root, platform, "assets", relative_dir)
                if not os.path.isdir(folder):
                    continue
                for name in os.listdir(folder):
                    if name.startswith(stem) or name.startswith("default_" + stem):
                        os.remove(os.path.join(folder, name))
                        removed.append(os.path.join(folder, name))
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--manifest", default=MANIFEST_PATH)
    parser.add_argument("--source-assets", default=SOURCE_ASSETS)
    parser.add_argument("--cold", action="store_true",
                        help="delete THIS manifest's staged files and cache products first")
    args = parser.parse_args(argv)

    project_assets = os.path.join(args.project, "Assets")

    if args.cold:
        log("cold run: clearing THIS manifest's staged sources and cache products")
        document_for_clear = manifest_io.load(args.manifest)
        removed = clear_manifest_files(args.project, document_for_clear)
        log("  cleared %d files" % len(removed))

    try:
        document, records = importer.stage_only(
            args.manifest, args.source_assets, project_assets, log=log)
    except (manifest_io.ManifestError, staging.StagingError) as exc:
        print("STAGE FAILED: %s" % exc)
        return 1

    mesh_records = [r for r in records if r.get("kind") == "static_mesh"]
    mesh_assets = manifest_io.static_mesh_assets(document)
    if len(mesh_records) != len(mesh_assets):
        print("STAGE FAILED: staged %d FBX files for %d mesh assets"
              % (len(mesh_records), len(mesh_assets)))
        return 1
    by_kind = {}
    for r in records:
        by_kind[r.get("kind")] = by_kind.get(r.get("kind"), 0) + 1
    print("staged by kind: %r" % by_kind)

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

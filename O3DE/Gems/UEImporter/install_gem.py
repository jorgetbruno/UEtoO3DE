"""
install_gem.py — put UEImporter into an O3DE project, with no engine rebuild.

Run:  python O3DE/Gems/UEImporter/install_gem.py --project <path-to-project>
      python O3DE/Gems/UEImporter/install_gem.py --project <path> --check

Why this exists, and why it is not just `o3de enable-gem`:

Registering the gem and adding it to `gem_names` is necessary but NOT
sufficient for a Python-only gem running against a **prebuilt SDK editor**.
Measured in Tests/o3de/probe_m10_gempath2.py:

    paths.gemroot("UEImporter")                        -> the right path
    paths.resolve_path("@gemroot:UEImporter@/Editor")  -> '' (alias missing)

The runtime knows the gem by name, but never mounts it, so
EditorPythonBindings never scans `Editor/Scripts` and `bootstrap.py` is never
read. The gem is not broken and neither is the registration -- the editor is
simply activating the gems its *build* told it about, and a gem with no CMake
target was never in that list. Rebuilding the project fixes it; so does the
setreg written here, which supplies the one thing the build would have
contributed: a target entry. A target with no modules is normal (the engine's
own `Camera.Tools` has exactly that shape), which is precisely what a gem with
no compiled code needs.

The alternative -- telling every user to rebuild their project to get a Python
tool -- is not a tool anyone would install.

`--check` verifies an existing installation without changing anything, so CI
can assert the environment rather than discovering it mid-suite.
"""

import argparse
import json
import os
import sys

GEM_ROOT = os.path.dirname(os.path.abspath(__file__))
GEM_NAME = "UEImporter"
SETREG_NAME = "ueimporter_gem.setreg"


def _forward(path):
    return os.path.abspath(path).replace("\\", "/")


def _gem_base_name(entry):
    """`"UEImporter==0.3.0"` -> `"UEImporter"`.

    O3DE writes VERSION SPECIFIERS into `gem_names` ("UEImporter==0.3.0",
    "PhysX>=2.0"), and comparing the raw string against a bare name reports a
    gem that is plainly there as missing -- measured on a project whose
    gem_names contained exactly that, where `--check` said "not in gem_names"
    and the install path would then have appended a SECOND entry for the same
    gem. `staging.project_physics_backends` already strips these; this is the
    same rule, and the two must agree or the two halves of the pipeline
    disagree about whether a gem is present.
    """
    name = entry if isinstance(entry, str) else (entry or {}).get("name", "")
    return str(name).split(">")[0].split("<")[0].split("=")[0].strip()


def _gem_base_names(gem_names):
    return [_gem_base_name(entry) for entry in gem_names]


def setreg_contents(gem_root=None):
    """The registry entry that mounts the gem. See the module docstring."""
    root = _forward(gem_root or GEM_ROOT)
    return {
        "O3DE": {
            "Gems": {
                GEM_NAME: {
                    "Path": root,
                    "SourcePaths": [root],
                    # A target with no "Modules" is a gem with no compiled
                    # code. This is the entry the CMake build would have
                    # generated, and the only reason the editor mounts a gem.
                    "Targets": {GEM_NAME + ".Editor": {}},
                }
            }
        }
    }


def install(project_path, gem_root=None, log=print):
    """Idempotent. Returns a list of the changes actually made."""
    changes = []
    project_path = os.path.abspath(project_path)
    project_json = os.path.join(project_path, "project.json")
    if not os.path.isfile(project_json):
        raise SystemExit("not an O3DE project (no project.json): " + project_path)

    with open(project_json, "r") as handle:
        document = json.load(handle)

    gem_names = document.setdefault("gem_names", [])
    plain = _gem_base_names(gem_names)
    if GEM_NAME not in plain:
        gem_names.append(GEM_NAME)
        changes.append("added %s to gem_names" % GEM_NAME)

    root = _forward(gem_root or GEM_ROOT)
    externals = document.setdefault("external_subdirectories", [])
    if not any(_forward(e) == root for e in externals if isinstance(e, str)):
        externals.insert(0, root)
        changes.append("added the gem to external_subdirectories")

    if changes:
        with open(project_json, "w") as handle:
            json.dump(document, handle, indent=4)
            handle.write("\n")

    registry_dir = os.path.join(project_path, "Registry")
    os.makedirs(registry_dir, exist_ok=True)
    setreg_path = os.path.join(registry_dir, SETREG_NAME)
    wanted = setreg_contents(gem_root)
    existing = None
    if os.path.isfile(setreg_path):
        try:
            with open(setreg_path, "r") as handle:
                existing = json.load(handle)
        except Exception:
            existing = None
    if existing != wanted:
        with open(setreg_path, "w") as handle:
            json.dump(wanted, handle, indent=4)
            handle.write("\n")
        changes.append("wrote " + setreg_path)

    for change in changes:
        log("  " + change)
    if not changes:
        log("  already installed; nothing to do")
    return changes


def check_gem_buildable(gem_root):
    """Problems that stop O3DE from MOUNTING or CONFIGURING the gem.

    The project-side checks below answer "did we write the right entries".
    They passed while the gem itself was unusable, which is the failure this
    covers: a project.json naming a gem, and a setreg pointing at a directory,
    say nothing about whether that directory is a gem O3DE can load. Three
    things have to hold, and each has been wrong at least once:

      * `gem.json` exists and its `gem_name` is the name the project asks for
        -- a mismatch mounts nothing, silently;
      * the gem is registered in the O3DE manifest's external subdirectories,
        which is how the engine finds the path at all. project.json listing
        the NAME is not the same as the engine knowing the PATH;
      * `CMakeLists.txt` exists. A registered directory without one fails
        project configuration for the whole project, not just this gem.
    """
    problems = []
    gem_root = os.path.abspath(gem_root)

    gem_json = os.path.join(gem_root, "gem.json")
    if not os.path.isfile(gem_json):
        problems.append(
            "missing %s -- O3DE will not mount a directory that does not "
            "declare itself a gem, however it is referenced" % gem_json)
    else:
        try:
            with open(gem_json, "r") as handle:
                declared = json.load(handle).get("gem_name")
        except Exception as exc:  # noqa: BLE001 - report, never raise out of a check
            declared = None
            problems.append("%s is not valid JSON: %s" % (gem_json, exc))
        if declared is not None and declared != GEM_NAME:
            problems.append(
                "%s declares gem_name %r, but the project asks for %r; the "
                "names must match or nothing mounts"
                % (gem_json, declared, GEM_NAME))

    cmake_lists = os.path.join(gem_root, "CMakeLists.txt")
    if not os.path.isfile(cmake_lists):
        problems.append(
            "missing %s -- a registered gem directory without one breaks "
            "configuration for every project that references it" % cmake_lists)

    manifest = os.path.expanduser(os.path.join("~", ".o3de", "o3de_manifest.json"))
    if not os.path.isfile(manifest):
        problems.append(
            "no %s -- cannot confirm the engine knows where this gem lives"
            % manifest)
    else:
        try:
            with open(manifest, "r") as handle:
                registered = json.load(handle).get("external_subdirectories") or []
        except Exception as exc:  # noqa: BLE001
            registered = []
            problems.append("%s is not valid JSON: %s" % (manifest, exc))
        wanted = _forward(gem_root).rstrip("/").lower()
        if not any(_forward(str(entry)).rstrip("/").lower() == wanted
                   for entry in registered):
            problems.append(
                "%s is not in %s external_subdirectories -- project.json can "
                "name the gem all it likes; without this the engine never "
                "resolves the name to this path (o3de register --gem-path)"
                % (gem_root, manifest))

    return problems


def check(project_path, gem_root=None, log=print):
    """Verify without writing. Returns a list of problems (empty == installed)."""
    problems = []
    project_path = os.path.abspath(project_path)
    project_json = os.path.join(project_path, "project.json")
    if not os.path.isfile(project_json):
        return ["not an O3DE project: " + project_path]

    with open(project_json, "r") as handle:
        document = json.load(handle)
    plain = _gem_base_names(document.get("gem_names", []))
    if GEM_NAME not in plain:
        problems.append("%s is not in project.json gem_names" % GEM_NAME)

    setreg_path = os.path.join(project_path, "Registry", SETREG_NAME)
    if not os.path.isfile(setreg_path):
        problems.append(
            "missing %s -- without it the editor resolves the gem's name but "
            "never mounts it, and bootstrap.py is silently never read"
            % setreg_path)
    else:
        with open(setreg_path, "r") as handle:
            try:
                actual = json.load(handle)
            except Exception as exc:
                actual = None
                problems.append("%s is not valid JSON: %s" % (setreg_path, exc))
        if actual is not None:
            entry = (actual.get("O3DE", {}).get("Gems", {}).get(GEM_NAME) or {})
            root = _forward(gem_root or GEM_ROOT)
            if _forward(entry.get("Path", "")) != root:
                problems.append(
                    "%s points at %r, but this gem lives at %r"
                    % (setreg_path, entry.get("Path"), root))
            if not entry.get("Targets"):
                problems.append(
                    "%s has no Targets entry; that entry is the whole point "
                    "of the file" % setreg_path)

    bootstrap = os.path.join(gem_root or GEM_ROOT, "Editor", "Scripts", "bootstrap.py")
    if not os.path.isfile(bootstrap):
        problems.append("missing " + bootstrap)

    problems.extend(check_gem_buildable(gem_root or GEM_ROOT))

    for problem in problems:
        log("  PROBLEM: " + problem)
    if not problems:
        log("  ok: %s is installed in %s" % (GEM_NAME, project_path))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--project", required=True, action="append",
                        help="project path (repeatable)")
    parser.add_argument("--gem-root", default=GEM_ROOT)
    parser.add_argument("--check", action="store_true",
                        help="verify only; change nothing")
    args = parser.parse_args(argv)

    failures = 0
    for project in args.project:
        print(("checking " if args.check else "installing into ") + project)
        if args.check:
            failures += len(check(project, args.gem_root))
        else:
            install(project, args.gem_root)
            failures += len(check(project, args.gem_root))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

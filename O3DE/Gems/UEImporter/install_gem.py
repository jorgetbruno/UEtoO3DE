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
    plain = [g if isinstance(g, str) else g.get("name") for g in gem_names]
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


def check(project_path, gem_root=None, log=print):
    """Verify without writing. Returns a list of problems (empty == installed)."""
    problems = []
    project_path = os.path.abspath(project_path)
    project_json = os.path.join(project_path, "project.json")
    if not os.path.isfile(project_json):
        return ["not an O3DE project: " + project_path]

    with open(project_json, "r") as handle:
        document = json.load(handle)
    plain = [g if isinstance(g, str) else g.get("name")
             for g in document.get("gem_names", [])]
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

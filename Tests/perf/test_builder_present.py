"""test_builder_present.py -- a gem being LISTED is not the gem being BUILT.

Pure: no editor, no engine. Run: python Tests/perf/test_builder_present.py

THE FAILURE THIS PINS, measured on a fresh project (D:/O3DE/Projects/Boat):

    project.json gem_names   contains "JoltPhysics==1.0.0"
    sidecars written          141, every one carrying a JoltMeshGroup
    .azmodel produced         141 of 141
    .joltmesh produced        0
    Asset Processor errors    0
    job logs mentioning Jolt  none

The gem was declared and never compiled, so the Asset Processor had no
serializer for `JoltMeshGroup` -- and an unrecognised `.assetinfo` entry is
SILENTLY DROPPED. Nothing anywhere reported a problem; the level would simply
have had no collision.

WHY IT ONLY BITES SOME BACKENDS, and why the check looks in two places:

    PhysX  ships WITH THE ENGINE -- PhysX.Editor.Gem.dll is in <engine>/bin,
           so a PhysX project needs no build and must NEVER trip this guard
    Jolt   is an EXTERNAL gem -- JoltPhysics.Editor.dll exists only after the
           project itself is built

Both directions are tested. A guard that fires on a healthy PhysX project
would be worse than no guard: it would be turned off, and the real failure
would come back.

The engine lookup is monkeypatched throughout so this suite asserts the same
thing on a machine with no O3DE installed.
"""

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import staging  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def make_project(gem_names, built=(), engine_dlls=()):
    """A throwaway project tree; returns (assets_root, engine_bin)."""
    root = tempfile.mkdtemp(prefix="ueo3de_proj_")
    with open(os.path.join(root, "project.json"), "w") as handle:
        json.dump({"project_name": "T", "gem_names": list(gem_names)}, handle)
    assets = os.path.join(root, "Assets")
    os.makedirs(assets, exist_ok=True)

    for name in built:
        target = os.path.join(root, "build", "windows", "bin", "profile")
        os.makedirs(target, exist_ok=True)
        open(os.path.join(target, name), "wb").close()

    engine_bin = os.path.join(root, "_engine", "bin", "Windows", "profile")
    os.makedirs(engine_bin, exist_ok=True)
    for name in engine_dlls:
        open(os.path.join(engine_bin, name), "wb").close()
    return assets, os.path.join(root, "_engine", "bin")


class engine_roots(object):
    """Pin what counts as 'the installed engines' for one block."""

    def __init__(self, roots):
        self.roots = list(roots)

    def __enter__(self):
        self.original = staging._engine_bin_roots
        staging._engine_bin_roots = lambda: self.roots
        return self

    def __exit__(self, *_exc):
        staging._engine_bin_roots = self.original


def backends(assets):
    """What stage() would end up cooking -- scan THEN builder verification.

    Kept separate in the module on purpose: `project_physics_backends` answers
    "what does the project declare" and stays pure so it can be unit-tested
    against a project.json in a temp directory; `verify_builders_present`
    answers "can this machine actually build it". Merging them made every
    synthetic fixture in test_pxmesh start raising.
    """
    try:
        chosen = staging.project_physics_backends(assets)
        staging.verify_builders_present(assets, chosen)
        return chosen
    except staging.StagingError as error:
        return error


# --- 1. listed but never built -> REFUSE ---------------------------------------
assets, engine = make_project(["JoltPhysics==1.0.0"])
with engine_roots([]):
    result = backends(assets)
check(isinstance(result, staging.StagingError),
      "an unbuilt JoltPhysics must RAISE; got %r. Staging would otherwise write "
      "141 sidecars whose physics groups the AP silently drops" % (result,))
if isinstance(result, staging.StagingError):
    text = str(result)
    check("JoltPhysics.Editor" in text,
          "the message must name the TARGET TO BUILD; got %r" % text[:120])
    check("UEO3DE_JOLT_COOK=0" in text,
          "the message must name the escape hatch, or the only way past a "
          "false positive is editing the importer")
    check("SILENTLY DROPPED" in text or "silently dropped" in text.lower(),
          "the message must say WHY it matters -- 'gem not built' alone does "
          "not tell anyone their level will have no collision")

# --- 2. built in the project -> allowed ----------------------------------------
assets, engine = make_project(["JoltPhysics==1.0.0"],
                              built=["JoltPhysics.Editor.dll"])
with engine_roots([]):
    check(backends(assets) == ("jolt",),
          "a project whose build tree has JoltPhysics.Editor.dll must cook jolt")

# --- 3. shipped with the ENGINE -> allowed with no build at all ----------------
# This is the PhysX shape, and the false positive that would discredit the
# whole guard.
assets, engine = make_project(["PhysX5"], engine_dlls=["PhysX.Editor.Gem.dll"])
with engine_roots([engine]):
    check(backends(assets) == ("physx",),
          "PhysX ships in the engine bin, so a PhysX project with NO build "
          "directory must still cook -- a guard that fires here gets disabled")

# --- 4. the override wins, both ways -------------------------------------------
assets, engine = make_project(["JoltPhysics"])
os.environ["UEO3DE_JOLT_COOK"] = "0"
try:
    with engine_roots([]):
        check(backends(assets) == (),
              "UEO3DE_JOLT_COOK=0 must mean 'stage without jolt' even when the "
              "builder is missing -- that is the documented way past this")
finally:
    del os.environ["UEO3DE_JOLT_COOK"]

os.environ["UEO3DE_JOLT_COOK"] = "1"
try:
    with engine_roots([]):
        check(backends(assets) == ("jolt",),
              "an explicit COOK=1 must still force the backend on: gems "
              "activate transitively, and that override exists for exactly "
              "the case this scan cannot see")
finally:
    del os.environ["UEO3DE_JOLT_COOK"]

# --- 5. a backend not listed at all is not a build problem ---------------------
assets, engine = make_project(["Atom", "EMotionFX"])
with engine_roots([]):
    check(backends(assets) == (),
          "a project with no physics gem listed cooks nothing and must not "
          "raise -- there is no builder to be missing")

# --- 6. the predicate itself ---------------------------------------------------
assets, engine = make_project(["JoltPhysics"], built=["JoltPhysics.Editor.dll"])
project_root = os.path.dirname(assets)
with engine_roots([]):
    check(not staging.backend_builder_missing(assets, "jolt"),
          "backend_builder_missing must be False when the dll is there")
    check(staging.backend_builder_missing(assets, "physx"),
          "backend_builder_missing must be True for a backend with no dll")
    check(not staging.backend_builder_missing(assets, "nonsense"),
          "an unknown backend has no prefix to look for and must not be "
          "reported as missing")
    check(len(staging._builder_binaries(project_root, "JoltPhysics")) == 1,
          "the dll should be found exactly once in the build tree")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

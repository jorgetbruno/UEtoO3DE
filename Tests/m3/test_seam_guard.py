"""
test_seam_guard.py — the physics-backend seam is enforceable; enforce it.

Plan constraint 5: "No milestone outside the adapter implementations may
contain a physics component name as a string literal. This is enforceable:
grep the importer for 'Jolt ' / 'PhysX ' in CI." This is that grep.

Scanned: the whole `ueimporter` package (excluding `adapters/`), the whole
`ueo3de` exporter package, and the M2+ test entry scripts that drive imports.
Probes and milestone acceptance tests are exempt -- they exist to *verify* the
seam and the simulation, and asserting on Jolt behaviour requires naming it.

Also asserts the reverse direction: the adapters package MUST contain the
names (a refactor that moved them out and broke detection would otherwise
pass), and `physics_build` must not import any concrete adapter module.

Run:  python Tests/m3/test_seam_guard.py
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCAN_ROOTS = [
    ("O3DE/Gems/UEImporter/Editor/Scripts/ueimporter", ("adapters",)),
    ("UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python/ueo3de", ()),
    ("Tests/m2", ()),
]

# The forbidden literals, with the trailing space the plan specifies -- these
# match component display names ("Jolt Rigid Body"), not prose about the
# JoltPhysics gem or backend ids like "jolt".
FORBIDDEN = (re.compile(r'"Jolt '), re.compile(r"'Jolt "),
             re.compile(r'"PhysX '), re.compile(r"'PhysX "))

ADAPTERS_DIR = "O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/adapters"

failures = []


def fail(message):
    failures.append(message)
    print("FAIL: " + message)


def scan():
    scanned = 0
    for root, excluded in SCAN_ROOTS:
        base = os.path.join(REPO_ROOT, root)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in excluded and d != "__pycache__"]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                scanned += 1
                with open(path, "r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, 1):
                        for pattern in FORBIDDEN:
                            if pattern.search(line):
                                fail("%s:%d contains a physics component-name "
                                     "literal outside adapters/: %s"
                                     % (os.path.relpath(path, REPO_ROOT),
                                        line_number, line.strip()[:90]))
    print("  scanned %d files outside adapters/" % scanned)


def check_adapters_carry_the_names():
    """The names must live in adapters/ -- and nowhere else."""
    found = False
    base = os.path.join(REPO_ROOT, ADAPTERS_DIR)
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(dirpath, name), "r", encoding="utf-8") as handle:
                text = handle.read()
            if '"Jolt ' in text or "'Jolt " in text:
                found = True
    if not found:
        fail("adapters/ no longer contains the Jolt component names; "
             "detection and authoring cannot work")


def check_physics_build_is_backend_neutral():
    path = os.path.join(REPO_ROOT,
                        "O3DE/Gems/UEImporter/Editor/Scripts/ueimporter/physics_build.py")
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    for module in ("adapters.jolt", "adapters.physx", "from .adapters import jolt",
                   "from .adapters import physx"):
        if module in text:
            fail("physics_build.py imports a concrete adapter (%r); it may only "
                 "speak the interface" % module)


def main():
    scan()
    check_adapters_carry_the_names()
    check_physics_build_is_backend_neutral()
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

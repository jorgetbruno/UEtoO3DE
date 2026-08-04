"""
test_install_gem.py — the gem-installed check must be able to say NO.

Pure: no editor. Run: python Tests/perf/test_install_gem.py

`install_gem.py --check` is what M10 runs to decide the gem is installed, and
it only ever inspected the PROJECT side: is the name in project.json, is there
a Registry setreg, does it point somewhere, does bootstrap.py exist. All four
can be true of a gem the engine cannot mount or a project cannot configure,
and that is exactly the state it once reported as healthy.

`check_gem_buildable` covers the gem side, and this pins that each of its
three failures is REACHABLE — a check that cannot fail is decoration:

  * `gem.json` missing, or declaring a different `gem_name` than the project
    asks for (mounts nothing, silently);
  * `CMakeLists.txt` missing (breaks configuration for every project that
    references the directory, not just this gem);
  * the directory absent from the O3DE manifest's external subdirectories
    (project.json names the gem; only the manifest resolves that name to a
    path).

The happy path is asserted against the REAL gem root, so a change that breaks
the actual installation fails here too rather than only in an editor run.
"""

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEM_DIR = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter")
sys.path.insert(0, GEM_DIR)

import install_gem  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def problems_for(root):
    return install_gem.check_gem_buildable(root)


def mentions(problems, needle):
    return any(needle.lower() in problem.lower() for problem in problems)


# --- 1. the real gem passes ---------------------------------------------------
real = problems_for(GEM_DIR)
check(not real,
      "the shipped gem root reports problems it should not: %r" % (real,))

# --- 2. every failure is reachable -------------------------------------------
empty = tempfile.mkdtemp(prefix="ueo3de_gem_empty_")
problems = problems_for(empty)
check(mentions(problems, "gem.json"),
      "a directory with no gem.json must be reported; got %r" % (problems,))
check(mentions(problems, "CMakeLists.txt"),
      "a directory with no CMakeLists.txt must be reported; got %r" % (problems,))
check(mentions(problems, "external_subdirectories"),
      "an unregistered directory must be reported -- project.json naming a gem "
      "does not tell the engine where it lives; got %r" % (problems,))

# --- 3. a gem.json that names something else -----------------------------------
wrong = tempfile.mkdtemp(prefix="ueo3de_gem_wrong_")
with open(os.path.join(wrong, "gem.json"), "w") as handle:
    json.dump({"gem_name": "SomethingElse"}, handle)
open(os.path.join(wrong, "CMakeLists.txt"), "w").close()
problems = problems_for(wrong)
check(mentions(problems, "gem_name"),
      "a gem.json declaring a different gem_name must be reported: the project "
      "asks for %r and would mount nothing; got %r"
      % (install_gem.GEM_NAME, problems))
check(not mentions(problems, "CMakeLists.txt"),
      "CMakeLists.txt exists here, so it must not be reported; got %r"
      % (problems,))

# --- 4. malformed gem.json is a problem, not a crash --------------------------
broken = tempfile.mkdtemp(prefix="ueo3de_gem_broken_")
with open(os.path.join(broken, "gem.json"), "w") as handle:
    handle.write("{not json")
try:
    problems = problems_for(broken)
    check(mentions(problems, "valid json"),
          "a malformed gem.json must be reported as a problem; got %r" % (problems,))
except Exception as error:  # noqa: BLE001
    check(False,
          "a malformed gem.json raised %s instead of being reported -- a check "
          "that throws cannot be used to decide anything" % type(error).__name__)

# --- 5. gem_names carry VERSION SPECIFIERS ------------------------------------
# Measured on D:/O3DE/Projects/Phoenix, whose project.json contained
# "UEImporter==0.3.0": `--check` reported "UEImporter is not in project.json
# gem_names" about a gem that was plainly there, and `install` would then have
# appended a SECOND entry for the same gem. `staging.project_physics_backends`
# already strips these specifiers; the two halves of the pipeline must agree
# about whether a gem is present.
for spelling, wanted in (
        ("UEImporter", "plain"),
        ("UEImporter==0.3.0", "== pin"),
        ("UEImporter>=0.3.0", ">= floor"),
        ("UEImporter<2.0", "< ceiling"),
        (" UEImporter == 1.0 ", "whitespace"),
        ({"name": "UEImporter==0.3.0"}, "dict form with a specifier"),
):
    check(install_gem._gem_base_name(spelling) == "UEImporter",
          "%s (%r) must resolve to the bare gem name, got %r"
          % (wanted, spelling, install_gem._gem_base_name(spelling)))

check(install_gem._gem_base_name("UEImporterExtra") == "UEImporterExtra",
      "a DIFFERENT gem whose name merely starts the same must not be "
      "flattened onto UEImporter -- stripping specifiers must not become a "
      "prefix match")

# install() must be idempotent against a specifier-bearing entry: the bug
# would have shown up as a duplicate, not an error.
project = tempfile.mkdtemp(prefix="ueo3de_proj_spec_")
with open(os.path.join(project, "project.json"), "w") as handle:
    json.dump({"project_name": "T", "gem_names": ["Atom", "UEImporter==0.3.0"],
               "external_subdirectories": []}, handle)
install_gem.install(project, gem_root=GEM_DIR, log=lambda _m: None)
with open(os.path.join(project, "project.json")) as handle:
    after = json.load(handle)["gem_names"]
check(sum(1 for g in after if install_gem._gem_base_name(g) == "UEImporter") == 1,
      "installing over an existing 'UEImporter==0.3.0' must not add a second "
      "entry; gem_names came out %r" % (after,))
check("UEImporter==0.3.0" in after,
      "the existing PINNED entry must be left alone -- rewriting someone's "
      "version pin is not this script's business; got %r" % (after,))

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

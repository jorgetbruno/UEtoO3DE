"""test_scratch_level.py -- imports must not author inside someone's work.

Pure: no editor. Run: python Tests/perf/test_scratch_level.py

THE INCIDENT THIS PINS (2026-08-23): imports used DefaultLevel as a
disposable scratch level -- opened with no save prompt, placed instances of
the target prefab REMOVED, level saved. That machinery ran against the
DefaultLevel a user was actively building in, and their placed scene was
stripped and saved over. Recovery came from the editor's .bak files, not
from anything this pipeline did right.

Three rules, each tested:
  * the DEFAULT authoring level is a dedicated scratch level, seeded from
    the engine's own project template (levels cannot be created from Python
    on this build -- probed, both create APIs return None and write nothing);
  * a level holding real work REFUSES the import, whatever its name, with
    UEO3DE_SCRATCH_OK=1 as the explicit override;
  * seeding only ever creates the dedicated scratch level -- any other name
    belongs to the user and is left exactly as found.
"""

import json
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import importer  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def write_level(project_root, name, entity_names):
    path = importer.level_prefab_path(project_root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    document = {"ContainerEntity": {"Name": name},
                "Entities": {"E%d" % i: {"Name": n}
                             for i, n in enumerate(entity_names)}}
    with open(path, "w") as handle:
        json.dump(document, handle)
    return path


# --- 1. the default is the dedicated scratch level, env-overridable ------------
check(importer.scratch_level_name({}) == "UEO3DE_Scratch",
      "the default authoring level must be the dedicated scratch level, "
      "never DefaultLevel -- DefaultLevel is where users build")
check(importer.scratch_level_name({"UEO3DE_SCRATCH_LEVEL": "MyScratch"})
      == "MyScratch", "the env override must win")
check(importer.scratch_level_name({"UEO3DE_SCRATCH_LEVEL": "  "})
      == "UEO3DE_Scratch", "whitespace is not a level name")

# --- 2. a populated level refuses, whatever it is called -----------------------
project = tempfile.mkdtemp(prefix="ueo3de_scratch_")
write_level(project, "DefaultLevel",
            ["Sun", "Sky"] + ["Pier_%03d" % i for i in range(200)])
try:
    importer.refuse_populated_level(project, "DefaultLevel", environ={})
    check(False, "a 202-entity DefaultLevel must REFUSE -- authoring in it "
                 "strips and saves over the user's scene")
except RuntimeError as error:
    text = str(error)
    check("UEO3DE_SCRATCH_OK" in text,
          "the refusal must name its override; got %r" % text[:120])
    check("REMOVES" in text or "removes" in text,
          "the refusal must say WHAT the import does to the level -- "
          "'too many entities' alone explains nothing")

# the explicit override wins
importer.refuse_populated_level(project, "DefaultLevel",
                                environ={"UEO3DE_SCRATCH_OK": "1"})

# a stock-sized level passes (the engine template is ~9 names)
write_level(project, "SmallLevel", ["Sun", "Sky", "Camera", "Grid"])
importer.refuse_populated_level(project, "SmallLevel", environ={})

# a MISSING level is not a refusal -- there is nothing there to destroy
importer.refuse_populated_level(project, "NoSuchLevel", environ={})

# even the scratch level itself refuses when someone filled it with work:
# the guard is about CONTENT, not names
write_level(project, "UEO3DE_Scratch",
            ["Thing_%03d" % i for i in range(100)])
try:
    importer.refuse_populated_level(project, "UEO3DE_Scratch", environ={})
    check(False, "a scratch level someone has filled with 100 entities is "
                 "work now, and must refuse like anything else")
except RuntimeError:
    pass

# --- 3. seeding: template in, only for the dedicated name ----------------------
project2 = tempfile.mkdtemp(prefix="ueo3de_scratch2_")
template = os.path.join(project2, "_template.prefab")
with open(template, "w") as handle:
    json.dump({"ContainerEntity": {"Name": "Level"},
               "Entities": {"E0": {"Name": "Sun"}}}, handle)

target = importer.ensure_scratch_level(project2, "UEO3DE_Scratch",
                                       template_path=template)
check(os.path.isfile(target),
      "the scratch level must be seeded from the template when absent")
check(target == importer.level_prefab_path(project2, "UEO3DE_Scratch"),
      "seeded at the level path the editor will open")
with open(target) as handle:
    check(json.load(handle)["Entities"]["E0"]["Name"] == "Sun",
          "the seeded level must be the template's content")

# seeding is idempotent -- a second call must not rewrite the file
before = os.path.getmtime(target)
importer.ensure_scratch_level(project2, "UEO3DE_Scratch", template_path=template)
check(os.path.getmtime(target) == before,
      "ensure must be a no-op when the scratch level already exists")

# any OTHER missing level is not ours to create
other = importer.ensure_scratch_level(project2, "UsersLevel",
                                      template_path=template)
check(not os.path.isfile(other),
      "ensure must never create a level that is not the dedicated scratch -- "
      "a user's mistyped level name must fail at open, not silently become "
      "an empty level")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

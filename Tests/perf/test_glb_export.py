"""test_glb_export.py -- the static-mesh container switch, and the basis map.

Pure: no UE, no editor. Run: python Tests/perf/test_glb_export.py

Two things are pinned here, and each is silent-wrong if it breaks:

  1. `UEO3DE_MESH_FORMAT` picks the STATIC MESH container and NOTHING ELSE.
     Skeletal meshes and animations stay FBX, so a glb run is a MIXED-format
     export -- the case that proves staging keys on each file's own extension
     rather than a global flag. A typo must RAISE, not fall back to FBX and
     produce a run that looks successful.

  2. `gltf_reader.expected_from_fbx_bounds` converts the exporter's ONE
     recorded per-mesh expectation from the FBX file basis into the glTF one.
     Both containers are written from the SAME baked mesh, so the map depends
     only on the two writers and holds for `#mx` variants too. It is validated
     here against UE 5.8's REAL SM_LetterF.glb, whose UE-source bounds are
     known independently from Exports/LaneB/SM_LetterF.ue_reference.json.

Getting the map wrong by a sign or an axis is exactly the failure the export
verifier exists to catch, so the verifier's own arithmetic needs pinning.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tests", "lib"))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

import gltf_reader  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- 1. the format switch ------------------------------------------------------
# ue_level imports `unreal` at module scope, so the switch is read from the
# source rather than imported. That is deliberate: this suite must stay
# runnable without an editor, and the alternative -- not testing it at all --
# is how a typo'd format silently became FBX.
UE_LEVEL = os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                        "UEO3DEExporter", "Content", "Python", "ueo3de",
                        "ue_level.py")
source = open(UE_LEVEL, encoding="utf-8").read()

namespace = {"os": os}
start = source.index("_STATIC_MESH_FORMATS")
end = source.index("_EXTENSIONS = {")
exec(compile(source[start:end], UE_LEVEL, "exec"), namespace)  # noqa: S102
static_mesh_format = namespace["static_mesh_format"]

check(static_mesh_format({}) == "fbx",
      "with nothing set the container must be FBX: every existing export, "
      "fixture and byte-pin means FBX")
check(static_mesh_format({"UEO3DE_MESH_FORMAT": ""}) == "fbx",
      "an empty value means unset, not an error")
for value in ("glb", "GLB", " glb ", ".glb"):
    check(static_mesh_format({"UEO3DE_MESH_FORMAT": value}) == "glb",
          "%r should select glb" % value)
check(static_mesh_format({"UEO3DE_MESH_FORMAT": "FBX"}) == "fbx",
      "'FBX' should select fbx")
for value in ("gltf", "obj", "gl b", "true"):
    try:
        static_mesh_format({"UEO3DE_MESH_FORMAT": value})
        check(False, "%r must RAISE: falling back to FBX would make a whole "
                     "export look successful in the wrong container" % value)
    except ValueError:
        pass

# `.gltf` is deliberately NOT offered: UE writes it with a companion .bin and
# staging copies exactly one file.
check("gltf" not in namespace["_STATIC_MESH_FORMATS"],
      ".gltf must not be selectable -- its companion .bin would be left behind")

# --- 2. the FBX -> glTF bounds map, against real UE bytes ----------------------
FIXTURE = os.path.join(REPO_ROOT, "Tests", "ue", "data", "SM_LetterF.glb")
if not os.path.exists(FIXTURE):
    check(False, "missing %s -- it is committed, so the working tree is "
                 "incomplete" % FIXTURE)
else:
    stats = gltf_reader.vertex_stats(FIXTURE)
    check(stats["count"] == 93,
          "the fixture has 93 vertices in UE and must read back as 93; got %d"
          % stats["count"])

    # UE asset space (cm) from Exports/LaneB/SM_LetterF.ue_reference.json.
    ue_min, ue_max = [-50.0, -12.5, 0.0], [50.0, 37.5, 200.0]
    # This .glb is a RAW export -- no Lane A bake -- so its FBX-file equivalent
    # is the source under the FBX writer's Y negation alone.
    fbx_min = [ue_min[0], -ue_max[1], ue_min[2]]
    fbx_max = [ue_max[0], -ue_min[1], ue_max[2]]

    predicted_min, predicted_max = gltf_reader.expected_from_fbx_bounds(fbx_min, fbx_max)
    check(max(abs(a - b) for a, b in zip(predicted_min, stats["min"])) < 1e-6,
          "predicted glTF min %s != measured %s"
          % ([round(v, 4) for v in predicted_min], [round(v, 4) for v in stats["min"]]))
    check(max(abs(a - b) for a, b in zip(predicted_max, stats["max"])) < 1e-6,
          "predicted glTF max %s != measured %s"
          % ([round(v, 4) for v in predicted_max], [round(v, 4) for v in stats["max"]]))

    # The map must be METRES: a missing /100 is the easiest slip to make and
    # would leave every bound 100x out while the axis order still looked right.
    check(max(abs(v) for v in stats["max"]) < 10.0,
          "a glTF file is in METRES; %s looks like centimetres"
          % [round(v, 4) for v in stats["max"]])

    # Negating an axis SWAPS min and max. Pin it directly, or an
    # expected_from_fbx_bounds that forgot would still pass a symmetric mesh.
    lo, hi = gltf_reader.expected_from_fbx_bounds([0.0, 10.0, 0.0], [1.0, 30.0, 2.0])
    check(lo == [0.0, 0.0, -0.3] and hi == [0.01, 0.02, -0.1],
          "the negated axis must swap min and max: got %s .. %s"
          % ([round(v, 4) for v in lo], [round(v, 4) for v in hi]))

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

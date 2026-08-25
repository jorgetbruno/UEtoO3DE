"""probe_gltf_vertices.py -- the glTF basis, settled at vertex level.

`probe_gltf_basis.py` measures each format's cooked physics AABB and finds the
same extents and units. **An AABB cannot finish this job.** SM_LetterF's bounds
are SYMMETRIC IN X (-50..+50 cm), so an X mirror -- which flips winding and
would ship silently as a backwards level -- is completely invisible to it. The
Y asymmetry it can see; the X mirror it cannot.

So this probe reads the ACTUAL VERTEX POSITIONS out of the products O3DE wrote
and compares them as sets. No editor: the `.azbuffer` products are on disk
after an AssetProcessor pass, and plain floats can be read out of them.

MEASURED (Jolt project, UE 5.8 / O3DE 26.05), against the UE reference in
`Exports/LaneB/SM_LetterF.ue_reference.json` -- 93 vertices in UE, 93 in the
glTF file, 93 in every product, so the comparison is exact and not a resample:

    UE asset space (m)   centroid (-0.2097, +0.0659, +1.4452)
    fbx    product       centroid (-0.2097, -0.0659, +1.4452)   diag( 1,-1,1)
    glTF   product       centroid (+0.2097, +0.0659, +1.4452)   diag(-1, 1,1)

and therefore, checked over ALL 93 vertices as sets with ZERO deviation:

    glTF product  ==  Rz180 . FBX product          diag(-1,-1,1), det +1

**That is a PROPER ROTATION.** The two formats differ by a lossless 180 degree
yaw -- no mirror, no winding flip, so none of Lane B's `#mx` mirrored-variant
machinery is needed for glTF. The compensation is one Rz180, and the codebase
already has that operation as `skel_build.compose_rz180`, proven as a matrix
identity in `Tests/m8/test_skel_build.py`.

Requires: SM_LetterF staged and processed in BOTH formats (M2 stages the FBX;
stage `Tests/ue/data/SM_LetterF.glb` for the glTF). Fails loudly when a product
is missing rather than reporting a comparison it did not make.

Run: python Tests/o3de/probe_gltf_vertices.py [<project-root>]
"""

import json
import os
import struct
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # .../Tests/o3de/<this> -> repo

sys.path.insert(0, os.path.join(REPO_ROOT, "Tests"))
from paths import PATHS  # noqa: E402

DEFAULT_PROJECT = PATHS.get("O3DE_PROJECT_JOLT")
PROJECT = (sys.argv[1] if len(sys.argv) > 1 else
           os.environ.get("O3DE_PROJECT_JOLT") or DEFAULT_PROJECT)
CACHE = os.path.join(PROJECT, "Cache", "pc", "assets", "uetoo3de")

UE_REFERENCE = os.path.join(REPO_ROOT, "Exports", "LaneB",
                            "SM_LetterF.ue_reference.json")

# UE asset-space extents in metres. Used to FIND the position block inside the
# .azbuffer -- the product carries a header this does not attempt to parse, so
# the run of floats is located by the shape it must have.
EXTENTS_M = (1.0, 0.5, 2.0)
VERTEX_COUNT = 93

PRODUCTS = {
    "fbx": os.path.join(CACHE, "game", "meshes",
                        "sm_letterf_lod0_position0.fbx.azbuffer"),
    "gltf": os.path.join(CACHE, "glbprobe",
                         "sm_letterf_lod0_position0.glb.azbuffer"),
}

CANDIDATES = (
    ("identity", (1, 1, 1)),
    ("Rz180", (-1, -1, 1)),
    ("Rx180", (1, -1, -1)),
    ("Ry180", (-1, 1, -1)),
    ("mirror X", (-1, 1, 1)),
    ("mirror Y", (1, -1, 1)),
    ("mirror Z", (1, 1, -1)),
)

failures = []


def fail(message):
    failures.append(message)
    print("FAIL: " + message)


def read_positions(path, count=VERTEX_COUNT, extents=EXTENTS_M):
    """The vertex positions inside an `.azbuffer`, or None.

    The block is located by its SHAPE -- `count` finite floats in a plausible
    coordinate range whose per-axis extents match `extents` -- rather than by a
    header offset, because the header layout is not documented here and a
    hard-coded offset would break silently on the next engine version. A
    mislocated block cannot match the extents by accident at this precision.
    """
    with open(path, "rb") as handle:
        blob = handle.read()
    need = count * 12
    for offset in range(0, len(blob) - need + 1):
        values = struct.unpack_from("<%df" % (need // 4), blob, offset)
        if not all(-1e3 < v < 1e3 for v in values):
            continue
        xs, ys, zs = values[0::3], values[1::3], values[2::3]
        got = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
        if all(abs(g - w) < 1e-3 for g, w in zip(got, extents)):
            return list(zip(xs, ys, zs)), offset
    return None, None


def as_set(vertices, signs):
    return sorted(tuple(round(s * v, 5) for s, v in zip(signs, vertex))
                  for vertex in vertices)


def centroid(vertices):
    n = float(len(vertices))
    return tuple(sum(v[i] for v in vertices) / n for i in range(3))


def main():
    print("project: %s" % PROJECT)

    if os.path.exists(UE_REFERENCE):
        reference = json.load(open(UE_REFERENCE))
        ue_centroid = tuple(v / 100.0 for v in reference["centroid"])
        print("UE asset space (m): centroid (%+.4f, %+.4f, %+.4f), %d vertices"
              % (ue_centroid + (reference["vertex_count"],)))
        if reference["vertex_count"] != VERTEX_COUNT:
            fail("UE reference has %d vertices, this probe assumes %d -- the "
                 "fixture changed and every number below is suspect"
                 % (reference["vertex_count"], VERTEX_COUNT))
    else:
        fail("missing %s" % UE_REFERENCE)

    measured = {}
    print("")
    for label, path in sorted(PRODUCTS.items()):
        if not os.path.exists(path):
            fail("%s product missing: %s -- stage and process it first"
                 % (label, path))
            continue
        vertices, offset = read_positions(path)
        if vertices is None:
            fail("%s: no %d-vertex position block with extents %s found in %s"
                 % (label, VERTEX_COUNT, list(EXTENTS_M), os.path.basename(path)))
            continue
        measured[label] = vertices
        print("%-5s %d vertices at byte %-4d centroid (%+.4f, %+.4f, %+.4f)"
              % ((label, len(vertices), offset) + centroid(vertices)))

    if len(measured) < 2:
        fail("need both products to compare bases; got %s"
             % (sorted(measured) or "none"))
        return

    print("")
    print("does   glTF product == M . FBX product   hold over ALL vertices?")
    target = as_set(measured["gltf"], (1, 1, 1))
    winners = []
    for name, signs in CANDIDATES:
        determinant = signs[0] * signs[1] * signs[2]
        if as_set(measured["fbx"], signs) == target:
            winners.append((name, signs, determinant))
            verdict = ("PROPER ROTATION - lossless, winding preserved"
                       if determinant > 0 else
                       "MIRROR - flips winding, needs a mirrored variant")
            print("  %-10s diag(%+d,%+d,%+d)  YES   det %+d  %s"
                  % ((name,) + signs + (determinant, verdict)))
        else:
            print("  %-10s diag(%+d,%+d,%+d)  no" % ((name,) + signs))

    print("")
    if not winners:
        fail("the two products differ by NONE of the axis-sign maps tried: the "
             "glTF basis is not a signed permutation of the FBX one, and the "
             "compensation is not a rotation. Read the vertices directly.")
        return
    if len(winners) > 1:
        fail("%d maps fit, so the fixture cannot tell them apart: %s. A more "
             "asymmetric mesh is needed to settle it."
             % (len(winners), [w[0] for w in winners]))
        return

    name, signs, determinant = winners[0]
    if determinant < 0:
        fail("the formats differ by %s, a MIRROR (det %+d). Winding flips, so "
             "glTF needs the mirrored-variant treatment Lane B built for #mx, "
             "not a rotation." % (name, determinant))
        return
    print("SETTLED: the glTF product is the FBX product under %s "
          "(det %+d, a proper rotation)." % (name, determinant))
    print("  Lossless, winding preserved, no mirrored variant needed.")
    print("  The importer's compensation is one %s -- and skel_build."
          "compose_rz180 already implements exactly that." % name)


main()
print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

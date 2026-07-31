"""gltf_reader.py -- vertex statistics from a `.gltf` / `.glb`, like fbx_reader.

`export_level.py` verifies every written mesh against the bounds the exporter
expects; that guard is what catches a bake that is missing or doubled, and it
must not lapse just because the container changed. Same interface as
`fbx_reader.vertex_stats` so the verifier only has to pick a reader.

UNITS AND AXES ARE NOT THE FBX ONES. Measured on UE 5.8's own export of
SM_LetterF (`Tests/o3de/probe_gltf_vertices.py`, and the raw file read
directly):

    file (metres, Y-up)      = (x_ue, z_ue, y_ue) / 100
    FBX file (cm, Z-up)      = (x_ue, -y_ue, z_ue)

so a glTF file is NOT an FBX with different bytes -- it is Y-up and in metres,
and `expected_from_fbx_bounds` below converts one expectation into the other so
the exporter keeps ONE recorded expectation per mesh instead of two that can
disagree.

The container is parsed by `ueimporter.gltf_source`, not re-implemented here.
Four hand-copied AABB helpers had already drifted apart in this repo before
they were factored into `Tests/lib/editor_physics.py`; a second GLB chunk
parser would go the same way.
"""

import os
import struct
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # .../Tests/lib/<this> -> repo
_GEM_SCRIPTS = os.path.join(_REPO_ROOT, "O3DE", "Gems", "UEImporter",
                            "Editor", "Scripts")
if _GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, _GEM_SCRIPTS)

from ueimporter import gltf_source  # noqa: E402


class GltfError(Exception):
    pass


# glTF componentType -> (struct code, byte size). Only float positions are
# valid for POSITION in practice; anything else is reported, not guessed at.
_COMPONENT = {5126: ("f", 4)}
_TYPE_COUNT = {"VEC3": 3}


def _buffer_bytes(path, document):
    """The single binary blob the accessors index into.

    `.glb` carries it as chunk 1. A `.gltf` points at a companion file via
    `buffers[0].uri`; that file must be beside it, which is exactly why the
    exporter emits `.glb` (staging copies one file).
    """
    if gltf_source.is_glb(path):
        for kind, data in gltf_source.read_glb_chunks(path):
            if kind == 0x004E4942:      # 'BIN\0'
                return data
        raise GltfError("%s has no BIN chunk" % path)

    buffers = document.get("buffers") or []
    if not buffers:
        raise GltfError("%s declares no buffers" % path)
    uri = buffers[0].get("uri")
    if not uri:
        raise GltfError("%s has a bufferless buffer and is not a .glb" % path)
    if uri.startswith("data:"):
        raise GltfError("%s uses a data: URI, which is not supported here" % path)
    companion = os.path.join(os.path.dirname(path), uri)
    if not os.path.exists(companion):
        raise GltfError(
            "%s references %s, which is not beside it -- a .gltf is not a "
            "single file and its companion must travel with it" % (path, uri))
    with open(companion, "rb") as handle:
        return handle.read()


def _positions(path, document, blob):
    """Every POSITION value in the file, as a flat list, in file units."""
    values = []
    for mesh in document.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            index = primitive.get("attributes", {}).get("POSITION")
            if index is None:
                continue
            accessor = document["accessors"][index]
            component = accessor.get("componentType")
            if component not in _COMPONENT:
                raise GltfError("%s: POSITION componentType %r is not float"
                                % (path, component))
            kind = accessor.get("type")
            if kind not in _TYPE_COUNT:
                raise GltfError("%s: POSITION type %r is not VEC3" % (path, kind))
            code, size = _COMPONENT[component]
            width = _TYPE_COUNT[kind]
            view = document["bufferViews"][accessor["bufferView"]]
            start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            stride = view.get("byteStride") or (size * width)
            for i in range(accessor["count"]):
                values.extend(struct.unpack_from("<%d%s" % (width, code),
                                                 blob, start + i * stride))
    return values


def read(path):
    """{'vertices': [x, y, z, ...]} in the FILE's units (metres, Y-up)."""
    document = gltf_source.load_document(path)
    blob = _buffer_bytes(path, document)
    return {"vertices": _positions(path, document, blob), "document": document}


def vertex_stats(path):
    """AABB, centroid and count of every vertex position in the file.

    Values are in the FILE's own units: metres, Y-up right-handed. That is not
    what `fbx_reader.vertex_stats` returns for an FBX (centimetres, Z-up), and
    the two must never be compared without going through
    `expected_from_fbx_bounds`.
    """
    values = read(path)["vertices"]
    if not values or len(values) % 3:
        raise GltfError("no usable POSITION data in " + path)
    xs, ys, zs = values[0::3], values[1::3], values[2::3]
    count = len(xs)
    return {
        "count": count,
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
        "centroid": [sum(xs) / count, sum(ys) / count, sum(zs) / count],
    }


def expected_from_fbx_bounds(fbx_min, fbx_max, unit_scale=100.0):
    """The same geometry's glTF-file bounds, given its FBX-file bounds.

    Both containers are written from the SAME baked mesh, so this depends only
    on the two writers and NOT on the bake -- it holds for `#mx` variants and
    normal entries alike:

        baked    = (fbx_x, -fbx_y, fbx_z)        undo the FBX writer's Y flip
        glTF     = (baked_x, baked_z, baked_y) / 100    Y-up, cm -> m

    so  glTF = (fbx_x, fbx_z, -fbx_y) / 100.  Negating an axis SWAPS its min
    and max, which is the easy thing to get wrong here.
    """
    lo = [fbx_min[0] / unit_scale, fbx_min[2] / unit_scale, -fbx_max[1] / unit_scale]
    hi = [fbx_max[0] / unit_scale, fbx_max[2] / unit_scale, -fbx_min[1] / unit_scale]
    return lo, hi

"""
fbx_reader.py — minimal binary FBX reader for tests.

Generalized from `Tests/ue/probe_fbx_globalsettings.py`, which M0 used to
measure what UE's exporter actually writes (spike S0.2). Promoted to a shared
module because M2 needs the same numbers to check that the geometry handed to
SceneAPI carries Lane B's reflection.

Reads only what the tests assert on: the GlobalSettings axis/unit properties
and the vertex position arrays. It is not a general FBX library and should not
grow into one.
"""

import struct
import zlib

_MAGIC = b"Kaydara FBX Binary  "

_SCALAR_FORMATS = {
    "I": ("<i", 4),
    "D": ("<d", 8),
    "L": ("<q", 8),
    "F": ("<f", 4),
    "Y": ("<h", 2),
}

_ARRAY_FORMATS = {"b": "b", "c": "b", "i": "i", "f": "f", "d": "d", "l": "q"}

GLOBAL_SETTING_KEYS = (
    "UpAxis", "UpAxisSign", "FrontAxis", "FrontAxisSign",
    "CoordAxis", "CoordAxisSign", "UnitScaleFactor", "OriginalUnitScaleFactor",
)


class FbxError(Exception):
    pass


def _read_node(data, offset):
    end, property_count, _property_bytes = struct.unpack_from("<III", data, offset)
    name_length = data[offset + 12]
    name = data[offset + 13:offset + 13 + name_length].decode("ascii", "replace")
    cursor = offset + 13 + name_length

    properties = []
    for _ in range(property_count):
        type_code = chr(data[cursor])
        cursor += 1
        if type_code in _SCALAR_FORMATS:
            fmt, size = _SCALAR_FORMATS[type_code]
            properties.append(struct.unpack_from(fmt, data, cursor)[0])
            cursor += size
        elif type_code in ("S", "R"):
            length = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
            properties.append(data[cursor:cursor + length])
            cursor += length
        elif type_code == "C":
            properties.append(data[cursor])
            cursor += 1
        else:
            count, encoding, compressed_length = struct.unpack_from("<III", data, cursor)
            cursor += 12
            payload = data[cursor:cursor + compressed_length]
            cursor += compressed_length
            if encoding == 1:
                payload = zlib.decompress(payload)
            fmt = _ARRAY_FORMATS.get(type_code)
            if fmt:
                width = struct.calcsize(fmt)
                properties.append(list(struct.unpack(
                    "<%d%s" % (count, fmt), payload[:count * width])))
            else:
                properties.append("<array %s x%d>" % (type_code, count))

    children = []
    child_cursor = cursor
    while child_cursor < end - 13:
        child, child_cursor = _read_node(data, child_cursor)
        children.append(child)
    return (name, properties, children), end


def _top_level_nodes(data):
    offset = 27  # 21-byte magic + 2 reserved bytes + version u32
    while offset < len(data) - 13:
        if all(byte == 0 for byte in data[offset:offset + 13]):
            break  # null record terminates the top-level node list
        node, offset = _read_node(data, offset)
        yield node


def _walk(node, visit):
    visit(node)
    for child in node[2]:
        _walk(child, visit)


def read(path):
    """Parse `path` and return {'global_settings': {...}, 'vertices': [...]}. """
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:20] != _MAGIC:
        raise FbxError("not a binary FBX: " + path)

    settings = {}
    # Per-geometry arrays in file order, so collision geometry can be left
    # out: the exporter now writes UE's hull elements as a `UCX_<node>`
    # mesh (verbatim source space, deliberately NOT under the bake -- see
    # mesh_export._copy_source_hulls), and a bounds check that swept those
    # vertices in with the render mesh reported every hull-bearing car as
    # mirrored. Geometry is tied to its Model through the `C` connections;
    # a geometry connected to a `UCX_`-named model is collision.
    geometries = []          # [(geometry id, vertices, polygon indices)]
    models = {}              # model id -> name
    parents = {}             # child object id -> parent object id

    def visit(node):
        name, properties, children = node
        if name == "P" and properties and isinstance(properties[0], bytes):
            key = properties[0].decode("ascii", "replace")
            if key in GLOBAL_SETTING_KEYS:
                settings[key] = properties[4:]
        elif name == "Geometry" and properties:
            verts, polys = [], []
            for child_name, child_properties, _grandchildren in children:
                if (child_name == "Vertices" and child_properties
                        and isinstance(child_properties[0], list)):
                    verts = child_properties[0]
                elif (child_name == "PolygonVertexIndex" and child_properties
                        and isinstance(child_properties[0], list)):
                    polys = child_properties[0]
            geometries.append((properties[0], verts, polys))
        elif name == "Model" and len(properties) > 1 and isinstance(properties[1], bytes):
            models[properties[0]] = properties[1].split(b"\x00")[0].decode("ascii", "replace")
        elif name == "C" and len(properties) >= 3:
            parents[properties[1]] = properties[2]

    for node in _top_level_nodes(data):
        _walk(node, visit)

    def is_collision(geometry_id):
        return models.get(parents.get(geometry_id), "").startswith("UCX_")

    vertices = []
    polygons = []
    for geometry_id, verts, polys in geometries:
        if is_collision(geometry_id):
            continue
        vertices.extend(verts)
        polygons.extend(polys)

    return {"global_settings": settings, "vertices": vertices,
            "polygon_vertex_index": polygons}


def vertex_stats(path):
    """AABB, centroid and count of every vertex position stored in the file.

    Values are in the file's own units, which for UE's exporter is centimetres
    with no axis conversion applied (measured in S0.2).
    """
    parsed = read(path)
    values = parsed["vertices"]
    if not values or len(values) % 3:
        raise FbxError("no usable vertex array in " + path)

    xs, ys, zs = values[0::3], values[1::3], values[2::3]
    count = len(xs)
    return {
        "count": count,
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
        "centroid": [sum(xs) / count, sum(ys) / count, sum(zs) / count],
        "global_settings": parsed["global_settings"],
    }


def signed_volume(path):
    """Signed volume of the mesh via the divergence theorem.

    Sum over triangles of dot(v0, cross(v1, v2)) / 6, with FBX polygons
    fan-triangulated (`PolygonVertexIndex` marks each polygon's last index as
    `~index`). For a closed mesh the MAGNITUDE is the enclosed volume and the
    SIGN encodes the winding: mirror a mesh without correcting winding and the
    sign flips. That makes it the one-number check for "is the mirrored bake
    inside-out", which per-vertex byte tests cannot see.
    """
    parsed = read(path)
    values = parsed["vertices"]
    indices = parsed["polygon_vertex_index"]
    if not values or not indices:
        raise FbxError("no polygon data in " + path)

    def vertex(index):
        base = index * 3
        return values[base], values[base + 1], values[base + 2]

    total = 0.0
    polygon = []
    for raw in indices:
        index = ~raw if raw < 0 else raw
        polygon.append(index)
        if raw < 0:
            for corner in range(1, len(polygon) - 1):
                ax, ay, az = vertex(polygon[0])
                bx, by, bz = vertex(polygon[corner])
                cx, cy, cz = vertex(polygon[corner + 1])
                total += (ax * (by * cz - bz * cy)
                          + ay * (bz * cx - bx * cz)
                          + az * (bx * cy - by * cx))
            polygon = []
    return total / 6.0

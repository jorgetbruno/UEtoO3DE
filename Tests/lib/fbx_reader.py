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
    vertices = []

    def visit(node):
        name, properties, _children = node
        if name == "P" and properties and isinstance(properties[0], bytes):
            key = properties[0].decode("ascii", "replace")
            if key in GLOBAL_SETTING_KEYS:
                settings[key] = properties[4:]
        elif name == "Vertices" and properties and isinstance(properties[0], list):
            vertices.extend(properties[0])

    for node in _top_level_nodes(data):
        _walk(node, visit)

    return {"global_settings": settings, "vertices": vertices}


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

"""
gltf_source.py — make a UE-exported glTF addressable by a `.assetinfo`.

PURE (json + struct only). Two facts, both measured against UE 5.8 and
O3DE 26.05, and both invisible until an Asset Processor job fails:

  1. **UE writes the mesh node UNNAMED.** Its glTF exporter names only the
     MESH (`meshes[i].name`), leaving `nodes[i].name` absent. SceneAPI selects
     by NODE, so there is nothing to select by name and the graph falls back to
     a synthesised `nodes[0]`. Naming the node ourselves is the fix, and the
     file is ours to write: `name_mesh_nodes` does it.

  2. **A glTF scene graph has no `RootNode`.** FBX graphs are rooted at a node
     literally called `RootNode`, so a selection path reads
     `RootNode.<node>` -- that is measured and correct for FBX. A glTF's root
     has an EMPTY path and the mesh sits directly beneath it, so the same
     path names nothing. `node_path` returns the right shape per format.

Both were found by dumping the graph from inside the Scene Builder
(`Tests/o3de/gltf_manifest_script.py`) after four static guesses were rejected
with the same unhelpful warning:

    SceneAPI: MeshGroup <name> wasn't found in the list of selected nodes.

With the node named and the prefix dropped, a UE glTF produces both
`<stem>.gltf.azmodel` and `<stem>.gltf.joltmesh` -- render and cooked physics
-- with zero AP errors. See LANE_C_GLTF.md.

BOTH CONTAINERS are handled. `.gltf` is a JSON file beside a `.bin`; `.glb`
is the same JSON as chunk 0 of a binary container, and naming its nodes means
rewriting that chunk in place. The container layout below is not read from the
spec -- it is what UE 5.8 actually wrote for `SM_LetterF.glb`:

    header  b'glTF' | version 2 | totalLength 143188
    chunk 0 b'JSON' | len 1804    padded with SPACES  -> {"nodes":[{"mesh":0}]}
    chunk 1 b'BIN\\0' | len 141356  padded with a NUL   buffers[0] byteLength 141355

Three details there are load-bearing, and each is a corruption if missed: a
chunk's declared length INCLUDES its padding (12 + 8+1804 + 8+141356 = 143188,
the declared total), the BIN chunk's padding is NOT counted in the buffer's
`byteLength`, and the two chunks pad with different bytes. So this module
rewrites chunk 0 and copies every other chunk through verbatim -- byte-identical,
padding and all -- rather than re-deriving anything it was not asked to change.
"""

import json
import struct

GLTF_EXTENSIONS = (".gltf",)
GLB_EXTENSIONS = (".glb",)

_GLB_MAGIC = b"glTF"
_GLB_VERSION = 2
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
# The spec pads chunk 0 with spaces and chunk 1 with NULs, and UE does exactly
# that. The two fillers are NOT interchangeable: a space is JSON whitespace, a
# NUL is not. Padding chunk 0 with NULs was tried here and `json.loads` threw
# `Extra data: line 1 column 1822` on the very next read -- so this table is
# measured, not transcribed.
_PAD = {_CHUNK_JSON: b"\x20", _CHUNK_BIN: b"\x00"}


def is_gltf(path):
    """Is this the JSON container (`.gltf`)?"""
    return str(path).lower().endswith(GLTF_EXTENSIONS)


def is_glb(path):
    """Is this the binary container (`.glb`)?"""
    return str(path).lower().endswith(GLB_EXTENSIONS)


def is_gltf_source(path):
    """Is this glTF in either container?

    The container decides how the bytes are read; the SCENE GRAPH is identical
    either way, so every selection-path question uses this and not `is_gltf`.
    """
    return is_gltf(path) or is_glb(path)


def node_path(node_name, source_path):
    """The scene-graph path a `.assetinfo` must name for this source.

    FBX: `RootNode.<node>`. glTF: `<node>`, because its graph root is unnamed.
    """
    if is_gltf_source(source_path):
        return node_name
    return "RootNode." + node_name


def root_path(source_path):
    """What to put in `unselectedNodes` to mean "the root".

    FBX names its root; a glTF root has no name, so there is nothing to
    unselect and the list stays empty.
    """
    return [] if is_gltf_source(source_path) else ["RootNode"]


def _pad_to_four(data, filler):
    remainder = len(data) % 4
    return data if not remainder else data + filler * (4 - remainder)


def read_glb_chunks(path):
    """Every chunk of a `.glb` as `[(type, data_including_padding), ...]`.

    Raises on anything that is not the container we measured. A file that is
    truncated or mislabelled must fail HERE, while the bytes are in hand, not
    as an unexplained Asset Processor error two steps later.
    """
    with open(path, "rb") as handle:
        blob = handle.read()

    if len(blob) < 12:
        raise ValueError("%s is %d bytes: too short to be a .glb header"
                         % (path, len(blob)))
    magic, version, declared = struct.unpack_from("<4sII", blob, 0)
    if magic != _GLB_MAGIC:
        raise ValueError("%s does not start with %r; it is not a .glb"
                         % (path, _GLB_MAGIC))
    if version != _GLB_VERSION:
        raise ValueError("%s is glB version %d; only %d is measured here"
                         % (path, version, _GLB_VERSION))
    if declared != len(blob):
        raise ValueError(
            "%s declares %d bytes but is %d on disk; refusing to rewrite a "
            "file whose length header already disagrees with itself"
            % (path, declared, len(blob)))

    chunks = []
    offset = 12
    while offset < len(blob):
        if offset + 8 > len(blob):
            raise ValueError("%s: chunk header at %d runs past the end"
                             % (path, offset))
        length, kind = struct.unpack_from("<II", blob, offset)
        start = offset + 8
        if start + length > len(blob):
            raise ValueError(
                "%s: chunk at %d claims %d bytes, %d remain"
                % (path, offset, length, len(blob) - start))
        chunks.append((kind, blob[start:start + length]))
        offset = start + length
    return chunks


def _write_glb_chunks(path, chunks):
    body = b""
    for kind, data in chunks:
        padded = _pad_to_four(data, _PAD.get(kind, b"\x00"))
        body += struct.pack("<II", len(padded), kind) + padded
    header = struct.pack("<4sII", _GLB_MAGIC, _GLB_VERSION, 12 + len(body))
    with open(path, "wb") as handle:
        handle.write(header + body)


def load_document(path):
    """The glTF JSON document, from either container."""
    if is_glb(path):
        for kind, data in read_glb_chunks(path):
            if kind == _CHUNK_JSON:
                return json.loads(data.decode("utf-8"))
        raise ValueError("%s has no JSON chunk" % path)
    if not is_gltf(path):
        raise ValueError("%s is not a .gltf or .glb" % path)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _store_document(path, document):
    if not is_glb(path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"))
        return
    # Rewrite chunk 0 only. Every other chunk -- the BIN payload and its own
    # padding -- is copied through untouched: this module changes one node
    # name, and re-deriving a 141 KB buffer to do it would be all risk.
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    chunks = [(kind, encoded if kind == _CHUNK_JSON else data)
              for kind, data in read_glb_chunks(path)]
    _write_glb_chunks(path, chunks)


def name_mesh_nodes(path, node_name):
    """Give every mesh-bearing node the name `node_name`, in either container.

    Returns the number of nodes renamed. Zero means the file is already right
    and IS NOT REWRITTEN -- restaging an unchanged asset must not re-fingerprint
    it and re-run every downstream Asset Processor job.
    """
    document = load_document(path)

    renamed = 0
    for node in document.get("nodes", []):
        if "mesh" in node and node.get("name") != node_name:
            node["name"] = node_name
            renamed += 1

    if renamed:
        _store_document(path, document)
    return renamed


def mesh_node_count(path):
    """How many mesh-bearing nodes the file has.

    More than one means "name them all the same" is wrong -- the selection
    would be ambiguous -- and the caller should say so rather than produce a
    sidecar that silently picks one.
    """
    document = load_document(path)
    return sum(1 for node in document.get("nodes", []) if "mesh" in node)

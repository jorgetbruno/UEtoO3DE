# Lane B, half 1: parse the FBX binary GlobalSettings that UE's exporter wrote.
# Prints UpAxis/Sign, FrontAxis/Sign, CoordAxis/Sign, UnitScaleFactor, OriginalUnitScaleFactor.
import struct
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"D:/Gamedev/UEtoO3DE/Exports/LaneB/SM_LetterF.fbx"
data = open(path, "rb").read()
assert data[:20] == b"Kaydara FBX Binary  ", "not a binary FBX"


def read_node(off):
    end, nprops, plen = struct.unpack_from("<III", data, off)
    nlen = data[off + 12]
    name = data[off + 13 : off + 13 + nlen].decode("ascii", "replace")
    poff = off + 13 + nlen
    props = []
    for _ in range(nprops):
        t = chr(data[poff]); poff += 1
        if t == "I":
            props.append(struct.unpack_from("<i", data, poff)[0]); poff += 4
        elif t == "D":
            props.append(struct.unpack_from("<d", data, poff)[0]); poff += 8
        elif t == "L":
            props.append(struct.unpack_from("<q", data, poff)[0]); poff += 8
        elif t == "F":
            props.append(struct.unpack_from("<f", data, poff)[0]); poff += 4
        elif t == "S" or t == "R":
            ln = struct.unpack_from("<I", data, poff)[0]; poff += 4
            props.append(data[poff : poff + ln]); poff += ln
        elif t == "Y":
            props.append(struct.unpack_from("<h", data, poff)[0]); poff += 2
        elif t == "C":
            props.append(data[poff]); poff += 1
        else:  # arrays: b,c,d,f,i,l — decode so we can inspect vertex data
            alen, enc, clen = struct.unpack_from("<III", data, poff); poff += 12
            payload = data[poff : poff + clen]; poff += clen
            if enc == 1:
                import zlib
                payload = zlib.decompress(payload)
            fmt = {"b": "b", "c": "b", "i": "i", "f": "f", "d": "d", "l": "q"}.get(t)
            if fmt:
                props.append(list(struct.unpack("<%d%s" % (alen, fmt), payload[: alen * struct.calcsize(fmt)])))
            else:
                props.append("<array %s x%d>" % (t, alen))
    children = []
    coff = poff
    # nested children until end offset (13-byte null record terminator)
    while coff < end - 13:
        child, coff = read_node(coff)
        children.append(child)
    return (name, props, children), end


def walk(node, want, out):
    name, props, children = node
    if name == "P" and props and isinstance(props[0], bytes):
        key = props[0].decode("ascii", "replace")
        if key in want:
            out[key] = props[4:]
    for c in children:
        walk(c, want, out)


want = {"UpAxis", "UpAxisSign", "FrontAxis", "FrontAxisSign", "CoordAxis", "CoordAxisSign",
        "UnitScaleFactor", "OriginalUnitScaleFactor"}
off = 27  # skip 21-byte magic + 2 bytes + version u32
found = {}
verts = [None]
while off < len(data) - 13:
    if all(b == 0 for b in data[off : off + 13]):
        break  # null record = end of top-level node list
    node, off = read_node(off)
    walk(node, want, found)

    def find_arrays(n):
        name, props, children = n
        if name == "Vertices" and props and isinstance(props[0], list):
            vals = props[0]
            xs, ys, zs = vals[0::3], vals[1::3], vals[2::3]
            verts[0] = (len(vals) // 3,
                        (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs)),
                        (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs)))
        for c in children:
            find_arrays(c)
    find_arrays(node)

for k in sorted(found):
    print("%s = %s" % (k, found[k]))
if verts[0]:
    n, xr, yr, zr, cent = verts[0]
    print("FBX stored vertices: %d" % n)
    print("  x [%.3f, %.3f]  y [%.3f, %.3f]  z [%.3f, %.3f]" % (xr + yr + zr))
    print("  centroid (%.3f, %.3f, %.3f)" % cent)

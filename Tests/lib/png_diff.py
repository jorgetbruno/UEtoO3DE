"""
png_diff.py — stdlib-only PNG decode + pixel deltas (M8).

EMotionFX reflects no buses to EditorPythonBindings in 26.05, so the M8
playback acceptance cannot read joint transforms; what it CAN read is frames.
FrameCaptureRequestBus writes real screenshots in the headless editor
(measured, Tests/o3de/probe_m8_emfx.py) and the edit-mode noise floor is
exactly zero, so "the same camera sees different pixels across game-mode
frames" is a robust playback observable -- provided the deltas are computed
per PIXEL, not per byte (PNG compression makes byte-identity useless).

Supports 8-bit grey/RGB/RGBA/grey+alpha, filters 0-4. No interlacing (Atom
does not write interlaced captures).
"""

import struct
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    pass


def decode(path):
    """(width, height, channels, raw bytes) for an 8-bit PNG."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if blob[:8] != PNG_MAGIC:
        raise PngError("not a PNG: " + path)
    pos, width, height, bit_depth, color_type, interlace = 8, 0, 0, 0, 0, 0
    idat = b""
    while pos + 8 <= len(blob):
        length, ctype = struct.unpack_from(">I4s", blob, pos)
        data = blob[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _c, _f, interlace = \
                struct.unpack_from(">IIBBBBB", data)
        elif ctype == b"IDAT":
            idat += data
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if bit_depth != 8:
        raise PngError("bit depth %d unsupported (%s)" % (bit_depth, path))
    if interlace:
        raise PngError("interlaced PNG unsupported (%s)" % path)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise PngError("color type %d unsupported (%s)" % (color_type, path))

    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _y in range(height):
        filt = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filt == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif filt == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif filt == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev[i]
                ul = prev[i - channels] if i >= channels else 0
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                line[i] = (line[i] + pred) & 255
        elif filt != 0:
            raise PngError("filter %d unsupported (%s)" % (filt, path))
        out += line
        prev = line
    return width, height, channels, bytes(out)


def delta(path_a, path_b, channel_threshold=8):
    """{'mean': mean |difference| per byte, 'changed': changed-pixel fraction,
    'size': 'WxHxC'} between two same-shape PNGs."""
    wa, ha, ca, a = decode(path_a)
    wb, hb, cb, b = decode(path_b)
    if (wa, ha, ca) != (wb, hb, cb):
        raise PngError("shape mismatch: %s vs %s"
                       % ((wa, ha, ca), (wb, hb, cb)))
    total = sum(abs(x - y) for x, y in zip(a, b))
    changed = sum(1 for i in range(0, len(a), ca)
                  if any(abs(a[i + c] - b[i + c]) > channel_threshold
                         for c in range(ca)))
    return {"mean": total / float(len(a)),
            "changed": changed / float(wa * ha),
            "size": "%dx%dx%d" % (wa, ha, ca)}

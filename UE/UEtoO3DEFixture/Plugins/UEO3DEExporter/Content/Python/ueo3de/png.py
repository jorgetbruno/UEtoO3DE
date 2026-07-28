"""
png.py — minimal PNG decode, so a texture UE refuses to write as TGA is not lost.

PURE (stdlib only, `zlib`), for the same reason `tga.py` is: the conversion has
to be testable without an editor.

WHY THIS EXISTS. `material_export` writes every texture through an
`AssetExportTask` with a `.tga` filename, and on a real marketplace pack that
stopped the whole export dead:

    LogExporter: Warning: No tga exporter found for Texture2D .../T_Grunge_06_O
    MaterialExportError: texture export failed: .../T_Grunge_06_O

`UTextureExporterTGA::SupportsObject` accepts only some source formats. A probe
over that level's 155 distinct textures (`Tests/ue/probe_texture_export.py`)
measured it exactly: **TGA refused 1, PNG refused 0** -- PNG accepts everything
TGA does and one thing it does not.

The obvious fix -- write the `.png` and point the manifest at it -- does not
work here, and the reason is worth recording. The manifest is written in step 0
of the export and textures in step 3, so by the time a refusal is discovered
the manifest already promises a `.tga`; and the ORM/opacity channel split needs
to READ pixels, which it cannot do from a PNG. So the fallback converts: export
PNG, decode here, write a real TGA at the path the manifest already names.
Everything downstream -- staging, the Atom preset chosen by filename suffix,
the channel split -- is then unchanged and unaware.

Scope is deliberately what UE's PNG exporter emits: bit depth 8 or 16,
non-interlaced, colour types 0/2/3/4/6. Interlacing, 1/2/4-bit depths and
anything else are rejected loudly rather than half-decoded.
"""

import struct
import zlib


class PngError(Exception):
    pass


_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# colour type -> samples per pixel
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _chunks(data):
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if len(body) != length:
            raise PngError("truncated %r chunk" % kind)
        yield kind, body
        offset += 12 + length  # length + type + data + crc


def _unfilter(raw, width, height, bytes_per_pixel, stride):
    """Undo the per-scanline filters. Returns the raw sample bytes."""
    out = bytearray(height * stride)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        if position >= len(raw):
            raise PngError("pixel data ended after %d of %d rows" % (row, height))
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        if len(line) != stride:
            raise PngError("scanline %d is %d bytes, expected %d"
                           % (row, len(line), stride))
        position += stride

        if filter_type == 0:
            pass
        elif filter_type == 1:      # Sub
            for i in range(bytes_per_pixel, stride):
                line[i] = (line[i] + line[i - bytes_per_pixel]) & 0xFF
        elif filter_type == 2:      # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:      # Average
            for i in range(stride):
                left = line[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:      # Paeth
            for i in range(stride):
                left = line[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up = previous[i]
                up_left = previous[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                estimate = left + up - up_left
                da, db, dc = (abs(estimate - left), abs(estimate - up),
                              abs(estimate - up_left))
                if da <= db and da <= dc:
                    predictor = left
                elif db <= dc:
                    predictor = up
                else:
                    predictor = up_left
                line[i] = (line[i] + predictor) & 0xFF
        else:
            raise PngError("unknown scanline filter %d on row %d"
                           % (filter_type, row))

        out[row * stride:(row + 1) * stride] = line
        previous = line
    return bytes(out)


def read(path):
    """Decode to `{'width', 'height', 'pixels'}` with pixels as 8-bit RGBA.

    RGBA regardless of the source colour type, because the only consumer is the
    TGA writer below and one layout means one place to be wrong.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.startswith(_SIGNATURE):
        raise PngError("not a PNG (bad signature): " + path)

    header = None
    palette = None
    transparency = None
    idat = []
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body[:13])
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("no IHDR chunk: " + path)
    if not idat:
        raise PngError("no IDAT chunk: " + path)

    width, height, depth, colour, compression, filter_method, interlace = header
    if compression != 0 or filter_method != 0:
        raise PngError("%s: compression %d / filter method %d unsupported"
                       % (path, compression, filter_method))
    if interlace != 0:
        raise PngError("%s: interlaced PNG unsupported" % path)
    if colour not in _CHANNELS:
        raise PngError("%s: colour type %d unsupported" % (path, colour))
    if depth not in (8, 16):
        raise PngError("%s: bit depth %d unsupported (8 or 16 only)"
                       % (path, depth))
    if colour == 3 and palette is None:
        raise PngError("%s: palette colour type with no PLTE chunk" % path)

    samples = _CHANNELS[colour]
    sample_bytes = depth // 8
    bytes_per_pixel = max(1, samples * sample_bytes)
    stride = width * samples * sample_bytes

    raw = _unfilter(zlib.decompress(b"".join(idat)), width, height,
                    bytes_per_pixel, stride)

    # Normalize to 8-bit RGBA.
    out = bytearray(width * height * 4)
    step = samples * sample_bytes
    for index in range(width * height):
        base = index * step

        def sample(which):
            # 16-bit: take the high byte. Atom's image builder works from 8-bit
            # here either way, and truncating is what UE's own TGA export does.
            return raw[base + which * sample_bytes]

        if colour == 0:            # greyscale
            value = sample(0)
            red = green = blue = value
            alpha = 255
        elif colour == 2:          # RGB
            red, green, blue, alpha = sample(0), sample(1), sample(2), 255
        elif colour == 3:          # palette
            entry = raw[base] * 3
            if entry + 2 >= len(palette):
                raise PngError("%s: palette index out of range" % path)
            red, green, blue = palette[entry], palette[entry + 1], palette[entry + 2]
            alpha = 255
            if transparency is not None and raw[base] < len(transparency):
                alpha = transparency[raw[base]]
        elif colour == 4:          # greyscale + alpha
            value = sample(0)
            red = green = blue = value
            alpha = sample(1)
        else:                      # colour == 6, RGBA
            red, green, blue, alpha = (sample(0), sample(1), sample(2), sample(3))

        position = index * 4
        out[position] = red
        out[position + 1] = green
        out[position + 2] = blue
        out[position + 3] = alpha

    return {"width": width, "height": height, "pixels": bytes(out)}


def to_tga(png_path, tga_path):
    """Decode a PNG and write it as the 32-bit TGA the rest of the pipeline reads.

    Written top-down (descriptor bit 5) and as BGRA, which is what
    `tga.read` expects and what UE's own TGA exporter produces, so a converted
    file is indistinguishable downstream from one UE wrote itself.
    """
    image = read(png_path)
    width, height = image["width"], image["height"]
    source = image["pixels"]

    body = bytearray(len(source))
    for index in range(0, len(source), 4):
        body[index] = source[index + 2]      # B
        body[index + 1] = source[index + 1]  # G
        body[index + 2] = source[index]      # R
        body[index + 3] = source[index + 3]  # A

    header = struct.pack("<BBBHHBHHHHBB",
                         0, 0, 2, 0, 0, 0, 0, 0, width, height, 32,
                         0x20 | 0x08)  # top-down origin, 8 alpha bits
    with open(tga_path, "wb") as handle:
        handle.write(header)
        handle.write(bytes(body))
    return tga_path

"""
tga.py — minimal TGA read/derive for texture export (plan M4).

PURE (stdlib only), because the ORM channel split must be testable without an
editor. Handles exactly what UE's TextureExporterTGA writes -- type 2
(uncompressed true-colour), 24 or 32 bpp, origin flags preserved -- and writes
the two derived forms M4 needs:

  * a grayscale (replicated to 24-bit BGR) TGA from one channel of a source --
    the ORM split: "packed ORM textures are split into separate grayscale
    images at export time -- simpler and more testable than channel-selection
    plumbing in v1" (plan M4);
  * a straight copy (role duplication: the same source texture used as both
    basecolor and something else gets one file per role, because the Atom
    image builder chooses its colour-space preset by FILENAME suffix).

Not a general TGA library; RLE and palettes are out of scope and rejected
loudly.
"""

import struct


class TgaError(Exception):
    pass


def read(path):
    """Return {'width', 'height', 'bpp', 'descriptor', 'pixels'} where pixels
    is bytes in BGR(A) order, row layout as stored (descriptor preserved)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 18:
        raise TgaError("truncated TGA: " + path)
    id_length = data[0]
    color_map_type = data[1]
    image_type = data[2]
    if image_type not in (2, 3) or color_map_type != 0:
        raise TgaError(
            "%s: TGA type %d/colormap %d unsupported (expected uncompressed "
            "true-colour or grayscale)"
            % (path, image_type, color_map_type))
    width = data[12] | data[13] << 8
    height = data[14] | data[15] << 8
    bpp = data[16]
    descriptor = data[17]
    if image_type == 3:
        if bpp != 8:
            raise TgaError("%s: grayscale TGA must be 8 bpp, got %d" % (path, bpp))
    elif bpp not in (24, 32):
        raise TgaError("%s: %d bpp unsupported" % (path, bpp))
    offset = 18 + id_length
    stride = bpp // 8
    expected = width * height * stride
    pixels = data[offset:offset + expected]
    if len(pixels) != expected:
        raise TgaError("%s: pixel data truncated (%d of %d bytes)"
                       % (path, len(pixels), expected))
    return {"width": width, "height": height, "bpp": bpp,
            "descriptor": descriptor, "pixels": pixels}


def _header(width, height, bpp, descriptor):
    return struct.pack("<BBBHHBHHHHBB",
                       0, 0, 2, 0, 0, 0, 0, 0, width, height, bpp, descriptor)


def write_grayscale_from_channel(source_path, output_path, channel):
    """Extract one channel into a 24-bit grayscale TGA. Returns output_path.

    `channel` is 'R', 'G', 'B' or 'A' in the intuitive colour sense; TGA
    stores BGR(A), so the byte index maps accordingly.

    Requesting 'A' from a 24-bit source produces a solid-white image: an RGB
    image's alpha IS 1.0 everywhere by definition (UE writes 24 bpp exactly
    when the texture carries no alpha data), so white is the faithful value,
    not a fallback."""
    image = read(source_path)
    stride = image["bpp"] // 8
    index_by_channel = {"B": 0, "G": 1, "R": 2, "A": 3}
    if channel not in index_by_channel:
        raise TgaError("bad channel %r" % channel)
    index = index_by_channel[channel]

    count = image["width"] * image["height"]
    out = bytearray(count * 3)
    if index >= stride:
        if channel != "A":
            raise TgaError("%s: channel %s requested but image is %d bpp"
                           % (source_path, channel, image["bpp"]))
        out = bytearray(b"\xff") * len(out)
    else:
        # Extended slices run at C speed; the per-pixel loop this replaces was
        # ~50M Python bytecode operations for one 4096x4096 RMA split, and a
        # packed level splits every such texture three times. Byte-identical
        # to the loop (verified against the old implementation on random
        # images before it was deleted).
        values = image["pixels"][index::stride]
        out[0::3] = values
        out[1::3] = values
        out[2::3] = values

    with open(output_path, "wb") as handle:
        handle.write(_header(image["width"], image["height"], 24,
                             image["descriptor"] & 0x2F))
        handle.write(bytes(out))
    return output_path


def write_grayscale(output_path, width, height, rows):
    """Write an 8-bit grayscale TGA from `rows` (bottom-up, values 0..255).

    Used by the M7 terrain bake for the plan's heightmap side artifact. 8-bit
    is a VISUALIZATION, not the source of truth -- the mesh carries the exact
    traced heights; this file exists for the stretch heightfield path and for
    a human eyeballing the terrain."""
    if len(rows) != height or any(len(row) != width for row in rows):
        raise TgaError("rows do not match %dx%d" % (width, height))
    # _header hard-codes image type 2 (truecolor); type 3 is grayscale.
    header = struct.pack("<BBBHHBHHHHBB",
                         0, 0, 3, 0, 0, 0, 0, 0, width, height, 8, 0x00)
    with open(output_path, "wb") as handle:
        handle.write(header)
        for row in rows:
            handle.write(bytes(int(max(0, min(255, value))) for value in row))
    return output_path


def copy(source_path, output_path):
    """Byte copy after validating the source parses as a supported TGA."""
    read(source_path)  # validation only
    with open(source_path, "rb") as src, open(output_path, "wb") as dst:
        dst.write(src.read())
    return output_path

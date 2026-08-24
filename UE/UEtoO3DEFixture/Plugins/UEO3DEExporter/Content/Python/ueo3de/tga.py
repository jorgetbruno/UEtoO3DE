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
import zlib


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


def write_channel_png(source_path, output_path, channel):
    """Extract one channel of a TGA into an 8-BIT GRAYSCALE PNG.

    Why PNG, and why not any TGA:

      * O3DE's ImageBuilder REJECTS grayscale TGA outright -- "TgaLoader:
        unsupported type code [3] ... Only support RGB(RLE) or color mapped"
        (measured: four probes, four instant failures). A grayscale-TGA
        writer here would be a trap for its next caller, so none exists.
      * The previous writer replicated the channel into 24-bit RGB TGA: one
        4096 packed-map split was 50.3 MB carrying 16.8 MB of data three
        times over. The same channel as grayscale PNG measured 9.8 MB and
        produced a full mipchain + streamingimage with zero AP errors --
        5.1x smaller, and a packed level splits every such texture three
        times.

    The image-builder PRESET still comes from the filename suffix
    (`_roughness`, `_metallic`, `_ao`), which is extension-independent.

    `channel` is 'R', 'G', 'B' or 'A' in the intuitive colour sense; TGA
    stores BGR(A), so the byte index maps accordingly. Requesting 'A' from a
    24-bit source produces solid white: an RGB image's alpha IS 1.0
    everywhere by definition (UE writes 24 bpp exactly when the texture
    carries no alpha), so white is the faithful value, not a fallback.
    """
    image = read(source_path)
    stride = image["bpp"] // 8
    index_by_channel = {"B": 0, "G": 1, "R": 2, "A": 3}
    if channel not in index_by_channel:
        raise TgaError("bad channel %r" % channel)
    index = index_by_channel[channel]

    width, height = image["width"], image["height"]
    if index >= stride:
        if channel != "A":
            raise TgaError("%s: channel %s requested but image is %d bpp"
                           % (source_path, channel, image["bpp"]))
        gray = b"\xff" * (width * height)
    else:
        gray = bytes(image["pixels"][index::stride])

    # TGA rows are bottom-up unless descriptor bit 5 says otherwise; PNG is
    # strictly top-down. Getting this wrong flips every split vertically
    # against its basecolor, which shares UVs with it.
    rows = [gray[y * width:(y + 1) * width] for y in range(height)]
    if not (image["descriptor"] & 0x20):
        rows = rows[::-1]
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    with open(output_path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", struct.pack(
            ">IIBBBBB", width, height, 8, 0, 0, 0, 0)))
        handle.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        handle.write(chunk(b"IEND", b""))
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

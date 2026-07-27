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
    if image_type != 2 or color_map_type != 0:
        raise TgaError(
            "%s: TGA type %d/colormap %d unsupported (expected uncompressed "
            "true-colour, which is what UE's exporter writes)"
            % (path, image_type, color_map_type))
    width = data[12] | data[13] << 8
    height = data[14] | data[15] << 8
    bpp = data[16]
    descriptor = data[17]
    if bpp not in (24, 32):
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
        for byte in range(len(out)):
            out[byte] = 255
    else:
        pixels = image["pixels"]
        for pixel in range(count):
            value = pixels[pixel * stride + index]
            base = pixel * 3
            out[base] = value
            out[base + 1] = value
            out[base + 2] = value

    with open(output_path, "wb") as handle:
        handle.write(_header(image["width"], image["height"], 24,
                             image["descriptor"] & 0x2F))
        handle.write(bytes(out))
    return output_path


def copy(source_path, output_path):
    """Byte copy after validating the source parses as a supported TGA."""
    read(source_path)  # validation only
    with open(source_path, "rb") as src, open(output_path, "wb") as dst:
        dst.write(src.read())
    return output_path

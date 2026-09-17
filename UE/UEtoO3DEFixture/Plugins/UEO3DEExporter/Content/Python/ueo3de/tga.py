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


def _to_linear(s):
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def _to_srgb(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1.0 / 2.4) - 0.055


def tint_table(factor, srgb):
    """256-entry lookup: byte -> byte after multiplying by `factor` in LINEAR space.

    UE samples an sRGB texture to linear, multiplies, and clamps base colour to
    [0, 1]; the table does exactly that per channel, then re-encodes.
    """
    table = bytearray(256)
    for value in range(256):
        s = value / 255.0
        linear = (_to_linear(s) if srgb else s) * factor
        linear = min(max(linear, 0.0), 1.0)
        out = _to_srgb(linear) if srgb else linear
        table[value] = int(round(min(max(out, 0.0), 1.0) * 255.0))
    return bytes(table)


def write_tinted(source_path, output_path, tint, srgb):
    """Copy a TGA with linear [r, g, b] multiplied into its colour channels.

    Alpha, size, bit depth and row order are preserved.
    """
    image = read(source_path)
    if image["bpp"] not in (24, 32):
        raise TgaError("%s: tint needs a colour TGA, got %d bpp"
                       % (source_path, image["bpp"]))
    stride = image["bpp"] // 8
    pixels = bytearray(image["pixels"])
    for index, factor in ((2, tint[0]), (1, tint[1]), (0, tint[2])):   # BGR order
        pixels[index::stride] = bytes(pixels[index::stride]).translate(
            tint_table(float(factor), srgb))
    with open(output_path, "wb") as handle:
        handle.write(_header(image["width"], image["height"], image["bpp"],
                             image["descriptor"]))
        handle.write(bytes(pixels))
    return output_path


# Alpha at or below this is "not visible" for dilation: such texels are FILLED
# from their neighbours and never used as a colour source. NOT zero, and not a
# hair above it. Measured on a marketplace decal pack (T_Paper04): its mask
# averages 3.7 with 97.9% of texels under 10, and the artwork's RGB is black
# wherever alpha is under ~32 (mean max-channel 0.0 below alpha 8, 1.9 at 8-31,
# 37.2 at 32-127, 128.5 above). Seeding from anything below 32 therefore floods
# the transparent region with the black that is the problem.
DILATE_ALPHA_FLOOR = 32


def dilate_rgb(width, height, pixels, stride, floor=DILATE_ALPHA_FLOOR):
    """Flood each transparent texel with its nearest visible colour, in place.

    RGB under alpha 0 is never seen directly, but BC7 blocks and every mip
    level average it with visible neighbours, so black there rings each decal
    with a dark halo as it shrinks on screen. A breadth-first spread from the
    visible texels costs one pass over the image and nothing at runtime.
    `pixels` is BGRA, `stride` 4.
    """
    from collections import deque

    alpha = stride - 1
    visited = bytearray(width * height)
    frontier = deque()
    for index in range(width * height):
        if pixels[index * stride + alpha] > floor:
            visited[index] = 1
            frontier.append(index)
    if not frontier or len(frontier) == width * height:
        return pixels
    while frontier:
        index = frontier.popleft()
        x, y = index % width, index // width
        source = index * stride
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            neighbour = ny * width + nx
            if visited[neighbour]:
                continue
            visited[neighbour] = 1
            target = neighbour * stride
            pixels[target:target + 3] = pixels[source:source + 3]
            frontier.append(neighbour)
    return pixels


def write_with_alpha(color_path, alpha_path, output_path, channel="R", dilate=True):
    """Write a 32-bit TGA: RGB from `color_path`, alpha from `alpha_path`'s channel.

    O3DE's decal shader takes a decal's opacity from the BASE COLOUR's alpha
    (`Atom/Features/PBR/Decals.azsli`: `baseMap.a * m_opacity * attenuation`)
    and reads no opacity map at all, so a UE decal's mask has to arrive inside
    the base colour or the whole projector box draws opaque.
    """
    color = read(color_path)
    mask = read(alpha_path)
    if (color["width"], color["height"]) != (mask["width"], mask["height"]):
        raise TgaError("%s is %dx%d but its mask %s is %dx%d"
                       % (color_path, color["width"], color["height"],
                          alpha_path, mask["width"], mask["height"]))
    index_by_channel = {"B": 0, "G": 1, "R": 2, "A": 3}
    if channel not in index_by_channel:
        raise TgaError("bad channel %r" % channel)

    width, height = color["width"], color["height"]
    color_stride = color["bpp"] // 8
    mask_stride = mask["bpp"] // 8
    mask_index = index_by_channel[channel]
    if mask["bpp"] == 8:                      # grayscale mask: one byte per texel
        mask_index = 0
    elif mask_index >= mask_stride:
        raise TgaError("%s has no %s channel (%d bpp)" % (alpha_path, channel, mask["bpp"]))
    # Row order is per file; the mask is flipped to match the colour's.
    flip = bool(color["descriptor"] & 0x20) != bool(mask["descriptor"] & 0x20)

    out = bytearray(width * height * 4)
    source = color["pixels"]
    mask_pixels = mask["pixels"]
    for y in range(height):
        mask_y = (height - 1 - y) if flip else y
        for x in range(width):
            index = y * width + x
            src = index * color_stride
            out[index * 4:index * 4 + 3] = source[src:src + 3]
            out[index * 4 + 3] = mask_pixels[(mask_y * width + x) * mask_stride + mask_index]
    if dilate:
        dilate_rgb(width, height, out, 4)
    descriptor = (color["descriptor"] & 0x20) | 0x08    # 8 attribute (alpha) bits
    with open(output_path, "wb") as handle:
        handle.write(_header(width, height, 32, descriptor))
        handle.write(bytes(out))
    return output_path


def copy(source_path, output_path):
    """Byte copy after validating the source parses as a supported TGA."""
    read(source_path)  # validation only
    with open(source_path, "rb") as src, open(output_path, "wb") as dst:
        dst.write(src.read())
    return output_path

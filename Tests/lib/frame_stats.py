"""frame_stats.py -- is a rendered frame USABLE, not just present?

THE GAP THIS CLOSES. An imported level rendered PURE WHITE and every check in
the suite passed: 905 entities, 693/693 colliders verified, 140/140 cooked
physics meshes, 50/50 colliding in the running world, 0 Asset Processor
errors. Two UE post-process volumes had carried `auto_exposure_bias` 12.0 and
9.5 into Atom's `Manual Compensation`, a property in EV stops -- a 4096x
multiply, stacked.

Everything the suite knew how to ask was "was this AUTHORED?". Nothing asked
"can you SEE anything?", and no amount of structural assertion would have.

The measure here is deliberately crude, because a crude measure that runs is
worth more than a perceptual one that does not:

  * mean luminance   -- a blown frame sits at ~1.0, a black one at ~0.0
  * clipped fraction -- how much of the frame is pinned at pure white or
                        pure black; a frame can average mid-grey and still be
                        half blown
  * dynamic range    -- a frame with almost no spread has no image in it,
                        whatever its average

It is NOT a golden-image comparison. Those need a reference per level, break
on every driver change, and would not have caught this either -- there was no
reference for `Demonstration`. This asks the one question that has an answer
independent of content: is there a picture here at all?
"""

import os
import sys

_LIB = os.path.dirname(os.path.abspath(__file__))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import png_diff  # noqa: E402

# Rec. 709 luma weights; the frame is sRGB-ish 8-bit, and weighting by
# perceived brightness beats averaging raw channels for "is this white".
_R, _G, _B = 0.2126, 0.7152, 0.0722

# A pixel at or beyond these is clipped -- no detail can survive there.
WHITE_CLIP = 250
BLACK_CLIP = 5


def frame_stats(path, sample_step=4):
    """{'mean', 'white_clipped', 'black_clipped', 'p05', 'p95', 'range'}.

    Luminance values are 0..1. `sample_step` skips pixels for speed: a 2771 x
    972 frame is 2.7 M pixels and this is pure Python, but the statistics of
    "is the image blown out" do not need every pixel.
    """
    width, height, channels, raw = png_diff.decode(path)
    if channels < 3:
        raise ValueError("%s has %d channels; need RGB" % (path, channels))

    histogram = [0] * 256
    total = 0
    step = max(1, int(sample_step)) * channels
    for offset in range(0, len(raw) - channels + 1, step):
        luma = int(_R * raw[offset] + _G * raw[offset + 1] + _B * raw[offset + 2])
        histogram[min(255, max(0, luma))] += 1
        total += 1
    if not total:
        raise ValueError("no pixels sampled from " + path)

    mean = sum(value * count for value, count in enumerate(histogram)) / float(total)
    white = sum(histogram[WHITE_CLIP:]) / float(total)
    black = sum(histogram[:BLACK_CLIP + 1]) / float(total)

    def percentile(fraction):
        target = fraction * total
        seen = 0
        for value, count in enumerate(histogram):
            seen += count
            if seen >= target:
                return value
        return 255

    p05, p95 = percentile(0.05), percentile(0.95)
    return {
        "width": width, "height": height, "sampled": total,
        "mean": mean / 255.0,
        "white_clipped": white,
        "black_clipped": black,
        "p05": p05 / 255.0, "p95": p95 / 255.0,
        "range": (p95 - p05) / 255.0,
    }


def verdict(stats, max_clipped=0.50, min_range=0.05):
    """None if the frame is usable, else why it is not.

    CLIPPING ALONE IS THE VERDICT, and that is a correction. The first version
    required heavy clipping AND a flat histogram, and it PASSED the very frame
    it was written for: the blown `Demonstration` capture was 72% pure white
    but still had a 0.745 spread, because the surviving fragments span the
    range. Requiring both conditions made the check assert almost nothing.

    Measured on this build, same capture path, back to back:

        control (empty default level)   0.0% white clipped, mean 0.50
        blown import (bias 12.0 + 9.5) 72.0% white clipped, mean 0.86

    Half the frame at or beyond 250/255 has lost its information, and Atom
    tonemaps -- a correctly exposed outdoor scene does not clip half its
    pixels to pure white, however bright the sky looks. The threshold is set
    at 50%, comfortably between the two measurements above rather than tight
    against either.

    This is NOT a quality bar: a dim night scene and a bright desert must both
    pass. It catches frames with no picture in them.
    """
    if stats["white_clipped"] > max_clipped:
        return ("%.0f%% of the frame is clipped to WHITE (mean luminance "
                "%.3f) -- half the picture or more carries no information. "
                "An exposure or a light is orders of magnitude too bright."
                % (stats["white_clipped"] * 100.0, stats["mean"]))
    if stats["black_clipped"] > max_clipped:
        return ("%.0f%% of the frame is clipped to BLACK (mean luminance "
                "%.3f) -- nothing is lit, or nothing is being drawn."
                % (stats["black_clipped"] * 100.0, stats["mean"]))
    if stats["range"] < min_range:
        return ("the 5-95%% luminance spread is %.3f (mean %.3f) -- the frame "
                "is a flat field with no detail in it"
                % (stats["range"], stats["mean"]))
    return None

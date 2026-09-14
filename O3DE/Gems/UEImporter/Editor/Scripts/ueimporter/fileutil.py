"""
fileutil.py — staging writes that leave unchanged files alone.

The Asset Processor decides what to recook from source files on disk, and a
file rewritten with identical bytes still gets a new timestamp. Staging used to
copy and rewrite every file of a manifest on every run, so re-exporting ONLY a
level's materials (NYC1950: 383 materials, against 2,272 meshes and 1,269
textures) still marked everything changed and turned a material fix into a
full cold-sized recook. These helpers write only when the bytes differ.
"""

import os
import shutil

_CHUNK = 1 << 20


def _same_bytes(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
    except OSError:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            left = fa.read(_CHUNK)
            right = fb.read(_CHUNK)
            if left != right:
                return False
            if not left:
                return True


def copy_if_changed(source, target):
    """Copy `source` over `target` unless they already match; True if copied."""
    if os.path.exists(target) and _same_bytes(source, target):
        return False
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def write_if_changed(path, text):
    """Write `text` to `path` unless it already holds exactly that; True if written.

    Newlines are written the way text mode always wrote them here (os.linesep):
    staging's files were written with open(path, "w"), and a writer that
    switched to bare \\n would "change" every existing sidecar and material
    once -- the full recook this exists to avoid.
    """
    data = text.replace("\n", os.linesep).encode("utf-8")
    try:
        with open(path, "rb") as handle:
            if handle.read() == data:
                return False
    except OSError:
        pass
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return True

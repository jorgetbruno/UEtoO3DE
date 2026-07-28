"""
naming.py — deterministic asset identity: stable GUIDs and sanitized paths.

PURE. No `unreal` import, so the validator and unit tests can re-derive every
identity in the manifest without an editor.

--------------------------------------------------------------------------
GUIDs (plan global constraint 7)
--------------------------------------------------------------------------
"every asset entry carries a stable GUID (derived from the UE package GUID /
path hash)". UE5 removed per-package GUIDs -- verified on 5.8: `AssetData`
exposes `package_name`, `package_path`, `asset_name` and `asset_class_path`,
and `get_editor_property('package_guid')` fails outright. So the path hash is
the only option, and it is the better one anyway: it is a pure function of the
level's content, so a rebuilt fixture reproduces the same manifest and the
M1 golden file keeps working.

GUIDs are UUIDv5 (SHA-1 based, deterministic, no wall clock, no randomness)
over the UE package path in a fixed project namespace.

--------------------------------------------------------------------------
Path sanitization
--------------------------------------------------------------------------
`/Game/Foo/SM_Bar.SM_Bar` -> `uetoo3de/game/foo/sm_bar`.

    1. reduce an object path to its package path (drop the `.Object` suffix)
    2. drop the leading slash, lowercase everything -- the Asset Processor
       lowercases product paths (observed in S0.1:
       `objects/_primitives/_box_1x1.fbx.azmodel`), so emitting anything else
       invites a case mismatch that only shows up on a case-sensitive host
    3. per segment: map every character outside [a-z0-9_.-] to `_`, collapse
       runs of `_`, strip leading/trailing `_` and `.`
    4. suffix Windows reserved device names (con, nul, com1, ...) so the file
       can actually be created
    5. prefix the importer root `uetoo3de/`

The rule is lossy by construction -- `/Game/A-B/X` and `/Game/A_B/X` both land
on `game/a_b/x`. That is why `PathRegistry` exists: a second UE path mapping
onto a path already claimed by a different one is an ERROR that aborts the
export, never a silent overwrite (plan M1).
"""

import uuid

# Fixed, reproducible namespace: uuid5 of the project URL under NAMESPACE_URL.
# Written as a literal so it can be verified by hand and can never drift.
PROJECT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
                               "https://github.com/jorgetbruno/UEtoO3DE")

IMPORTER_ROOT = "uetoo3de"

# NO DOT. A sanitized stem never legitimately contains one (package_path has
# already dropped the `.Object` suffix), and the Asset Processor derives its
# PRODUCT names from the source filename's pre-first-dot stem: a stem with a
# dot in it makes two distinct sources fight over one product, which AP
# rejects outright ("another source has already produced the same product").
# Fragment keys carry a full actor object path, so dots reach here for real.
_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_-")

# Reserved on Windows regardless of extension; a file named `con.fbx` cannot
# be created, and the exporter must not depend on the host OS.
_RESERVED = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}


def package_path(ue_path):
    """`/Game/Meshes/SM_LetterF.SM_LetterF` -> `/Game/Meshes/SM_LetterF`.

    A `#fragment` key is returned WHOLE. Those keys (`#mx` M4.5, `#terrain`
    M7, `#spline` M9) are already explicit identities, and two of them are
    built from an ACTOR object path -- which always contains a dot
    (`/Game/Maps/M.M:PersistentLevel.Actor_1:SplineMesh`). Truncating at the
    first dot threw away the actor AND the component, collapsing every
    spline bake and the landscape of one level onto a single guid and a
    single staged FBX: the second spline silently rendered the first's
    geometry, and a level with both a spline and a terrain had them
    overwrite each other's file. `#mx` keys are unaffected either way --
    they are built from an already-truncated package path, so there is no
    dot left to cut.
    """
    ue_path = str(ue_path).strip()
    if "#" in ue_path:
        return ue_path
    head, sep, _tail = ue_path.partition(".")
    return head if sep else ue_path


def asset_guid(ue_path):
    """Stable GUID for a UE asset, keyed on its package path."""
    return str(uuid.uuid5(PROJECT_NAMESPACE, package_path(ue_path)))


def entity_id(actor_path_name):
    """Stable id for a level actor, keyed on its full object path.

    `/Game/Maps/Fixture_01.Fixture_01:PersistentLevel.TriggerBox_0` is stable
    across re-exports as long as the actor is not deleted and recreated; the
    actor *label* is not (users rename freely). M10's incremental re-import
    may upgrade this to UE's own actor instance GUID.
    """
    return str(uuid.uuid5(PROJECT_NAMESPACE, str(actor_path_name)))


def _sanitize_segment(segment):
    out = []
    previous_underscore = False
    for char in segment.lower():
        if char in _ALLOWED:
            out.append(char)
            previous_underscore = char == "_"
        elif not previous_underscore:
            out.append("_")
            previous_underscore = True
    text = "".join(out).strip("_.")
    if not text:
        text = "_"
    if text in _RESERVED:
        text = text + "_"
    return text


def sanitize_path(ue_path):
    """UE package/object path -> O3DE-relative path stem (no extension)."""
    segments = [s for s in package_path(ue_path).split("/") if s]
    if not segments:
        raise ValueError("cannot sanitize empty UE path: " + repr(ue_path))
    return IMPORTER_ROOT + "/" + "/".join(_sanitize_segment(s) for s in segments)


def empty_slot_label(index):
    """The material name a NULL mesh-asset slot gets in the baked FBX.

    UE's FBX exporter DROPS a null-material slot outright -- the model then
    has fewer slots than the manifest and an actor's override of that slot
    can never be assigned (measured: temple-roof undersides). The bake
    substitutes a placeholder material with this name, and ue_level records
    the same name in `material_slot_material_names`, so the label round-trips
    like any real material's. Keyed by the ORIGINAL slot index."""
    return "UEO3DE_Slot%d" % index


def with_extension(stem, extension):
    """`uetoo3de/game/meshes/sm_letterf` + `fbx` -> `.../sm_letterf.fbx`."""
    return stem + "." + extension.lstrip(".")


class PathRegistry:
    """Claims sanitized paths and refuses to let two UE assets share one."""

    def __init__(self):
        self._claims = {}       # sanitized stem -> UE package path

    def claim(self, ue_path):
        """Return the sanitized stem, or raise if a different asset owns it."""
        package = package_path(ue_path)
        stem = sanitize_path(package)
        owner = self._claims.get(stem)
        if owner is None:
            self._claims[stem] = package
            return stem
        if owner != package:
            raise PathCollisionError(stem, owner, package)
        return stem

    def claims(self):
        return dict(self._claims)


class PathCollisionError(Exception):
    """Two distinct UE assets sanitize onto the same O3DE-relative path."""

    def __init__(self, stem, first, second):
        super().__init__(
            "path collision on '%s': '%s' and '%s' sanitize to the same path"
            % (stem, first, second))
        self.stem = stem
        self.first = first
        self.second = second

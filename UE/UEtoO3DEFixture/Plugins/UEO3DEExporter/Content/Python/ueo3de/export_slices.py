"""
export_slices.py — which editor exports which mesh, when the export runs wide.

Pure: no `unreal`. Shared by the lead export (Tests/ue/export_level.py), its
worker editors (Tests/ue/export_mesh_worker.py) and the tests.

The mesh export is serial on the editor's one Python thread: NYC_Level_WC's
2,272 meshes took about two and a half hours. Worker editors split the meshes
that load as standalone assets. Everything bound to the OPEN LEVEL -- spline
bakes read their component out of the level, terrain samples its collision --
stays with the lead, which is the only editor that has the level loaded. That
split is also what makes workers cheap: an editor on an empty map measured
under 3 GB against the lead's 8-13 GB with the city open.
"""

import os

LEVEL_BOUND_FRAGMENTS = ("spline", "terrain")
MAX_WORKERS = 8


class SliceError(Exception):
    pass


def mesh_worker_count(environ=None):
    """UEO3DE_MESH_WORKERS -> worker editors for standalone meshes (default 1).

    1 is the old single-editor export. A garbage value raises: a typo must not
    quietly run a three-hour export serially.
    """
    environ = os.environ if environ is None else environ
    raw = str(environ.get("UEO3DE_MESH_WORKERS", "")).strip()
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        raise SliceError("UEO3DE_MESH_WORKERS=%r is not a whole number" % raw)
    if not 1 <= value <= MAX_WORKERS:
        raise SliceError("UEO3DE_MESH_WORKERS=%r must be between 1 and %d"
                         % (raw, MAX_WORKERS))
    return value


def spline_worker_count(environ=None):
    """UEO3DE_SPLINE_WORKERS -> worker editors that open the LEVEL and take spline bakes.

    Default 0: the lead bakes every spline itself, after its texture export --
    measured on NYC1950 as the long pole of a wide export (7.7 min of
    textures, then 9.5 min of 1,803 splines while three mesh workers finished
    at minute 10 and sat idle). A spline worker opens the level headless
    (~10 s, 6.1 GB measured) and bakes its share while the lead exports
    textures. The lead keeps only terrain, whose bake traces the physics scene.
    """
    environ = os.environ if environ is None else environ
    raw = str(environ.get("UEO3DE_SPLINE_WORKERS", "")).strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise SliceError("UEO3DE_SPLINE_WORKERS=%r is not a whole number" % raw)
    if not 0 <= value <= MAX_WORKERS:
        raise SliceError("UEO3DE_SPLINE_WORKERS=%r must be between 0 and %d"
                         % (raw, MAX_WORKERS))
    return value


def _fragment(asset):
    return asset.get("ue_path", "").partition("#")[2]


def spline_worker_meshes(assets, index, spline_workers):
    """The spline bakes spline worker `index` (0-based) of `spline_workers` takes."""
    if not 0 <= index < spline_workers:
        raise SliceError("spline worker %d of %d does not exist" % (index, spline_workers))
    splines = [a for a in static_meshes(assets) if _fragment(a) == "spline"]
    return splines[index::spline_workers]


def is_level_bound(asset):
    """A bake that reads the open level (spline component, terrain actor)."""
    fragment = asset.get("ue_path", "").partition("#")[2]
    return fragment in LEVEL_BOUND_FRAGMENTS


def static_meshes(assets):
    return [a for a in assets if a.get("kind") == "static_mesh"]


def lead_meshes(assets, workers, spline_workers=0):
    """The static meshes the LEAD exports.

    All of them when nothing runs wide; only the level-bound ones when mesh
    workers take the standalone meshes; and of those, only terrain when spline
    workers take the splines.
    """
    meshes = static_meshes(assets)
    if workers <= 1 and spline_workers <= 0:
        return meshes
    keep = []
    for asset in meshes:
        fragment = _fragment(asset)
        if fragment == "terrain":
            keep.append(asset)
        elif fragment == "spline" and spline_workers <= 0:
            keep.append(asset)
        elif not is_level_bound(asset) and workers <= 1:
            keep.append(asset)
    return keep


def worker_meshes(assets, index, workers):
    """The standalone meshes worker `index` (0-based) of `workers` exports.

    Striped in manifest order rather than cut into runs: a manifest groups
    similar meshes (a pack's buildings sit together), so contiguous runs would
    hand one worker every heavy building. Striping spreads them.
    """
    if not 0 <= index < workers:
        raise SliceError("worker %d of %d does not exist" % (index, workers))
    standalone = [a for a in static_meshes(assets) if not is_level_bound(a)]
    return standalone[index::workers]


def merge_records(assets, record_sets):
    """Join the lead's and the workers' records; every mesh exactly once.

    A mesh in no record set is a hole in the level; a mesh in two is a worker
    that exported outside its slice. Both raise, naming the meshes.
    """
    expected = {a["guid"] for a in static_meshes(assets)}
    seen = {}
    merged = []
    for owner, records in record_sets:
        for record in records:
            guid = record.get("guid")
            if guid in seen:
                raise SliceError("mesh %s exported by both %s and %s"
                                 % (record.get("relative_path"), seen[guid], owner))
            seen[guid] = owner
            merged.append(record)
    missing = expected - set(seen)
    unexpected = set(seen) - expected
    if missing or unexpected:
        by_guid = {a["guid"]: a.get("o3de_relative_path") for a in static_meshes(assets)}
        raise SliceError(
            "%d meshes exported by nobody (e.g. %s); %d exported that the "
            "manifest does not list"
            % (len(missing), sorted(by_guid.get(g) for g in missing)[:5], len(unexpected)))
    return merged


def reuse_requested(environ=None):
    """UEO3DE_REUSE_MESHES=1 -> keep the previous export's static mesh FBX files.

    For re-exporting what is NOT geometry -- materials, textures, the manifest
    -- without re-baking every mesh: NYC1950's 2,272 meshes are most of an
    export, and a material fix touched none of them. Anything but "", "0" or
    "1" raises.
    """
    environ = os.environ if environ is None else environ
    raw = str(environ.get("UEO3DE_REUSE_MESHES", "")).strip()
    if raw not in ("", "0", "1"):
        raise SliceError("UEO3DE_REUSE_MESHES=%r must be 0 or 1" % raw)
    return raw == "1"


def reuse_mesh_records(assets, previous_records, assets_root):
    """The previous export's mesh records, proven to still cover this manifest.

    Every static mesh the NEW manifest lists must have a record and its file
    on disk; a mesh added to the level since the last export, or a file
    deleted since, raises rather than exporting a level with a hole in it.
    """
    wanted = {a["guid"] for a in static_meshes(assets)}
    kept = [r for r in previous_records if r.get("guid") in wanted]
    merged = merge_records(assets, [("previous export", kept)])
    missing = [r.get("relative_path") for r in merged
               if not os.path.exists(os.path.join(assets_root, r.get("relative_path", "")))]
    if missing:
        raise SliceError("%d reused meshes are no longer on disk (e.g. %s); "
                         "re-export the meshes" % (len(missing), missing[:5]))
    return merged


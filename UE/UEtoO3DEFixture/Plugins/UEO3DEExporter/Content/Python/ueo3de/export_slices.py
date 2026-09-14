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


def is_level_bound(asset):
    """A bake that reads the open level (spline component, terrain actor)."""
    fragment = asset.get("ue_path", "").partition("#")[2]
    return fragment in LEVEL_BOUND_FRAGMENTS


def static_meshes(assets):
    return [a for a in assets if a.get("kind") == "static_mesh"]


def lead_meshes(assets, workers):
    """The static meshes the LEAD exports: all of them, or the level-bound ones."""
    meshes = static_meshes(assets)
    if workers <= 1:
        return meshes
    return [a for a in meshes if is_level_bound(a)]


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

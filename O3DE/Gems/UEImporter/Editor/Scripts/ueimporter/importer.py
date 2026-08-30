"""
importer.py — orchestration for the O3DE side of the import (plan M2).

Two entry points, because they run in different processes:

  `stage_only`  pure file I/O -- copies the exported FBX files into the project
                and writes their `.assetinfo` sidecars. Needs no editor, so CI
                can run it, then run AssetProcessorBatch to completion, then
                start the editor. Determinism instead of a race.

  `import_level` the editor half -- waits for every product asset it is about
                to reference (constraint 8), creates the entities, and saves
                the prefab.

`import_level` calls `wait_for_asset` even when CI has already run AP to
completion. That is the point: the barrier has to be in the code path that
references the asset, not in the shell script that usually happens to run
first, or M10's interactive import (where AP is live and lagging) has no
protection at all.
"""

import os

from . import manifest_io
from . import staging
from .report import Report


# Measured on a 44,504-entity marketplace city: 4,000 entities import in 126 s
# at a 4.8 GB peak, and 12,000 kills the editor during `saving prefab` with no
# assert, no log line and exit 0xC0000409. The ceiling is deliberately the
# largest size MEASURED to work rather than the smallest measured to fail --
# the gap between them is unexplored, and guessing into it is how someone loses
# a twenty-minute import to a silent process death.
CHUNK_CEILING = 4000


def chunk_ceiling(environ=None):
    environ = os.environ if environ is None else environ
    raw = str(environ.get("UEO3DE_CHUNK_CEILING", "")).strip()
    if not raw:
        return CHUNK_CEILING
    value = int(raw)          # a garbage ceiling must raise, never fall back
    if value < 1:
        raise ValueError("UEO3DE_CHUNK_CEILING must be >= 1, got %r" % raw)
    return value


CHUNK_ORDERS = ("size", "spatial")


def chunk_order(environ=None):
    """How `chunk_of` assigns subtrees to chunks: `size` or `spatial`.

    `size` (default) packs largest-first onto the emptiest bin: even chunks,
    but position never enters into it, so on a level of singleton actors each
    chunk is every n-th building spread over the whole map -- loading one
    part shows a sieve, and finishing one street needs all of them.
    `spatial` walks the roots along a Hilbert curve over the level's XY extent
    and cuts the walk into `count` contiguous runs, so each chunk is a compact
    patch of the map. Opt-in, because it changes which entities land in which
    chunk, and a chunk that moved between runs would make re-import
    meaningless.
    """
    environ = os.environ if environ is None else environ
    raw = str(environ.get("UEO3DE_CHUNK_ORDER", "")).strip().lower()
    if not raw:
        return CHUNK_ORDERS[0]
    if raw not in CHUNK_ORDERS:   # a garbage order must raise, never fall back
        raise ValueError("UEO3DE_CHUNK_ORDER must be one of %s, got %r"
                         % ("|".join(CHUNK_ORDERS), raw))
    return raw


def _hilbert_index(x, y, order):
    """Position of integer cell (x, y) along a Hilbert curve with 2**order
    cells per side. Neighbouring indices are neighbouring cells, which is the
    whole point: a contiguous run of indices is a compact patch of the map."""
    index = 0
    side = 1 << (order - 1)
    while side > 0:
        rx = 1 if (x & side) else 0
        ry = 1 if (y & side) else 0
        index += side * side * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = side - 1 - x
                y = side - 1 - y
            x, y = y, x
        side >>= 1
    return index


def _world_xy(entity):
    """An entity's world XY, or None when the manifest carries no transform
    (test documents, transform-only placeholders)."""
    transform = entity.get("transform") or {}
    for space in ("world", "local"):
        translation = (transform.get(space) or {}).get("translation")
        if translation and len(translation) >= 2:
            return float(translation[0]), float(translation[1])
    return None


def _spatial_keys(groups):
    """One Hilbert key per group, from the centroid of the group's positioned
    entities. A group with no position keys at the level's minimum corner."""
    centroids = []
    for group in groups:
        points = [xy for xy in (_world_xy(e) for e in group) if xy is not None]
        if points:
            centroids.append((sum(p[0] for p in points) / len(points),
                              sum(p[1] for p in points) / len(points)))
        else:
            centroids.append(None)
    known = [c for c in centroids if c is not None]
    if not known:
        return [0] * len(groups)
    min_x = min(c[0] for c in known)
    min_y = min(c[1] for c in known)
    extent = max(max(c[0] for c in known) - min_x,
                 max(c[1] for c in known) - min_y, 1e-6)
    order = 16
    cells = (1 << order) - 1
    keys = []
    for c in centroids:
        if c is None:
            keys.append(0)
            continue
        cx = int((c[0] - min_x) / extent * cells)
        cy = int((c[1] - min_y) / extent * cells)
        keys.append(_hilbert_index(cx, cy, order))
    return keys


def recommended_chunks(entity_count, ceiling=None):
    """How many chunks this manifest needs; 1 when it fits."""
    ceiling = chunk_ceiling() if ceiling is None else ceiling
    if entity_count <= ceiling:
        return 1
    return (entity_count + ceiling - 1) // ceiling


def chunk_guard_message(entity_count, chunks, ceiling):
    """What to tell someone whose level cannot arrive as one prefab."""
    return (
        "this manifest has %d entities and the measured ceiling for a single "
        "import is %d: at roughly three times that the editor dies during "
        "`saving prefab` with no assert and no log line, so importing anyway "
        "would cost the whole run and produce nothing to debug. Import it as "
        "%d chunks instead -- each writes its own prefab, split by whole "
        "subtrees so no entity is separated from its parent:\n%s\n"
        "Set UEO3DE_CHUNK=1/1 to import it as one prefab anyway, or raise "
        "UEO3DE_CHUNK_CEILING if a larger import has been measured to work "
        "on this machine. UEO3DE_CHUNK_ORDER=spatial makes each chunk a "
        "compact patch of the map instead of every n-th actor."
        % (entity_count, ceiling, chunks,
           "\n".join("    set UEO3DE_CHUNK=%d/%d   (then run the import)"
                     % (index, chunks) for index in range(1, chunks + 1))))


SCRATCH_LEVEL_NAME = "UEO3DE_Scratch"
# The engine template's DefaultLevel is ~9 entity names (sun, sky, camera,
# grid, container). Anything well beyond that is someone's WORK.
SCRATCH_MAX_ENTITY_NAMES = 24


def scratch_level_name(environ=None):
    """The level imports author in. UEO3DE_SCRATCH_LEVEL overrides."""
    source = os.environ if environ is None else environ
    return (source.get("UEO3DE_SCRATCH_LEVEL") or "").strip() or SCRATCH_LEVEL_NAME


def level_prefab_path(project_root, level_name):
    return os.path.join(project_root, "Levels", level_name,
                        level_name + ".prefab")


def _engine_template_level():
    """The stock empty level shipped with the installed engine, or None.

    Levels cannot be created from Python on this build -- create_level and
    create_level_no_prompt both return None and write nothing (probed on
    26.05, Tests/o3de/probe_create_level.py) -- so the scratch level is
    SEEDED by copying the engine's own project-template DefaultLevel. That
    file is 12.7 KB, nine stock entities, and contains no reference to its
    own name, so a plain copy under a new name is a complete level.
    """
    import glob as glob_module
    manifest = os.path.join(os.path.expanduser("~"), ".o3de",
                            "o3de_manifest.json")
    try:
        import json as json_module
        with open(manifest, "r") as handle:
            engines = json_module.load(handle).get("engines") or []
    except (OSError, ValueError):
        engines = []
    for engine in engines:
        root = engine.get("path") if isinstance(engine, dict) else engine
        if not root:
            continue
        hits = glob_module.glob(os.path.join(
            str(root), "Templates", "*", "Template", "Levels", "DefaultLevel",
            "DefaultLevel.prefab"))
        if hits:
            return sorted(hits)[0]
    return None


def ensure_scratch_level(project_root, level_name, template_path=None):
    """Seed `Levels/<name>/<name>.prefab` from the engine template if absent.

    Only ever creates the DEDICATED scratch level; any other name is the
    caller's own level and is left exactly as found.
    """
    target = level_prefab_path(project_root, level_name)
    if os.path.isfile(target):
        return target
    if level_name != scratch_level_name():
        return target          # not ours to create; the editor will complain
    template = template_path or _engine_template_level()
    if template is None:
        raise RuntimeError(
            "no engine project template found to seed the scratch level "
            "'%s' from, and levels cannot be created from Python on this "
            "build. Create Levels/%s/%s.prefab once by hand (File > New "
            "Level) and re-run." % (level_name, level_name, level_name))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    import shutil as shutil_module
    shutil_module.copyfile(template, target)
    return target


def refuse_populated_level(project_root, level_name, environ=None):
    """Raise before an import authors inside a level holding real work.

    THE INCIDENT THIS GUARDS (2026-08-23): imports use their level as
    disposable scratch -- they open it with no save prompt, REMOVE placed
    instances of the prefab being rebuilt, and the level gets saved. That
    machinery ran against the level the user was building in, and their
    placed scene was stripped and saved over. The content came back only
    because the editor keeps .bak files.

    The check is a plain file read, no editor: a level file with more than
    SCRATCH_MAX_ENTITY_NAMES entity names is somebody's work, whatever it is
    called. UEO3DE_SCRATCH_OK=1 overrides for someone who genuinely means it.
    """
    source = os.environ if environ is None else environ
    if str(source.get("UEO3DE_SCRATCH_OK", "")).strip().lower() in (
            "1", "on", "true", "yes"):
        return
    path = level_prefab_path(project_root, level_name)
    if not os.path.isfile(path):
        return                 # nothing there to destroy
    try:
        import json as json_module
        with open(path, "r") as handle:
            names = json_module.dumps(json_module.load(handle)).count('"Name"')
    except (OSError, ValueError):
        return                 # unreadable: let the editor's own open fail it
    if names <= SCRATCH_MAX_ENTITY_NAMES:
        return
    raise RuntimeError(
        "REFUSING to import inside level %r: it holds ~%d entities and looks "
        "like real work, not a scratch level. The import authors in this "
        "level, REMOVES existing instances of the target prefab from it, and "
        "the level can end up saved that way -- this exact sequence stripped "
        "a user's scene out of the level they were building in. Import into "
        "the dedicated scratch level instead (the default), or set "
        "UEO3DE_SCRATCH_OK=1 if this level really is disposable."
        % (level_name, names))


def chunked_prefab_path(prefab_path, index, total):
    """`.../Name.prefab` -> `.../Name_part02_of_12.prefab`.

    The chunk guard PROMISES this ("each writes its own prefab", above), and
    for a while only a test helper kept the promise: `cli.py` and `dialog.py`
    both wrote the plain path, so chunk 2 overwrote chunk 1 and then computed
    a re-import diff against chunk 1's ledger -- every entity "changed". The
    suffix lives HERE, where UEO3DE_CHUNK is parsed, so every entry point
    inherits it and none can forget.

    Idempotent: a caller that already suffixed the path (the old test helper
    did) must not end up with the suffix twice.
    """
    suffix = "_part%02d_of_%02d" % (index, total)
    stem, extension = os.path.splitext(prefab_path)
    if stem.endswith(suffix):
        return prefab_path
    return stem + suffix + extension


def _ranked_spatially(groups):
    """The groups in Hilbert order; ties by id so the same manifest always
    walks the same way."""
    keys = _spatial_keys(groups)
    return [groups[i] for i in sorted(
        range(len(groups)),
        key=lambda i: (keys[i], groups[i][0]["id"],
                       groups[i][1]["id"] if len(groups[i]) > 1 else ""))]


def _fill_runs(ranked, cap):
    """Cut a ranked walk into runs of at most `cap` entities each; a group
    larger than `cap` gets a run of its own (the ceiling split above keeps
    every group at or under the ceiling, so that only happens below it)."""
    runs = [[]]
    sizes = [0]
    for group in ranked:
        if sizes[-1] and sizes[-1] + len(group) > cap:
            runs.append([])
            sizes.append(0)
        runs[-1].append(group)
        sizes[-1] += len(group)
    return runs


def _spatial_runs(ranked, count, ceiling):
    """`count` contiguous runs, as even as the walk allows and never over the
    ceiling; None when even the ceiling needs more than `count` runs."""
    total = sum(len(g) for g in ranked)
    if not total:
        return [[] for _ in range(count)]
    low = (total + count - 1) // count          # the even share
    high = ceiling
    if len(_fill_runs(ranked, high)) > count:
        return None
    # the smallest cap in [share, ceiling] that fits in `count` runs: the
    # tightest packing keeps the chunks closest to even
    while low < high:
        mid = (low + high) // 2
        if len(_fill_runs(ranked, mid)) <= count:
            high = mid
        else:
            low = mid + 1
    runs = _fill_runs(ranked, high)
    return runs + [[] for _ in range(count - len(runs))]


def spatial_chunks(document, ceiling=None):
    """How many chunks the spatial order needs for this manifest: the size
    order's count is a floor, a contiguous walk may need one or two more."""
    ceiling = chunk_ceiling() if ceiling is None else ceiling
    groups = _split_groups(document, ceiling, spatial=True)
    return max(1, len(_fill_runs(_ranked_spatially(groups), ceiling)))


def _split_groups(document, ceiling, spatial=False):
    """Root subtrees, oversized ones split by direct children (see chunk_of).

    Under the spatial order the oversized root's children are walked along
    the curve before they are cut into pieces, so each piece is a compact
    patch too: NYC's InstancedFoliageActor pieces spanned 816 x 274 m (52% of
    the map) when cut in export order, against 25% for the rest.
    """
    entities = document["entities"]
    children = {}
    for entity in entities:
        children.setdefault(entity["parent_id"], []).append(entity)

    def subtree(root):
        out = [root]
        stack = [root["id"]]
        while stack:
            for child in children.get(stack.pop(), ()):
                out.append(child)
                stack.append(child["id"])
        return out

    groups = [subtree(root) for root in children.get(None, ())]
    split = []
    for group in groups:
        if len(group) <= ceiling:
            split.append(group)
            continue
        root = group[0]
        piece = [root]
        branches = [subtree(child) for child in children.get(root["id"], ())]
        if spatial:
            branches = _ranked_spatially(branches)
        for branch in branches:
            if len(piece) > 1 and len(piece) + len(branch) > ceiling:
                split.append(piece)
                piece = [root]
            piece.extend(branch)
        split.append(piece)
    return split


def chunk_of(document, index, count, order=None):
    """The `index`-th of `count` slices of a manifest, split by whole subtrees.

    A level can be too large to import as one prefab. Measured on a 44,504-entity
    marketplace city: 4,000 entities import in 126 s at a 4.8 GB peak, and
    12,000 kills the editor outright during `saving prefab` (no assert, no
    log line, exit 0xC0000409). Nothing about that is going to be fixed by
    waiting longer, so the level has to arrive as several prefabs.

    Split by ROOT SUBTREE, never by entity index. An index range would cut
    parents away from their children, and a child whose parent is missing
    either vanishes or lands at the level root -- a building's windows
    scattered at the origin, in a prefab that saved cleanly. Slicing whole
    subtrees means every entity keeps the parent it was exported with.

    Bins are filled largest-subtree-first onto the currently-emptiest bin, so
    chunks come out close to even (this level: 1051 roots, largest subtree 646
    entities, 674 of them singletons) without any bin exceeding what one import
    can hold. With `order="spatial"` (UEO3DE_CHUNK_ORDER) the subtrees are
    instead walked along a Hilbert curve over the level and cut into `count`
    contiguous runs, so each chunk is a compact patch of the map: on
    NYC_Level_WC (51,776 entities, 13 chunks) the size order gave every chunk
    the level's full extent, and finishing one street needed all thirteen.

    Guarantees, asserted by `Tests/perf/test_chunk.py`: every entity appears in
    exactly one chunk, no subtree is split across chunks, and the chunks
    reassemble to the original entity set.
    """
    order = chunk_order() if order is None else order
    if order not in CHUNK_ORDERS:
        raise ValueError("chunk order must be one of %s, got %r"
                         % ("|".join(CHUNK_ORDERS), order))
    entities = document["entities"]

    # Manifest order decides ties, so the same manifest always splits the same
    # way -- a chunk that moved between runs would make re-import meaningless.
    #
    # A subtree larger than what one import can hold cannot be binned whole.
    # Measured on NYC_Level_WC: one InstancedFoliageActor root with 13,964
    # flat children -- foliage instances expanded into entities -- landed
    # in a single chunk of 13,965 against a crash boundary near 12,000.
    # Such a root is split by its DIRECT children's subtrees, and the root
    # itself (a transform-only container) rides along in every piece, so no
    # child ever loses the parent it was exported with. The root therefore
    # appears once per piece; re-import matches it by id in each chunk's
    # own ledger, and the copies are invisible placeholders at one place.
    ceiling = chunk_ceiling()
    groups = _split_groups(document, ceiling, spatial=(order == "spatial"))
    bins = [[] for _ in range(count)]
    if order == "spatial":
        # Walk the groups along a Hilbert curve over the level's XY extent and
        # cut the walk into contiguous runs, so each chunk is a compact patch:
        # a few blocks, not every n-th building. The first cut tried is the
        # even share; a run that would overflow it starts the next bin. When
        # the curve's fragmentation needs more than `count` bins at that
        # share (a near-ceiling foliage piece next to a half-full bin -- NYC
        # put 5,982 in one chunk on the first try), the cap widens up to the
        # ceiling; past that the manifest genuinely needs more chunks, and
        # the error names how many.
        runs = _spatial_runs(_ranked_spatially(groups), count, ceiling)
        if runs is None:
            raise ValueError(
                "UEO3DE_CHUNK_ORDER=spatial needs %d chunks for this manifest "
                "at a ceiling of %d (%d asked): a contiguous walk packs less "
                "tightly than the size order. Set UEO3DE_CHUNK=i/%d."
                % (spatial_chunks(document, ceiling), ceiling, count,
                   spatial_chunks(document, ceiling)))
        for target, run in enumerate(runs):
            for group in run:
                bins[target].extend(group)
    else:
        groups.sort(key=lambda g: (-len(g), g[0]["id"], g[1]["id"] if len(g) > 1 else ""))
        sizes = [0] * count
        for group in groups:
            target = sizes.index(min(sizes))
            bins[target].extend(group)
            sizes[target] += len(group)

    keep = {entity["id"] for entity in bins[index]}
    sliced = dict(document)
    sliced["entities"] = [e for e in entities if e["id"] in keep]
    return sliced


def settle_frames(bake_count, skeletal_authored):
    """Frames to idle after authoring, before serializing the prefab.

    It stays a blind constant, and that is a conclusion rather than a
    concession. Mesh colliders bake on their component's tick and the result is
    serialized into the prefab; serialize too early and the collider is written
    out with no geometry at all. Four probes looked for something to wait ON
    instead:

      * the bake appears in none of the collider's 17 reflected properties
      * a baked collider and an unbaked one read IDENTICALLY through every
        Python-visible call, compared side by side in one session
      * the in-memory template is a snapshot -- re-flushing it 12 times over
        3600 further frames recovered nothing
      * the prefab cannot be re-created in the same session ("Creating prefab
        as an override edit is currently not supported")

    So there is nothing to poll and nothing to repair. What makes a constant
    acceptable is the check that now follows the save: a bake that does not
    reach the file is reported as PHYS_COLLIDER_NOT_BAKED (error) instead of
    passing silently, which is what it did until it was measured.

    The numbers, on L_Showcase (2905 entities, 2501 mesh colliders, a Landscape
    whose baked data is 3 MB):

        settle    bakes in the file
             0    2486 / 2501   <- 15 lost, silently, import reported PASS
            30    2501 / 2501
           120    2501 / 2501
           200    2501 / 2501
          1500    2501 / 2501

    The old formula asked for 41,040 frames -- `60 + 5*bakes + 5*assigned +
    5*slots + 10*skeletal` -- against a real need somewhere under 30. It had
    grown across three rounds of tuning a `CreatePrefabInMemory` failure that
    turned out not to be a streaming race at all but a stale prefab instance in
    the scratch level (`prefab_build.detach_conflicting_instances`), and its
    terms were never re-measured once that cause was found.

    The two material terms are gone because they were measured to guard
    nothing: at settle=0 every material asset id in the prefab was identical to
    the control, and only cooked collider data differed. They were there
    against a serialization throw, which has its own retry below and which did
    not happen at settle=0 either.

    What remains is deliberately generous -- ~1550 frames on L_Showcase, some
    fifty times the measured need -- because the failure is unrecoverable
    within a session, one level on one machine is a thin basis for a threshold,
    and 20 s of a 125 s import is a cheap insurance premium. Collider count is
    a PROXY for bake work, which is really geometry volume; the proxy is
    acceptable only because being wrong is now loud. `UEO3DE_SETTLE_FRAMES`
    overrides it, which is how the table above was measured.

    The 10-per-skeletal term is unchanged and remains UNMEASURED: L_Showcase
    has no skeletal entities.

    `bake_count` counts RENDER-MESH BAKES only (`mesh_colliders`), never
    cooked-asset colliders (`mesh_asset_colliders`): an asset collider's
    geometry lives in the `.pxmesh`/`.joltmesh` product and the component
    serializes a reference, so there is no tick to wait for. A level imported
    entirely through cooked assets therefore lands on the 300-frame floor,
    which at that point is insurance against nothing measured -- worth
    re-measuring once a Jolt gem with asset-based mesh colliders is built,
    since it is the last reason this phase exists at all.
    """
    override = os.environ.get("UEO3DE_SETTLE_FRAMES", "").strip()
    if override:
        return int(override)
    if bake_count == 0:
        # Nothing bakes on a tick, so there is nothing for a settle to wait
        # for. This is not an optimisation guess: measured on a 3,677-entity
        # siege map imported entirely through cooked `.joltmesh` assets, where
        # settle=0 and the full settle produced prefabs `prefab_diff` calls
        # EQUIVALENT -- same component types, same 3,290 cooked-mesh asset ids,
        # same transforms -- and both verified 3,290 of 3,290 references
        # present in the saved file.
        #
        # The skeletal term stays: it was never measured and is not what this
        # measurement covers.
        return 10 * skeletal_authored
    return 300 + bake_count // 2 + 10 * skeletal_authored


def stage_only(manifest_path, source_assets_root, project_assets_root, log=None):
    """Copy FBX + write `.assetinfo`. Returns (document, staged records)."""
    def emit(message):
        if log is not None:
            log(message)

    document = manifest_io.load(manifest_path)
    emit("manifest ok: schema %d, %d entities, %d assets"
         % (document["schema_version"], len(document["entities"]), len(document["assets"])))
    emit("staging into " + project_assets_root)
    records = staging.stage(document, source_assets_root, project_assets_root, log=log)
    emit("staged %d static mesh source files" % len(records))
    return document, records


def import_level(manifest_path, source_assets_root, project_assets_root,
                 prefab_path, level_name=None, asset_timeout=180.0,
                 restage=False, backend=None, log=None, max_entities=None,
                 reimport=True):
    """Import a manifest into a saved `.prefab`. Returns (report, prefab_path).

    `backend` is the explicit physics backend name ('jolt'/'physx') or None to
    detect. Detection never guesses: if both backends resolve and no explicit
    choice is given, the import fails before authoring anything (constraint 5).

    `reimport` (M10) makes a second import of the same prefab incremental: the
    previous import's ledger is consulted, entities are matched by manifest id,
    and anything the user edited by hand in O3DE is reported and KEPT rather
    than reverted. Pass False to ignore the ledger and author everything from
    the manifest -- the escape hatch for "just give me exactly what UE says".
    """
    import json as json_module
    import time as time_module

    import azlmbr.legacy.general as general

    from . import asset_wait
    from . import env_build
    from . import light_build
    from . import physics_build
    from . import prefab_build
    from . import reimport as reimport_module
    from .adapters import base as adapters_base
    from .adapters import detect_in_editor, make_adapter

    def emit(message):
        if log is not None:
            log(message)

    report = Report()

    # A running stopwatch: `mark(name)` attributes everything since the last
    # mark to `name`. One line per phase boundary rather than a `with` block
    # around each -- the phases here are long sequential stretches, and
    # wrapping them would reindent most of this function for no extra
    # information. Because every mark closes the previous span, the figures
    # account for the WHOLE import rather than a chosen subset, which is the
    # property that makes them safe to reason about.
    _clock = [time_module.perf_counter()]

    def mark(name):
        now = time_module.perf_counter()
        report.timings[name] = report.timings.get(name, 0.0) + (now - _clock[0])
        _clock[0] = now

    document = manifest_io.load(manifest_path)
    skip_indices = {int(i) for i in os.environ.get("UEO3DE_SKIP", "").split(",") if i.strip()}
    if skip_indices:
        document = dict(document)
        document["entities"] = [e for i, e in enumerate(document["entities"])
                                if i not in skip_indices]
    # UEO3DE_CHUNK=i/n -- import only the i-th of n slices (1-based), split by
    # whole subtrees. For levels no single prefab can hold; see `chunk_of`.
    chunk = os.environ.get("UEO3DE_CHUNK", "").strip()
    if chunk:
        index, _, total = chunk.partition("/")
        index, total = int(index), int(total)
        if not (total >= 1 and 1 <= index <= total):
            raise ValueError("UEO3DE_CHUNK must be i/n with 1 <= i <= n, got %r"
                             % chunk)
        document = chunk_of(document, index - 1, total)
        if total > 1:
            # 1/1 is the documented "import as one prefab anyway" escape and
            # keeps the plain name; real slices each get their own file, or
            # the level ends up as whichever chunk imported last.
            prefab_path = chunked_prefab_path(prefab_path, index, total)
        emit("UEO3DE_CHUNK=%s (%s order) -- %d of this manifest's entities -> %s"
             % (chunk, chunk_order(), len(document["entities"]),
                os.path.basename(prefab_path)))
    if max_entities is not None:
        # Diagnostic bisect knob (UEO3DE_MAX_ENTITIES): import only the first
        # N entities to localize scale- or content-dependent failures.
        keep = {e["id"] for e in document["entities"][:max_entities]}
        document = dict(document)
        document["entities"] = [e for e in document["entities"]
                                if e["id"] in keep and
                                (e["parent_id"] is None or e["parent_id"] in keep)]

    # Refuse a manifest measured to be beyond what a single import survives,
    # rather than discovering it during `saving prefab` twenty minutes in,
    # where the failure leaves nothing to read.
    #
    # AFTER every knob that shrinks the document, and that ordering is the
    # whole point: an explicit UEO3DE_CHUNK is a decision to override this, and
    # UEO3DE_MAX_ENTITIES=500 against a huge level is a 500-entity import that
    # this guard has no business refusing. It measures what is actually about
    # to be imported, not what the file happens to contain.
    if not chunk:
        ceiling = chunk_ceiling()
        count = len(document["entities"])
        chunks = recommended_chunks(count, ceiling)
        if chunks > 1 and chunk_order() == "spatial":
            # a contiguous walk packs less tightly than largest-first: the
            # command the guard prints must be one the spatial fill accepts
            chunks = max(chunks, spatial_chunks(document, ceiling))
        if chunks > 1:
            raise ValueError(chunk_guard_message(count, chunks, ceiling))

    # --- incremental re-import (M10) ---------------------------------------
    # Computed BEFORE anything is authored, because it reads the prefab as it
    # stands right now -- which is where the user's hand edits are. Once the
    # rebuild starts, that state is gone.
    previous_ledger = reimport_module.load_ledger(prefab_path) if reimport else None
    prefab_duplicates = set()
    transforms_before = reimport_module.read_prefab(prefab_path,
                                                   duplicates=prefab_duplicates)
    reimport_plan = reimport_module.plan(previous_ledger, document,
                                         transforms_before,
                                         prefab_duplicates=prefab_duplicates)
    emit(reimport_module.summarize(reimport_plan))
    if reimport and previous_ledger is None and transforms_before:
        report.warn("REIMPORT_LEDGER_MISSING", os.path.basename(prefab_path),
                    "a prefab exists at this path but has no ledger beside it; "
                    "hand edits in it cannot be detected and will be replaced")
    if reimport and previous_ledger is not None             and not os.path.exists(prefab_path):
        # The INVERSE orphan: a ledger with no prefab. The known way to get
        # here is a previous import that deleted the old prefab and then
        # failed before writing the new one -- whatever hand edits that file
        # held are already gone, and the empty transform map below would
        # otherwise make this import look like a clean first run. Say so
        # instead of letting the silence stand.
        report.warn("REIMPORT_LEDGER_MISSING", os.path.basename(prefab_path),
                    "a ledger exists but the prefab it describes does NOT -- "
                    "a previous import likely failed after removing the old "
                    "file. Any hand edits it held are unrecoverable; this "
                    "import rebuilds from the manifest alone (check for a "
                    "%s.prev backup beside it)"
                    % os.path.basename(prefab_path))
    for name in sorted(set(reimport_plan["name_collisions"]) | prefab_duplicates):
        report.warn("REIMPORT_NAME_COLLISION", name,
                    "more than one entity carries this name (two manifest "
                    "entities, or an actor sharing the level root's name), so "
                    "hand edits on it cannot be told apart and are neither "
                    "detected nor preserved")
    for removed in reimport_plan["removed"]:
        report.warn("REIMPORT_ENTITY_REMOVED", removed["name"] or removed["id"],
                    "present in the previous import, absent from this manifest")
    for unmatched in reimport_plan["unmatched"]:
        report.warn("REIMPORT_ENTITY_UNMATCHED",
                    unmatched["name"] or unmatched["id"],
                    "the previous import authored this entity but the prefab "
                    "has no entity of that name; any hand edits on it cannot "
                    "be matched and are replaced")
    # "Added" means "new SINCE THE LAST IMPORT". On a first import every
    # entity is new in the trivial sense, and counting them all reads as
    # "12 actors appeared" on a report where nothing appeared -- so the
    # re-import counters stay at zero until there is a previous import to be
    # different from.
    if not reimport_plan["first_import"]:
        names_by_id = {e["id"]: e.get("name") for e in document["entities"]}
        for entity_id in reimport_plan["added"]:
            # Report the NAME, not the uuid: the subject column is what a user
            # reads to find the thing in their level, and a uuid5 identifies
            # nothing to them.
            report.warn("REIMPORT_ENTITY_ADDED", names_by_id.get(entity_id) or entity_id,
                        "new since the last import")
        report.count("reimport_added", len(reimport_plan["added"]))
    report.count("reimport_removed", len(reimport_plan["removed"]))
    report.count("reimport_conflicts", len(reimport_plan["conflicts"]))
    mark("reimport diff")

    # An open level comes FIRST: prefab authoring needs a root prefab instance
    # (S0.1), and the adapter's resolve step creates a scratch entity to read
    # the backend's contact offset -- entity creation without a level throws.
    # BEFORE opening: a level holding an instance of the prefab this import is
    # about to rewrite makes CreatePrefabInMemory throw, and no amount of
    # settling helps. See prefab_build.detach_conflicting_instances.
    project_root = os.path.dirname(os.path.normpath(project_assets_root))
    if level_name is None:
        level_name = scratch_level_name()
    ensure_scratch_level(project_root, level_name)
    refuse_populated_level(project_root, level_name)
    report.count("stale_instances_removed",
                 prefab_build.detach_conflicting_instances(
                     project_root, level_name, prefab_path, log=emit))

    general.idle_enable(True)
    general.open_level_no_prompt(level_name)
    general.idle_wait_frames(30)
    mark("open level")

    # --- physics backend: detect, resolve-or-fail, negotiate (M3) ---
    detection = detect_in_editor(explicit=backend)
    emit("physics backend: %s (source: %s, settings hint: %r)"
         % (detection["backend"], detection["source"], detection["settings_hint"]))
    adapter = make_adapter(detection["backend"])
    adapter.resolve_components()
    emit("  components resolved; contact offset %.4f m" % adapter.contact_offset())
    physics_build.negotiate(adapter, document, report)
    mark("backend detect + resolve")

    profiles_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "collision_profiles.json")
    with open(profiles_path, "r") as handle:
        all_profiles = json_module.load(handle)
    profile_map = {k: v for k, v in
                   (all_profiles.get(detection["backend"]) or {}).items()
                   if not k.startswith("_")}

    # Re-deriving the staged records is cheap and keeps this entry point usable
    # on its own (M10's interactive import does not run a separate stage step).
    product_prefix = os.path.basename(os.path.normpath(project_assets_root)).lower()
    if restage:
        records = staging.stage(document, source_assets_root, project_assets_root, log=log)
    else:
        records = []
        for asset in manifest_io.static_mesh_assets(document):
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": "static_mesh",
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": staging.product_path_for(relative_path, product_prefix),
                "wait": True,
            })
        for asset in manifest_io.skeletal_assets(document):
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": asset["kind"],
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": staging.skeletal_product_path_for(
                    relative_path, product_prefix, asset["kind"]),
                "wait": True,
            })
        for asset in document["assets"]:
            if asset["kind"] != "material" or not asset.get("material_data"):
                continue
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": "material",
                "guid": asset["guid"],
                "relative_path": relative_path,
                "staged_fbx": os.path.join(project_assets_root, relative_path).replace("\\", "/"),
                "product_path": ("%s/%s" % (product_prefix,
                                            relative_path.rsplit(".", 1)[0]
                                            + ".azmaterial")).lower(),
                "wait": True,
            })

    mark("stage + resolve product paths")
    waitable = [record for record in records if record.get("wait")]
    emit("waiting for %d product assets (timeout %.0fs each)"
         % (len(waitable), asset_timeout))
    asset_ids = asset_wait.wait_for_all(waitable, timeout_seconds=asset_timeout, log=emit)
    report.count("assets_waited_for", len(asset_ids))
    emit("  all %d products present in the catalog" % len(asset_ids))
    mark("wait for product assets")

    mesh_asset_ids = {record["guid"]: asset_ids[record["guid"]]
                      for record in waitable if record["kind"] == "static_mesh"}
    material_asset_ids = {record["guid"]: asset_ids[record["guid"]]
                          for record in waitable if record["kind"] == "material"}
    skeletal_asset_ids = {record["guid"]: asset_ids[record["guid"]]
                          for record in waitable
                          if record["kind"] in ("skeletal_mesh", "animation")}

    # --- cooked physics meshes (.pxmesh / .joltmesh), CAP_SHAPE_MESH_COOKED ---
    # The SIDECAR ON DISK decides what to wait for, not the manifest: a
    # sidecar staged before cooked-mesh support (or into a project without the
    # PhysX gem) never asked the Asset Processor to cook, and waiting on a
    # product that was never requested burns the timeout once per asset before
    # falling back anyway.
    #
    # The TIMEOUT depends on whether this run just rewrote those sidecars.
    # `wait_for_asset` polls the catalog for PRESENCE and cannot tell a product
    # of the current fingerprint from one the previous cook left behind, so on
    # a restage the azmodel waits above pass INSTANTLY on stale entries and
    # prove nothing about the scene jobs AP has only just queued. A short cap
    # there would time out on every mesh AP had not reached yet and blame a
    # cook failure that never happened -- so restage gets the full budget, and
    # only the no-restage path (where CI ran AP to completion before the editor
    # started) keeps the short one.
    cooked_mesh_ids = {}
    if adapters_base.CAP_SHAPE_MESH_COOKED in adapter.capabilities():
        from . import assetinfo
        cook_timeout = asset_timeout if restage else min(asset_timeout, 30.0)
        expected = []
        for asset in manifest_io.static_mesh_assets(document):
            staged_fbx = os.path.join(
                project_assets_root, asset["o3de_relative_path"]).replace("\\", "/")
            plan = assetinfo.physics_in_sidecar(staged_fbx + ".assetinfo",
                                                backend=adapter.name())
            if plan:
                expected.append((asset, plan, staged_fbx))
            elif assetinfo.physics_for_asset(asset):
                report.warn("PHYS_MESH_NOT_COOKED", asset["ue_path"],
                            "this mesh needs a cooked physics mesh but its "
                            "staged sidecar carries no PhysX mesh group; AABB "
                            "boxes substitute. Restage to fix -- and if a "
                            "restage does not, this project activates PhysX "
                            "transitively rather than listing it in "
                            "project.json, so stage it with UEO3DE_PHYSX_COOK=1")
        emit("waiting for %d cooked physics meshes (timeout %.0fs each)"
             % (len(expected), cook_timeout))
        for asset, plan, staged_fbx in expected:
            product = staging.physics_product_path_for(
                asset["o3de_relative_path"], product_prefix, adapter.name())
            try:
                pxmesh_id = asset_wait.wait_for_asset(
                    product, timeout_seconds=cook_timeout,
                    source_path=staged_fbx)
            except asset_wait.AssetWaitTimeout:
                report.warn("PHYS_MESH_NOT_COOKED", asset["ue_path"],
                            "the sidecar asks for a cooked physics mesh but no "
                            "%s product appeared within %.0fs; AABB boxes "
                            "substitute -- check the Asset Processor log for "
                            "the cook error" % (product, cook_timeout))
                continue
            cooked_mesh_ids[asset["guid"]] = {"asset_id": pxmesh_id,
                                              "method": plan["method"],
                                              "decompose_hulls": plan.get("decompose_hulls")}
        report.count("cooked_physics_meshes", len(cooked_mesh_ids))
        mark("wait for cooked physics meshes")

    level_root_name = document["level"]["name"]
    emit("creating entities under level root %r" % level_root_name)
    level_root = prefab_build.create_level_root(level_root_name)
    created = prefab_build.create_entities(document, mesh_asset_ids, report, level_root, log=emit)
    report.count("entities_created", len(created))
    mark("create entities")

    # --- materials (M4): per entity, default slot or per-slot by label ---
    # A model whose mapped slots all share one material takes the default
    # slot (covers everything, no dependency on the model asset having
    # streamed in). Distinct materials per slot go through o3dimport's
    # label-matching technique, which needs the component's Model Materials
    # rows and so runs in TWO passes with one shared wait between them.
    assets_by_guid = manifest_io.assets_by_guid(document)
    emit("assigning materials (%d converted)" % len(material_asset_ids))
    prefab_build.reset_material_stats()
    assigned = 0
    slots_assigned = 0
    pending_slots = []   # (component pair, entity id, assignments, name)
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        # Skeletal entities carry the same per-slot structure; the Material
        # component consumes an Actor component's model the way it does a
        # Mesh component's (both are material consumers).
        mesh = item.get("mesh") or item.get("skeletal")
        if entity_id is None or mesh is None:
            continue
        slots = mesh.get("material_slots") or []
        mapped = [slot for slot in slots
                  if slot.get("material_guid") in material_asset_ids]
        if not mapped:
            continue  # unmapped material: the backend default stays, by design
        distinct = []
        for slot in mapped:
            if slot["material_guid"] not in distinct:
                distinct.append(slot["material_guid"])
        if len(distinct) == 1:
            prefab_build.assign_material(
                entity_id, material_asset_ids[distinct[0]], item["name"])
            assigned += 1
            continue
        # The LABEL is the mesh asset's own material name for that slot -- that
        # is what the baked FBX carries and what the azmodel slot is called.
        # The MATERIAL is the entity's effective one, which a component
        # override may have changed. Keying the label off the effective
        # material instead is the bug L_Showcase exposed: every tree overrides
        # its leaf material per instance, so no label ever matched and 97
        # entities silently kept the asset's default.
        mesh_asset = assets_by_guid.get(mesh["asset_guid"], {})
        asset_slot_names = mesh_asset.get("material_slot_material_names") or []
        assignments = []
        labels_seen = {}
        for slot in mapped:
            guid = slot["material_guid"]
            index = slot.get("index", 0)
            label = (asset_slot_names[index] if index < len(asset_slot_names)
                     else "") or assets_by_guid[guid]["name"]
            if label in labels_seen:
                if labels_seen[label] != guid:
                    report.warn("MAT_SLOT_LABEL_AMBIGUOUS", item["name"],
                                "slot label %r covers two different materials; "
                                "only the first can be assigned" % label)
                continue
            labels_seen[label] = guid
            assignments.append((label, material_asset_ids[guid]))
        # Pass 1 stops here: add the component and remember what to do with it.
        # The rows it exposes do not exist until a tick has elapsed, and that
        # tick is SHARED -- see prefab_build.wait_for_model_rows. Assigning
        # inline instead made every one of these entities wait for its own
        # copy of the same tick.
        pending_slots.append((prefab_build.begin_material_slots(entity_id, item["name"]),
                              entity_id, assignments, item["name"]))
        assigned += 1

    # One wait for the whole level, then pass 2.
    not_ready = prefab_build.wait_for_model_rows([p for p, _e, _a, _n in pending_slots])
    for index, (pair, entity_id, assignments, name) in enumerate(pending_slots):
        slots_assigned += prefab_build.finish_material_slots(
            pair, entity_id, assignments, name, report,
            ready=index not in not_ready)
    report.count("materials_assigned", assigned)
    report.count("material_slots_assigned", slots_assigned)
    mark("materials")
    # Sub-phases of the phase that turned out to BE half the import. Recorded
    # as timings so they appear beside the top-level rows, and as counters for
    # the frame budget, which is the actionable number: frames burned polling
    # for models to stream in are frames nobody chose to spend.
    for key, value in prefab_build.MATERIAL_STATS.items():
        if key.endswith("_s"):
            report.subtimings[key[:-2].replace("material_", "materials: ")] = value
        else:
            report.count(key, value)

    # --- skeletal entities (M8): Actor + Simple Motion ---
    from . import skel_build
    emit("authoring skeletal entities")
    skeletal_authored = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        skeletal = item.get("skeletal")
        if entity_id is None or skeletal is None:
            continue
        plan = skel_build.plan_skeletal(skeletal, item["name"])
        skel_build.author_skeletal(
            entity_id, plan,
            skeletal_asset_ids.get(skeletal["asset_guid"]),
            skeletal_asset_ids.get(skeletal.get("animation_guid")),
            item["name"], prefab_build.resolve_component_type)
        skeletal_authored += 1
        emit("  %-22s Actor%s" % (
            item["name"],
            " + Simple Motion" if skeletal.get("animation_guid") else ""))
    report.count("skeletal_entities", skeletal_authored)
    mark("skeletal")

    # --- decals + cameras (M9) ---
    from . import camera_build
    from . import decal_build
    emit("authoring decals + cameras")
    decals = 0
    cameras = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        if entity_id is None:
            continue
        decal = item.get("decal")
        if decal is not None:
            material_asset = material_asset_ids.get(decal.get("material_guid"))
            plan = decal_build.plan_decal(decal, item["name"])
            if decal.get("material_guid") and material_asset is None:
                # Unconverted material: author the volume + sort key only,
                # and say so -- an invisible decal must never be silent.
                plan["properties"] = [p for p in plan["properties"]
                                      if p[1] != "material_asset"]
                report.warn("DECAL_MATERIAL_UNCONVERTED", item["name"],
                            "the decal's material did not convert; the decal "
                            "imports without a material")
            decal_build.author_decal(entity_id, plan, material_asset,
                                     item["name"],
                                     prefab_build.resolve_component_type)
            decals += 1
            emit("  %-22s Decal" % item["name"])
        camera = item.get("camera")
        if camera is not None:
            plan = camera_build.plan_camera(camera, item["name"])
            camera_build.author_camera(entity_id, plan, item["name"],
                                       prefab_build.resolve_component_type)
            cameras += 1
            emit("  %-22s Camera (v-fov %.2f deg)"
                 % (item["name"], plan["properties"][0][1]))
    report.count("decals_created", decals)
    report.count("cameras_created", cameras)
    mark("decals + cameras")

    # --- lights (M5) ---
    emit("authoring lights")
    lights = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        light = item.get("light")
        if entity_id is None or light is None:
            continue
        plan, light_warnings = light_build.plan_light(light, item["name"])
        for code, detail in light_warnings:
            report.warn(code, item["name"], detail)
        if plan is None:
            continue
        light_build.author_light(entity_id, plan, item["name"],
                                 prefab_build.resolve_component_type)
        lights += 1
        emit("  %-22s %s (%d properties)"
             % (item["name"], plan["component"], len(plan["properties"])))
    report.count("lights_created", lights)
    mark("lights")

    # --- environment (M6) ---
    # Sky first and only once: a level usually has both a SkyLight and a
    # SkyAtmosphere, and two Physical Sky components fight over the same sky.
    emit("authoring environment")
    environments = 0
    sky_authored = False
    # A SkyLight carries the artist's authored intensity; a SkyAtmosphere
    # carries scattering parameters Atom cannot represent at all. When a level
    # has both -- most do -- the skylight must win the one Physical Sky, or
    # that intensity is silently replaced by a default.
    def sky_first(item):
        kind = (item.get("environment") or {}).get("type")
        return 0 if kind == "skylight" else 1

    # Exposure is global and does not stack, so exactly one level-wide volume
    # may author it -- and WHICH one is not arbitrary. UE resolves overlapping
    # unbound volumes by PRIORITY, so the highest-priority volume must be
    # reached first, or "the first one wins" would silently pick whichever the
    # manifest happened to list first. Measured on Demonstration: two distinct
    # volumes both named PostProcessVolume2, both unbound, both priority 0,
    # biases 12.0 and 9.5 -- two enabled Exposure Controls and a white level.
    def exposure_rank(item):
        environment = item.get("environment") or {}
        if environment.get("type") != "post_process":
            return 0.0
        return -float(environment.get("priority", 0) or 0)

    ordered = sorted(document["entities"], key=lambda i: (sky_first(i),
                                                          exposure_rank(i)))
    exposure_authored = False

    for item in ordered:
        entity_id = created.get(item["id"])
        environment = item.get("environment")
        if entity_id is None or environment is None:
            continue
        plans, env_warnings = env_build.plan_environment(
            environment, item["name"], sky_already_authored=sky_authored,
            exposure_already_authored=exposure_authored)
        for code, detail in env_warnings:
            report.warn(code, item["name"], detail)
        if not plans:
            continue
        authored = env_build.author_environment(
            entity_id, plans, item["name"], prefab_build.resolve_component_type)
        if env_build.PHYSICAL_SKY in authored:
            sky_authored = True
        if env_build.EXPOSURE_CONTROL in authored:
            exposure_authored = True
        environments += 1
        emit("  %-22s %s" % (item["name"], ", ".join(authored)))
    report.count("environments_created", environments)
    mark("environment")

    # --- physics authoring, all through the adapter (M3) ---
    # After the meshes: mesh colliders bake from the entity's own render model,
    # which must already be assigned (and its product waited for, above).
    emit("authoring physics through the %r adapter" % adapter.name())
    bodies = 0
    for item in document["entities"]:
        entity_id = created.get(item["id"])
        if entity_id is None:
            continue
        summary = physics_build.author_entity_physics(
            adapter, entity_id, item, assets_by_guid, report, profile_map,
            cooked_mesh_ids=cooked_mesh_ids)
        if summary:
            bodies += 1
            emit("  %-22s %s" % (item["name"], summary))
    report.count("physics_bodies", bodies)
    mark("physics authoring")
    # Let the mesh-collider bakes finish before serialization. Each runs on the
    # component's own tick, and its result is written INTO the prefab, so a
    # bake still in flight produces a collider with no geometry. See
    # `settle_frames` for why this is a constant and what it costs to get wrong.
    bake_count = report.counters.get("mesh_colliders", 0)
    settle = settle_frames(bake_count, skeletal_authored)
    report.count("settle_frames", settle)
    general.idle_wait_frames(settle)
    # Named for the collider bakes alone: it used to say "+ asset streaming"
    # too, and that half was measured to be false -- at settle=0 every material
    # asset id in the prefab matched the control exactly, and only cooked
    # collider data was lost.
    mark("settle: collider bakes")
    report.count("manifest_roots", sum(1 for item in document["entities"]
                                       if item["parent_id"] is None))
    if not created:
        raise prefab_build.PrefabBuildError("manifest produced no entities")

    emit("saving prefab")
    # One entity, at the origin: the container lands at the origin too, so
    # instantiating the prefab at the origin reproduces the level exactly.
    #
    # CreatePrefabInMemory surfaces internal failures as an opaque exception,
    # which this project twice mis-read as an asset-streaming race and "fixed"
    # with ever-longer settles. The real cause was a stale instance of the
    # target prefab inside the scratch level (see
    # prefab_build.detach_conflicting_instances) -- once removed, a 140-entity
    # level with 128 baked colliders saves on the FIRST attempt. The single
    # retry stays for genuine mid-bake serialization, but a failure here now
    # means something structural, not something to wait out.
    # Ids that already serialize belong to EARLIER imports in this editor
    # session (chunk N-1, a previous run); the flush must never mistake one of
    # those for the template just created -- every chunk shares the level-root
    # marker name.
    known_template_ids = prefab_build.snapshot_template_ids()
    try:
        prefab_build.create_prefab_in_memory([level_root], prefab_path)
    except RuntimeError:
        emit("  CreatePrefabInMemory threw; settling 900 frames and retrying once")
        general.idle_wait_frames(900)
        prefab_build.create_prefab_in_memory([level_root], prefab_path)
    prefab_build.flush_template_to_disk(prefab_path, level_root_name, log=emit,
                                        known_template_ids=known_template_ids)
    mark("save prefab")

    # --- did the collider bakes actually reach the file? ---
    #
    # `mesh_colliders` counts what was AUTHORED, and a collider whose bake had
    # not finished is written out fully configured with no geometry at all: it
    # collides with nothing, the save reports success, and the counter reads
    # the same either way. Measured on L_Showcase: settling zero frames lost 15
    # of 2501 bakes and every suite in this repo stayed green.
    #
    # This is a check rather than a wait because a wait is not available. Four
    # probes went looking for something to poll and found nothing (the bake is
    # in none of the collider's 17 reflected properties; a baked collider and
    # an unbaked one read identically through every Python-visible call). Nor
    # can it be repaired: the in-memory template is a snapshot that does not
    # track late bakes, and O3DE refuses a second CreatePrefabInMemory in the
    # same session. So the settle stays a constant, and this is what stops a
    # constant that is one day too small from failing in silence.
    # The stretch below used to parse the freshly-saved prefab up to FOUR
    # times (bake verification, ledger, conflict lookup, conflict patching) --
    # ~1 s each on a 20 MB level, purely for want of passing the document
    # along. Parse once; preserve_conflicts still writes the file itself.
    with open(prefab_path, "r") as handle:
        saved_prefab_document = json_module.load(handle)
    verification = prefab_build.collider_verification(
        prefab_path,
        jolt_mesh_is_asset_based=bool(
            getattr(adapter, "mesh_is_asset_based", lambda: False)()),
        document=saved_prefab_document)
    unbaked = verification["unbaked"]
    report.count("colliders_cooked", bake_count - len(unbaked))
    for name in unbaked:
        report.warn("PHYS_COLLIDER_NOT_BAKED", name,
                    "settled %d frames before serializing; re-import with a "
                    "larger UEO3DE_SETTLE_FRAMES" % settle)
    if unbaked:
        emit("  %d of %d mesh collider bakes did NOT reach the prefab"
             % (len(unbaked), bake_count))
    # The cooked-asset counterpart (PhysX): no bake and no settle involved,
    # but a mesh collider whose .pxmesh reference did not serialize collides
    # with nothing just as silently, so the reference is verified on the
    # bytes the same way.
    asset_collider_count = report.counters.get("mesh_asset_colliders", 0)
    missing_asset = verification["missing_asset"]
    report.count("mesh_asset_colliders_verified",
                 asset_collider_count - len(missing_asset))
    for name in missing_asset:
        report.warn("PHYS_MESH_ASSET_MISSING", name,
                    "the mesh collider serialized without a cooked physics "
                    "mesh reference; it collides with nothing")
    if missing_asset:
        emit("  %d of %d mesh collider asset references did NOT reach the "
             "prefab" % (len(missing_asset), asset_collider_count))
    mark("verify collider bakes")

    # --- record what this import AUTHORED, then put hand edits back (M10) ---
    #
    # The order matters and is not obvious. The ledger is written FIRST, from
    # the freshly rebuilt prefab -- that is, from the manifest's values, before
    # any hand edit is patched back over them. Writing it afterwards instead
    # made preservation survive exactly ONE re-import and then lose the edit in
    # silence:
    #
    #   run 2: conflict -> prefab patched to the user's value C
    #          ledger written from the patched file            -> records C
    #   run 3: file is C, ledger says C -> no conflict detected
    #          rebuild writes UE's value                       -> C is GONE
    #
    # The ledger's question is "what did WE author last time", so it must hold
    # what we authored. Then the conflict test -- does the file differ from
    # that? -- keeps answering yes for as long as the edit exists, and the edit
    # survives indefinitely and is reported on every run.
    ledger_path = reimport_module.write_ledger(
        prefab_path, reimport_module.build_ledger(
            document, prefab_path, prefab_document=saved_prefab_document))
    emit("wrote import ledger " + os.path.basename(ledger_path))

    # The prefab has just been rebuilt from the manifest, so any entity the
    # user had moved is now back at UE's value. Patch those few entities in
    # the saved file and say which ones, loudly.
    if reimport_plan["conflicts"]:
        rebuilt = reimport_module.read_prefab(
            prefab_path, document=saved_prefab_document)
        for conflict in reimport_plan["conflicts"]:
            # The REBUILT prefab is keyed by the NEW manifest names, so it must
            # be looked up by `new_name`. Using the ledger's old name made
            # `also_moved_in_ue` always False for a relabelled actor -- the
            # report then said "only you changed this" while UE's new
            # transform was being dropped. Same fix as preserve_conflicts;
            # it belongs in both places, and originally landed in only one.
            lookup = conflict.get("new_name") or conflict["name"]
            authored_now = rebuilt.get(lookup)
            also_moved_in_ue = (
                authored_now is not None
                and not reimport_module.transforms_equal(authored_now,
                                                         conflict["authored"]))
            if also_moved_in_ue:
                detail = ("edited in O3DE AND moved in UE since the last "
                          "import; the O3DE edit is kept, so this actor's new "
                          "UE transform was NOT applied")
            else:
                detail = ("edited in O3DE since the last import; the edit is "
                          "kept and the manifest's transform was not applied")
            # Report the name the entity has NOW, so the subject names
            # something the user can find in their level. The two warnings for
            # one entity used to disagree: this one used the old name while
            # REIMPORT_CONFLICT_NOT_PRESERVED used the new one.
            report.warn("REIMPORT_ENTITY_CONFLICT", lookup, detail)
        patched = reimport_module.preserve_conflicts(
            prefab_path, reimport_plan["conflicts"],
            document=saved_prefab_document)
        report.count("reimport_preserved", len(patched))
        emit("preserved %d hand-edited transform(s)" % len(patched))
        # Reporting a conflict and then not preserving it is the worst of both
        # outcomes: the user is told their edit was kept, and it was not. That
        # can only happen if an entity could not be found in the rebuilt
        # prefab under the name we looked for, so name it rather than let the
        # counters quietly disagree.
        if len(patched) != len(reimport_plan["conflicts"]):
            lost = sorted({(c.get("new_name") or c.get("name"))
                           for c in reimport_plan["conflicts"]} - set(patched))
            for name in lost:
                report.warn("REIMPORT_CONFLICT_NOT_PRESERVED", name,
                            "reported as hand-edited, but no entity of that "
                            "name was found in the rebuilt prefab, so the edit "
                            "could NOT be restored and has been lost")

    mark("ledger + hand edits")

    emit("")
    _total = sum(report.timings.values())
    emit("where the time went (%.1f s total):" % _total)
    for name, seconds, percent in report.timing_rows():
        emit("  %-42s %8.1f s  %5.1f%%" % (name, seconds, percent))
    if report.subtimings:
        emit("  within a phase (already counted above):")
        for name, seconds in sorted(report.subtimings.items(), key=lambda kv: -kv[1]):
            emit("    %-40s %8.1f s  %5.1f%%"
                 % (name, seconds, (100.0 * seconds / _total) if _total else 0.0))

    return report, prefab_path

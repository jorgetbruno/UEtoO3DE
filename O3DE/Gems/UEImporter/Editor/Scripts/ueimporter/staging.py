"""
staging.py — copy exported FBX files into the O3DE project so AP can see them.

PURE (stdlib only). Deliberately runs outside the editor: putting the source
files and their `.assetinfo` sidecars in place is plain file I/O, and doing it
as a separate step means CI can run `AssetProcessorBatch` to completion *before*
the editor starts. The editor-side import then still calls `wait_for_asset`
before referencing any product (global constraint 8) -- it just usually returns
immediately, instead of being the only thing standing between the importer and
a missing-asset reference.

Product paths follow from where the files land: a source at
`<project>/Assets/uetoo3de/game/meshes/sm_letterf.fbx` produces
`assets/uetoo3de/game/meshes/sm_letterf.fbx.azmodel`, because the project root
is the scan folder and AP lowercases product paths (observed in S0.1 and S0.2).
"""

import json
import os
import shutil

from . import assetinfo
from . import gltf_source


class StagingError(Exception):
    pass


def fbx_lod_nodes(path, node_name):
    """`[<node>_LOD0, ...]` when the staged FBX carries a LOD chain, else [].

    The exporter wraps a multi-LOD bake in an FbxLODGroup whose children are
    named `<node>_LOD<i>` (measured: probe_write_lods). Binary FBX stores
    node names as plain strings, so a printable-strings scan finds them
    without an FBX parser -- the same instrument that located the lightmap
    UV layers. Contiguity from 0 is required: a file with _LOD0 and _LOD2
    but no _LOD1 is not a chain this pipeline wrote, and treating it as one
    would select a hole into the LodRule.
    """
    if not str(path).lower().endswith(".fbx"):
        return []
    with open(path, "rb") as handle:
        blob = handle.read()
    found = []
    index = 0
    while True:
        marker = ("%s_LOD%d" % (node_name, index)).encode("ascii")
        # Name strings are length-delimited, so the next byte after a real
        # node name is never another identifier character -- this rejects
        # `_LOD1` matching inside `_LOD10`... nothing yet writes 10 LODs,
        # but the check costs one byte.
        position = blob.find(marker)
        hit = False
        while position != -1:
            after = blob[position + len(marker):position + len(marker) + 1]
            if not after.isalnum() and after != b"_":
                hit = True
                break
            position = blob.find(marker, position + 1)
        if not hit:
            break
        found.append("%s_LOD%d" % (node_name, index))
        index += 1
    return found if len(found) >= 2 else []


def fbx_hull_nodes(path, node_name):
    """`[UCX_<node>_00, UCX_<node>_01, ...]` when the staged FBX carries UE's
    hull elements as collision nodes, else [].

    UE's FBX writer names simple-collision convex elements
    `UCX_<mesh>_<index>` with a two-digit index from 00 (its own import
    convention, read back the same way). Same printable-strings scan and the
    same contiguity/boundary rules as `fbx_lod_nodes`: a hole means a file
    this pipeline did not write, and `_01` must not match inside `_010`.
    """
    if not str(path).lower().endswith(".fbx"):
        return []
    with open(path, "rb") as handle:
        blob = handle.read()
    found = []
    index = 0
    while True:
        marker = ("UCX_%s_%02d" % (node_name, index)).encode("ascii")
        position = blob.find(marker)
        hit = False
        while position != -1:
            after = blob[position + len(marker):position + len(marker) + 1]
            if not after.isalnum() and after != b"_":
                hit = True
                break
            position = blob.find(marker, position + 1)
        if not hit:
            break
        found.append(marker.decode("ascii"))
        index += 1
    return found


def product_path_for(relative_path, product_prefix):
    """`uetoo3de/a/b.fbx` -> `assets/uetoo3de/a/b.fbx.azmodel` (lowercased)."""
    return ("%s/%s.azmodel" % (product_prefix, relative_path)).lower()


# Cooked physics-mesh product extension per backend. BOTH scene builders name
# their product from the FULL source filename, .fbx included, exactly as the
# azmodel builder does -- verified in both caches (`sm_rock.fbx.pxmesh`,
# `sm_carriage.fbx.joltmesh`), and contrast the EMotionFX builders, which drop
# the extension.
PHYSICS_PRODUCT_EXTENSION = {"physx": "pxmesh", "jolt": "joltmesh"}


def physics_product_path_for(relative_path, product_prefix, backend):
    """`uetoo3de/a/b.fbx` -> `assets/uetoo3de/a/b.fbx.<ext>` (lowercased)."""
    try:
        extension = PHYSICS_PRODUCT_EXTENSION[backend]
    except KeyError:
        raise StagingError("unknown physics backend %r" % (backend,))
    return ("%s/%s.%s" % (product_prefix, relative_path, extension)).lower()


def pxmesh_product_path_for(relative_path, product_prefix):
    """Back-compat alias for the PhysX product path."""
    return physics_product_path_for(relative_path, product_prefix, "physx")


# Which gem name prefix proves a backend's scene builder is present. Matched
# against `project.json` gem_names: "PhysX5" in 26.05 ("PhysX" in older
# engines), "JoltPhysics" for the Jolt gem.
_BACKEND_GEM_PREFIX = {"physx": "PhysX", "jolt": "JoltPhysics"}
_BACKEND_COOK_ENV = {"physx": "UEO3DE_PHYSX_COOK", "jolt": "UEO3DE_JOLT_COOK"}


def _builder_binaries(project_root, prefix):
    """Built `<prefix>*.Editor*.dll` files reachable by this project's AP.

    LISTED IS NOT BUILT, and the difference is invisible until far too late.
    A gem in `gem_names` whose code was never compiled leaves the Asset
    Processor with no serializer for its mesh group's `$type` -- and an
    unrecognised `.assetinfo` entry is SILENTLY DROPPED. Measured on a fresh
    project: `JoltPhysics==1.0.0` in `gem_names`, sidecars carrying
    `JoltMeshGroup`, 141 of 141 `.azmodel` built, **0 `.joltmesh`, 0 errors,
    and no job log mentioning Jolt at all.**

    Two places count, and the difference is why this only bites some backends:
    PhysX SHIPS WITH THE ENGINE (`PhysX.Editor.Gem.dll` sits in the engine's
    bin), so a PhysX project needs no build; Jolt is an EXTERNAL gem whose
    `JoltPhysics.Editor.dll` exists only after the project is built.
    """
    found = []
    roots = []
    build_root = os.path.join(project_root, "build")
    if os.path.isdir(build_root):
        roots.append(build_root)
    roots.extend(_engine_bin_roots())

    for root in roots:
        for directory, _subdirs, files in os.walk(root):
            for name in files:
                lowered = name.lower()
                if (lowered.startswith(prefix.lower())
                        and ".editor" in lowered and lowered.endswith(".dll")):
                    found.append(os.path.join(directory, name))
            if len(found) > 4:            # enough to answer the question
                return found
    return found


def _engine_bin_roots():
    """Every registered engine's `bin`, best effort.

    Read from the o3de manifest because staging runs OUTSIDE the editor, where
    no engine path is resolved for us. Returning nothing is fine -- the check
    that uses this only ever DOWNGRADES to "cannot prove it is built", and it
    says so rather than guessing.
    """
    manifest = os.path.join(os.path.expanduser("~"), ".o3de", "o3de_manifest.json")
    roots = []
    try:
        with open(manifest, "r") as handle:
            engines = json.load(handle).get("engines") or []
    except (OSError, ValueError):
        return roots
    for engine in engines:
        path = engine.get("path") if isinstance(engine, dict) else engine
        if not path:
            continue
        candidate = os.path.join(str(path), "bin")
        if os.path.isdir(candidate):
            roots.append(candidate)
    return roots


def backend_builder_missing(project_assets_root, backend):
    """Is `backend`'s gem listed but its builder nowhere to be found?

    True means every physics mesh group written for it will be silently
    dropped. False means either the builder is there or we could not look.
    """
    prefix = _BACKEND_GEM_PREFIX.get(backend)
    if not prefix:
        return False
    project_root = os.path.dirname(os.path.normpath(project_assets_root))
    return not _builder_binaries(project_root, prefix)


def project_physics_backends(project_assets_root):
    """Which physics backends' mesh groups this project's AP can cook.

    Decides which physics mesh groups a sidecar may carry. A project without a
    backend's gem has no serializer for that group's `$type`, so writing one
    would at best warn and at worst fail every static mesh's AP job -- and it
    could never cook the product anyway. Read from `project.json` because
    staging runs OUTSIDE the editor, where no gem registry is loaded.

    Returns a tuple in `assetinfo.BACKENDS` order, so sidecar bytes do not
    depend on the order gems happen to be listed in.

    `UEO3DE_PHYSX_COOK` / `UEO3DE_JOLT_COOK` override per backend, and they are
    not conveniences: O3DE activates gems TRANSITIVELY, so a project listing
    only a game gem whose `gem.json` depends on a physics gem runs that backend
    while this literal scan says no. The importer would then report
    PHYS_MESH_NOT_COOKED per asset and advise a restage that re-runs the same
    scan and can never fix it. The overrides are the way out, and
    PHYS_MESH_NOT_COOKED names them.
    """
    project_json = os.path.join(os.path.dirname(os.path.normpath(project_assets_root)),
                                "project.json")
    try:
        with open(project_json, "r") as handle:
            gems = json.load(handle).get("gem_names") or []
    except (OSError, ValueError):
        gems = []
    listed = []
    for gem in gems:
        name = gem.get("name", "") if isinstance(gem, dict) else gem
        # Version specifiers ("PhysX>=2.0") ride on the name in some templates.
        listed.append(str(name).split(">")[0].split("=")[0].strip())

    out = []
    for backend in ("physx", "jolt"):
        override = os.environ.get(_BACKEND_COOK_ENV[backend], "").strip().lower()
        if override in ("1", "on", "true", "yes"):
            out.append(backend)
            continue
        if override in ("0", "off", "false", "no"):
            continue
        if override:
            raise StagingError(
                "%s=%r is not understood; use 1/on/true or 0/off/false"
                % (_BACKEND_COOK_ENV[backend], override))
        prefix = _BACKEND_GEM_PREFIX[backend]
        if any(name.startswith(prefix) for name in listed):
            out.append(backend)
    return tuple(out)


def verify_builders_present(project_assets_root, backends):
    """Raise if a chosen backend's scene builder does not exist on disk.

    DELIBERATELY SEPARATE from `project_physics_backends`, which answers "what
    does this project DECLARE" and is pure enough to unit-test against a
    project.json in a temp directory. This answers "can the Asset Processor
    actually build it", which is a question about the machine. Folding the two
    together made every synthetic project fixture in the suite start raising --
    the fixtures were right and the merged function was wrong.

    Called from `stage()`, so a real staging run refuses in seconds instead of
    writing hundreds of sidecars whose physics groups are silently dropped.
    """
    for backend in backends:
        # AN EXPLICIT COOK=1 WINS. That override means "I know this backend is
        # active even though the scan cannot see it" -- the transitive-gem
        # case -- and the search below is a heuristic over dll NAMES and
        # directory layout. If it ever returns a false negative, an
        # unoverridable check would be a wall with no way past it. Every other
        # override in this module works the same way.
        if os.environ.get(_BACKEND_COOK_ENV[backend], "").strip().lower() in (
                "1", "on", "true", "yes"):
            continue
        prefix = _BACKEND_GEM_PREFIX.get(backend)
        if prefix and backend_builder_missing(project_assets_root, backend):
            raise StagingError(
                "%s is listed in project.json but no %s*.Editor*.dll was "
                "found in the project's build tree or any registered engine "
                "bin. Its scene builder does not exist, so every %s mesh "
                "group would be SILENTLY DROPPED -- the Asset Processor "
                "reports no error, produces no cooked mesh, and the imported "
                "level has no collision. Build the project (target "
                "%s.Editor), or set %s=0 to stage without it."
                % (prefix, prefix, backend, prefix, _BACKEND_COOK_ENV[backend]))


def project_has_physx_gem(project_assets_root):
    """Back-compat: does the project cook PhysX physics meshes?"""
    return "physx" in project_physics_backends(project_assets_root)


def skeletal_product_path_for(relative_path, product_prefix, kind):
    """`uetoo3de/a/b.fbx` -> `assets/uetoo3de/a/b.actor` / `.motion`.

    Unlike azmodel products, the EMotionFX builders name their products from
    the source STEM, dropping the .fbx (measured on the M8 probe: the default
    scene rules produced probe_character.actor and probe_anim.motion with no
    .assetinfo involved).
    """
    stem = relative_path.rsplit(".", 1)[0]
    suffix = "actor" if kind == "skeletal_mesh" else "motion"
    return ("%s/%s.%s" % (product_prefix, stem, suffix)).lower()


def stage(document, source_root, project_assets_root, log=None):
    """Stage everything a manifest references into the project.

    Static meshes: copy FBX + write `.assetinfo` (M2). Textures: copy the
    role-suffixed TGAs -- the suffix alone selects the Atom image preset, so
    no sidecar is needed (M4). Materials with material_data: write the
    StandardPBR `.material` file (M4); materials without stay unwritten so
    their entities keep the default material.

    Returns one record per staged file with kind + product_path; only records
    whose products the importer must wait on carry `wait: True` (models and
    materials -- image products are dependencies of the material job).
    """
    from . import material_build

    def emit(message):
        if log is not None:
            log(message)

    product_prefix = os.path.basename(os.path.normpath(project_assets_root)).lower()
    assets_by_guid = {a["guid"]: a for a in document["assets"]}
    cook_backends = project_physics_backends(project_assets_root)
    # Before writing a single sidecar: a declared-but-unbuilt physics gem takes
    # the whole physics pipeline down without one error message anywhere.
    verify_builders_present(project_assets_root, cook_backends)
    records = []

    for asset in document["assets"]:
        if asset["kind"] == "texture":
            relative_path = asset["o3de_relative_path"]
            source = os.path.join(source_root, relative_path).replace("\\", "/")
            if not os.path.exists(source):
                raise StagingError("exported texture missing for %s: %s"
                                   % (asset["ue_path"], source))
            staged = os.path.join(project_assets_root, relative_path).replace("\\", "/")
            os.makedirs(os.path.dirname(staged), exist_ok=True)
            shutil.copyfile(source, staged)
            records.append({"kind": "texture", "guid": asset["guid"],
                            "relative_path": relative_path, "staged": staged,
                            "wait": False})
            continue

        if asset["kind"] == "material":
            if not asset.get("material_data"):
                continue  # default material by design; nothing to write
            path = material_build.write(asset, assets_by_guid, project_assets_root)
            relative_path = asset["o3de_relative_path"]
            records.append({
                "kind": "material", "guid": asset["guid"],
                "relative_path": relative_path, "staged": path,
                "staged_fbx": path,  # wait_for_asset names this in timeouts
                "product_path": ("%s/%s" % (product_prefix,
                                            relative_path.rsplit(".", 1)[0]
                                            + ".azmaterial")).lower(),
                "wait": True,
            })
            emit("  %-42s -> %s" % (asset["ue_path"], records[-1]["product_path"]))
            continue

        if asset["kind"] in ("skeletal_mesh", "animation"):
            # Skeletal + animation FBX stage WITHOUT an .assetinfo: the
            # default scene rules already produce the .actor/.motion products
            # (measured, Tests/o3de/probe_m8_emfx.py prep), and an authored
            # manifest would have to reproduce EMotionFX group defaults for
            # no gain. Auto-generated azmaterials ride along; harmless.
            relative_path = asset["o3de_relative_path"]
            source_fbx = os.path.join(source_root, relative_path).replace("\\", "/")
            if not os.path.exists(source_fbx):
                raise StagingError(
                    "exported skeletal FBX is missing for %s: %s"
                    % (asset["ue_path"], source_fbx))
            staged_fbx = os.path.join(project_assets_root, relative_path).replace("\\", "/")
            os.makedirs(os.path.dirname(staged_fbx), exist_ok=True)
            shutil.copyfile(source_fbx, staged_fbx)
            record = {
                "kind": asset["kind"],
                "guid": asset["guid"],
                "ue_path": asset["ue_path"],
                "relative_path": relative_path,
                "source_fbx": source_fbx,
                "staged_fbx": staged_fbx,
                "product_path": skeletal_product_path_for(
                    relative_path, product_prefix, asset["kind"]),
                "wait": True,
            }
            records.append(record)
            emit("  %-42s -> %s" % (asset["ue_path"], record["product_path"]))
            continue

        if asset["kind"] != "static_mesh":
            continue

        relative_path = asset["o3de_relative_path"]
        source_fbx = os.path.join(source_root, relative_path).replace("\\", "/")
        if not os.path.exists(source_fbx):
            raise StagingError(
                "exported FBX is missing for %s: %s (run the UE export first)"
                % (asset["ue_path"], source_fbx))

        node_name = asset.get("fbx_node_name")
        if not node_name:
            raise StagingError(
                "%s has no fbx_node_name; the .assetinfo node path cannot be "
                "built and the Asset Processor job would fail" % asset["ue_path"])

        physics = assetinfo.physics_for_asset(asset) if cook_backends else None
        staged_fbx = os.path.join(project_assets_root, relative_path).replace("\\", "/")
        os.makedirs(os.path.dirname(staged_fbx), exist_ok=True)
        shutil.copyfile(source_fbx, staged_fbx)

        # A glTF's mesh node arrives UNNAMED from UE, and SceneAPI selects by
        # node -- so the sidecar written below would address nothing and the
        # AP job would fail with "wasn't found in the list of selected nodes".
        # Name it on the STAGED copy (never the export), so the file the sidecar
        # describes is the file the Asset Processor reads.
        if gltf_source.is_gltf_source(staged_fbx):
            meshes = gltf_source.mesh_node_count(staged_fbx)
            if meshes > 1:
                raise StagingError(
                    "%s has %d mesh nodes; this staging path names them all "
                    "%r, which would make the sidecar's selection ambiguous. "
                    "One mesh per file is the exporter's contract."
                    % (relative_path, meshes, node_name))
            gltf_source.name_mesh_nodes(staged_fbx, node_name)

        lod_nodes = fbx_lod_nodes(staged_fbx, node_name)
        hull_nodes = []
        if physics and physics.get("hull_nodes"):
            hull_nodes = fbx_hull_nodes(staged_fbx, node_name)
            if not hull_nodes:
                # UEO3DE_COLLISION=ue on a file exported without hull nodes:
                # say so and cook the whole-mesh hull rather than a group
                # that selects nothing (which fails the AP job outright).
                emit("  %s: UEO3DE_COLLISION=ue but the FBX carries no UCX_ "
                     "hull nodes (exported before hull export, or the asset "
                     "has none); cooking a single hull" % relative_path)
        sidecar = assetinfo.write(staged_fbx, node_name, physics=physics,
                                  backends=cook_backends, lod_nodes=lod_nodes,
                                  hull_nodes=hull_nodes)

        record = {
            "kind": "static_mesh",
            "guid": asset["guid"],
            "ue_path": asset["ue_path"],
            "relative_path": relative_path,
            "source_fbx": source_fbx,
            "staged_fbx": staged_fbx,
            "assetinfo": sidecar,
            "product_path": product_path_for(relative_path, product_prefix),
            "wait": True,
        }
        records.append(record)
        emit("  %-42s -> %s" % (asset["ue_path"], record["product_path"]))

    return records


def clear(project_assets_root, subfolder, log=None):
    """Remove a previously staged tree, for cold-cache runs.

    Only ever deletes inside `<project_assets_root>/<subfolder>`, and only a
    path that actually resolves under it -- this deletes files, so it refuses
    to act on anything it cannot prove is in bounds.
    """
    root = os.path.abspath(project_assets_root)
    target = os.path.abspath(os.path.join(root, subfolder))
    if not target.startswith(root + os.sep):
        raise StagingError("refusing to clear %r: outside %r" % (target, root))
    if os.path.isdir(target):
        shutil.rmtree(target)
        if log is not None:
            log("  cleared " + target)

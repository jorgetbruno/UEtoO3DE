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


class StagingError(Exception):
    pass


def product_path_for(relative_path, product_prefix):
    """`uetoo3de/a/b.fbx` -> `assets/uetoo3de/a/b.fbx.azmodel` (lowercased)."""
    return ("%s/%s.azmodel" % (product_prefix, relative_path)).lower()


def pxmesh_product_path_for(relative_path, product_prefix):
    """`uetoo3de/a/b.fbx` -> `assets/uetoo3de/a/b.fbx.pxmesh` (lowercased).

    The PhysX scene builder names its product from the FULL source filename,
    .fbx included, exactly as the azmodel builder does (verified in the
    UEtoO3DETest-PhysX cache: `sm_rock.fbx.pxmesh` beside `sm_rock.fbx.azmodel`;
    contrast the EMotionFX builders, which drop the extension).
    """
    return ("%s/%s.pxmesh" % (product_prefix, relative_path)).lower()


def project_has_physx_gem(project_assets_root):
    """Does the target project's gem list include a PhysX gem?

    Decides whether sidecars may carry a PhysX mesh group. A project without
    the gem (UEtoO3DETest-Jolt ships JoltPhysics INSTEAD of PhysX5) has no
    serializer for the group's $type, so writing one there would at best warn
    and at worst fail every static mesh's AP job -- and it could never cook
    the product anyway. Read from `project.json` because staging runs OUTSIDE
    the editor, where no gem registry is loaded.

    `UEO3DE_PHYSX_COOK` overrides in either direction, and it is not a
    convenience: O3DE activates gems TRANSITIVELY, so a project listing only a
    game gem whose `gem.json` depends on PhysX runs the PhysX backend while
    this literal scan says no. The importer would then report
    PHYS_MESH_NOT_COOKED per asset and advise a restage that re-runs the same
    scan and can never fix it. The override is the way out, and
    PHYS_MESH_NOT_COOKED names it.
    """
    override = os.environ.get("UEO3DE_PHYSX_COOK", "").strip().lower()
    if override in ("0", "off", "false", "no"):
        return False
    if override in ("1", "on", "true", "yes"):
        return True
    if override:
        raise StagingError(
            "UEO3DE_PHYSX_COOK=%r is not understood; use 1/on/true or "
            "0/off/false" % override)
    project_json = os.path.join(os.path.dirname(os.path.normpath(project_assets_root)),
                                "project.json")
    try:
        with open(project_json, "r") as handle:
            gems = json.load(handle).get("gem_names") or []
    except (OSError, ValueError):
        return False
    for gem in gems:
        name = gem.get("name", "") if isinstance(gem, dict) else gem
        # "PhysX5" in 26.05; "PhysX" in older engines. Version specifiers
        # ("PhysX>=2.0") ride on the name in some templates.
        if str(name).split(">")[0].split("=")[0].strip().startswith("PhysX"):
            return True
    return False


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
    cook_physics = project_has_physx_gem(project_assets_root)
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

        physics = assetinfo.physics_for_asset(asset) if cook_physics else None
        staged_fbx = os.path.join(project_assets_root, relative_path).replace("\\", "/")
        os.makedirs(os.path.dirname(staged_fbx), exist_ok=True)
        shutil.copyfile(source_fbx, staged_fbx)
        sidecar = assetinfo.write(staged_fbx, node_name, physics=physics)

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

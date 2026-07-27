"""
asset_wait.py — `wait_for_asset`, the Asset Processor barrier (constraint 8).

    "Asset Processor is asynchronous - treat it as a hard dependency, not a
     background detail. The importer must never add a component that references
     a product asset before that asset exists in the cache. Implement
     wait_for_asset(source_path, timeout) in M2 and use it everywhere (models,
     materials, cooked collider data). On timeout: fail loudly with the source
     path. Skipping this produces importers that pass on the second run and
     fail in CI."

Shared by M3-M7, so it lives on its own and takes the source path purely so a
timeout can name the file a human has to go look at -- the catalog itself only
knows product paths.

The failure this prevents is quiet: `GetAssetIdByPath` returns an invalid id
for an unprocessed asset, `SetComponentProperty` accepts it, and the result is
a Mesh component pointing at nothing. Nothing downstream errors; the level is
just empty. So a timeout here raises rather than returning a sentinel.
"""

import time


class AssetWaitTimeout(Exception):
    """A product asset never appeared in the catalog."""


def resolve(product_path):
    """Return the asset id if `product_path` is in the catalog, else None."""
    import azlmbr.asset as asset
    import azlmbr.bus as bus
    import azlmbr.math as math

    asset_id = asset.AssetCatalogRequestBus(
        bus.Broadcast, 'GetAssetIdByPath', product_path, math.Uuid(), False)
    if asset_id is None:
        return None
    # An invalid id still round-trips through GetAssetPathById as an empty
    # string, which is the only reliable "is this real" signal exposed here.
    path_back = asset.AssetCatalogRequestBus(bus.Broadcast, 'GetAssetPathById', asset_id)
    if not path_back:
        return None
    return asset_id


def wait_for_asset(product_path, timeout_seconds=180.0, source_path=None,
                   poll_frames=10, log=None):
    """Block until `product_path` is in the asset catalog; return its asset id.

    Raises AssetWaitTimeout naming the source file on the way out.
    """
    import azlmbr.legacy.general as general

    deadline = time.time() + timeout_seconds
    attempts = 0
    while True:
        asset_id = resolve(product_path)
        if asset_id is not None:
            if attempts and log is not None:
                log("    waited %d poll(s) for %s" % (attempts, product_path))
            return asset_id
        if time.time() >= deadline:
            raise AssetWaitTimeout(
                "product %r never appeared in the asset catalog after %.0fs. "
                "Source: %s. The Asset Processor may not be running, or the "
                "job failed -- check the AP log before assuming a timing issue."
                % (product_path, timeout_seconds, source_path or "<unknown>"))
        attempts += 1
        # Pumping the editor is what lets the catalog update at all; a plain
        # sleep here would spin until the deadline and then fail.
        general.idle_wait_frames(poll_frames)


def wait_for_all(records, timeout_seconds=180.0, log=None):
    """`wait_for_asset` over staged records; returns {guid: asset_id}."""
    resolved = {}
    for record in records:
        resolved[record["guid"]] = wait_for_asset(
            record["product_path"],
            timeout_seconds=timeout_seconds,
            source_path=record.get("staged_fbx") or record.get("source_fbx"),
            log=log)
    return resolved

"""
ueimporter — the O3DE-side importer for UEtoO3DE manifests.

Split so that everything which does not need an editor can be tested without
one, and so the Asset Processor barrier is impossible to skip by accident:

  manifest_io   load + refuse a manifest (schema version, Lane A/B rules)  [pure]
  assetinfo     the SceneAPI `.assetinfo` sidecar contract from LANE_B.md  [pure]
  staging       copy FBX + sidecars into the project so AP can see them    [pure]
  report        the importer's own coded warning channel                   [pure]
  asset_wait    wait_for_asset -- the AP barrier (constraint 8)            [azlmbr]
  prefab_build  entities, transforms, Mesh components, prefab save         [azlmbr]
  importer      orchestration                                             [azlmbr]

Packaged as a tool gem (constraint 4). The M2 tests add
`Editor/Scripts` to sys.path explicitly rather than relying on gem
registration, so the pipeline is testable before the gem is enabled in a
project; M10 wires the editor menu, which is what actually needs registration.
"""

__version__ = "0.6.0"

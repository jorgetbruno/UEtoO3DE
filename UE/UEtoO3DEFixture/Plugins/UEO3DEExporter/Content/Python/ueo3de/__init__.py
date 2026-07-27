"""
ueo3de — the UE-side exporter for the UEtoO3DE pipeline.

Layout, and the reason for it: everything except `ue_level` is pure Python
with no `unreal` import, so the manifest's coordinate conversion, asset
identity and warning catalogue can be re-derived and asserted by tests
running in a plain interpreter. Only `ue_level` needs an editor.

  lane_a    Lane A transform conversion (UE cm/left-handed -> O3DE m/right-handed)
  naming    stable asset GUIDs + deterministic path sanitization
  warnings  the `manifest.warnings[]` catalogue and collector
  manifest  schema version, float rounding, deterministic serialization
  ue_level  the level walk (imports `unreal`)

This package lives under the plugin's Content/Python so UE puts it on
sys.path whenever UEO3DEExporter is enabled.
"""

__version__ = "0.1.0"

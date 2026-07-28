"""
init_unreal.py — the plugin's editor startup hook (plan M10).

UE's PythonScriptPlugin runs `init_unreal.py` from the `Content/Python` folder
of every enabled plugin, which is the exporter's equivalent of O3DE's gem
bootstrap. One catch, measured in Tests/ue/probe_m10_ui.py: the plugin's
`Content/Python` was NOT on sys.path at all, because the .uplugin declared
`"CanContainContent": false` -- with no mounted content folder there is
nothing for the Python plugin to scan, and this file would never be read. The
flag is now true.

Kept deliberately thin: register the menu entry, import nothing heavy. The
exporter modules pull in the whole manifest machinery, and paying for that on
every editor launch to draw one menu item would be a poor trade.
"""

import traceback

import unreal

try:
    from ueo3de import ue_menu

    ue_menu.install()
except Exception:
    unreal.log_error("[UEO3DE] init_unreal failed (editor startup continues):\n"
                     + traceback.format_exc())

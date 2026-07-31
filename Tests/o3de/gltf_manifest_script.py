"""
gltf_manifest_script.py -- a SceneAPI ScriptProcessorRule script.

Runs INSIDE the Scene Builder, where the loaded scene and its graph are in
hand -- which is the one place the node paths a `.assetinfo` must name are
actually knowable. Four static guesses at a glTF node path were rejected
before this (see LANE_C_GLTF.md); this asks instead of guessing.

It writes every graph node's name and path to a log beside the source, so the
answer survives the build, and then builds the manifest FROM those paths. If
that works it is also the shipping design for glTF: paths computed at build
time cannot drift from what the importer assumed.

Defensive on purpose: the entry point's name is not documented in the
installed engine (headers only, no .cpp), so several candidates are defined
and the module logs at import time. A log with "loaded" but no callback line
means the rule wired up and the function name is wrong -- which is itself the
measurement.
"""

import os

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "gltf_manifest_script.log")


def _log(message):
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(str(message) + "\n")
    except Exception:
        pass


_log("=== loaded: %s" % __file__)


def _dump_builder_environment():
    """What the SCENE BUILDER's Python exposes -- not the editor's.

    The rule loads this file (proven: the line above appears), but neither
    OnUpdateManifest nor OnPrepareForExport is called, so the engine expects
    the script to announce itself some other way -- almost certainly by
    connecting to a notification bus. The builder's azlmbr differs from the
    editor's, so the names have to be read HERE.
    """
    try:
        import azlmbr
    except Exception as error:  # noqa: BLE001
        _log("azlmbr unavailable in the builder: %r" % (error,))
        return
    interesting = sorted(n for n in dir(azlmbr)
                         if any(word in n.lower()
                                for word in ("scene", "script", "builder", "asset")))
    _log("azlmbr modules: %s" % interesting)
    for name in interesting:
        try:
            module = getattr(azlmbr, name)
            members = sorted(m for m in dir(module)
                             if not m.startswith("_")
                             and any(word in m.lower()
                                     for word in ("bus", "notification", "script",
                                                  "manifest", "event")))
            if members:
                _log("  azlmbr.%s -> %s" % (name, members))
        except Exception as error:  # noqa: BLE001
            _log("  azlmbr.%s unreadable (%r)" % (name, error))
    try:
        import scene_api.scene_data as scene_data  # noqa: F401
        _log("scene_api imports OK in the builder")
    except Exception as error:  # noqa: BLE001
        _log("scene_api NOT importable in the builder: %r" % (error,))


_dump_builder_environment()


def _walk(scene):
    """Every (path, has_content) in the scene graph, depth first.

    Uses the RAW graph, not `scene_api.scene_data`: that helper package is not
    importable inside the Scene Builder (measured -- ModuleNotFoundError),
    only in the editor. The wrapper is a thin shim over exactly these calls.
    """
    rows = []
    try:
        graph = scene.graph
    except Exception as error:  # noqa: BLE001
        _log("scene.graph unreadable: %r" % (error,))
        return rows

    try:
        root = graph.GetRoot()
    except Exception as error:  # noqa: BLE001
        _log("GetRoot failed: %r" % (error,))
        return rows

    stack = [root]
    seen = 0
    while stack and seen < 500:
        current = stack.pop()
        seen += 1
        try:
            name = graph.GetNodeName(current)
            path = name.GetPath() if hasattr(name, "GetPath") else str(name)
            rows.append((str(path), graph.HasNodeContent(current)))
        except Exception as error:  # noqa: BLE001
            _log("node read failed: %r" % (error,))
        try:
            if graph.HasNodeChild(current):
                stack.append(graph.GetNodeChild(current))
            if graph.HasNodeSibling(current):
                stack.append(graph.GetNodeSibling(current))
        except Exception as error:  # noqa: BLE001
            _log("traversal failed: %r" % (error,))
            break
    return rows


def _report(scene, where):
    _log("--- %s" % where)
    try:
        _log("scene name: %s" % scene.name)
    except Exception:  # noqa: BLE001
        pass
    for path, has_content in _walk(scene):
        _log("  node %-60s content=%s" % (path, has_content))


def _on_update_manifest(args):
    """ScriptBuildingNotificationBus callback. `args[0]` is the loaded scene."""
    try:
        scene = args[0]
    except Exception as error:  # noqa: BLE001
        _log("callback args unusable: %r" % (args,))
        raise error
    _report(scene, "OnUpdateManifest")
    # Returning the manifest unchanged: this run exists to READ the graph, and
    # a manifest invented before the node paths are known is how the last four
    # attempts went wrong.
    try:
        return scene.manifest.ExportToJson()
    except Exception as error:  # noqa: BLE001
        _log("could not re-export the existing manifest: %r" % (error,))
        return ""


# The engine does not call bare module functions -- measured: the module loads
# but neither OnUpdateManifest nor OnPrepareForExport is invoked. It expects a
# handler CONNECTED to ScriptBuildingNotificationBus, which is what the
# builder's azlmbr exposes (and `scene_api`, which wraps this, is not
# importable here).
try:
    import azlmbr.scene as _scene_api

    _handler = _scene_api.ScriptBuildingNotificationBusHandler()
    _handler.connect()
    _handler.add_callback("OnUpdateManifest", _on_update_manifest)
    _log("connected to ScriptBuildingNotificationBus")
except Exception as _error:  # noqa: BLE001
    _log("could not connect the notification handler: %r" % (_error,))

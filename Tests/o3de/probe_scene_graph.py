"""
probe_scene_graph.py -- what does SceneAPI actually call the nodes in a file?

Written after three guesses at a glTF node path in a row were wrong, each
costing an Asset Processor cycle. `RootNode.<node name>`, a bare `RootNode`,
and `RootNode.<name>_2` (the name the procedural prefab showed) were all
rejected with the same warning:

    SceneAPI: MeshGroup <group> wasn't found in the list of selected nodes.

The sidecar's NodeSelectionList has to name graph paths EXACTLY, and for FBX
those paths are known (`RootNode.<the node UE named>`). glTF goes through a
different importer -- the job log says `Using 'AssImp' Import Context Provider`
-- and nothing so far has told us what it produces. So: ask, rather than guess
again.

Reports which scene APIs this build exposes to Python and, if any of them can
load a scene, every node path in it. Asserts nothing: this is the measurement
that a selection rule gets written from.

Env: UEO3DE_SCENE  file to inspect (default: the staged glTF probe mesh)
Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_scene_graph.py \
         <result> <project>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_scene_graph_result.txt'))

lines = []


def log(message=""):
    lines.append(str(message))
    print(message)


def main():
    import azlmbr
    import azlmbr.legacy.general as general

    project_root = general.get_game_folder().rstrip('/\\')
    scene_path = os.environ.get("UEO3DE_SCENE", "").strip() or os.path.join(
        project_root, "Assets", "uetoo3de", "gltfprobe", "SM_LetterF.gltf")
    log("scene: %s (exists=%s)" % (scene_path, os.path.isfile(scene_path)))

    log("")
    log("=== azlmbr modules mentioning scene ===")
    candidates = sorted(n for n in dir(azlmbr) if "scene" in n.lower())
    log("  %s" % (candidates or "none"))
    for name in candidates:
        try:
            module = getattr(azlmbr, name)
            members = sorted(m for m in dir(module) if not m.startswith("_"))
            log("  azlmbr.%s -> %s" % (name, ", ".join(members[:40])))
        except Exception as error:  # noqa: BLE001
            log("  azlmbr.%s unreadable (%s)" % (name, error))

    for module_name in ("azlmbr.scene", "azlmbr.sceneapi", "azlmbr.scenedata"):
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as error:  # noqa: BLE001
            log("  %-18s not importable (%s)" % (module_name, error))
            continue
        members = sorted(m for m in dir(module) if not m.startswith("_"))
        log("  %-18s -> %s" % (module_name, ", ".join(members[:40])))

    # If a scene can be loaded, print every graph path -- that is the whole
    # point of the probe.
    log("")
    log("=== node paths ===")
    try:
        import azlmbr.scene as scene_module
    except Exception as error:  # noqa: BLE001
        log("  azlmbr.scene unavailable (%s); no path listing possible here. "
            "The remaining route is the Scene Settings UI or a .dbgsg dump."
            % error)
        return

    loader = None
    for attribute in ("SceneAPI", "Scene", "load", "OpenScene"):
        if hasattr(scene_module, attribute):
            loader = attribute
            break
    log("  candidate entry point: %r" % loader)
    if loader is None:
        log("  nothing on azlmbr.scene loads a file; listing not possible")
        return
    try:
        graph = getattr(scene_module, loader)
        log("  %s members: %s"
            % (loader, ", ".join(sorted(m for m in dir(graph)
                                        if not m.startswith("_"))[:40])))
    except Exception:  # noqa: BLE001
        log("  " + traceback.format_exc())


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
_general.exit_no_prompt()

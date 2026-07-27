# S0.2 (Lane B), scene-level dump: parse SM_LetterF.fbx through SceneAPI's own reader
# (azlmbr.scene) and dump node structure + mesh vertex ranges. Comparing this with the
# raw FBX data (probe_fbx_globalsettings.py) and the product-side artifacts
# (abdata.json dimension, procprefab identity transform) closes the Lane B chain:
#   UE asset -> FBX file -> SceneAPI scene -> .azmodel product
import os
import sys
import traceback

RESULT_PATH = (
    sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-")
    else r"D:/Gamedev/UEtoO3DE/Tests/o3de/results/lane_b_scene_result.txt"
)
FBX_PATH = r"C:/Users/jorge/O3DE/Projects/UEtoO3DETest-Jolt/Assets/UEtoO3DE/SM_LetterF.fbx"

lines = []


def log(m):
    lines.append(str(m))
    print(m)


def main():
    import azlmbr.scene as sceneApi
    import azlmbr.legacy.general as general

    general.idle_enable(True)

    scene = sceneApi.Scene(FBX_PATH)
    graph = scene.graph

    stats = {"meshes": 0}

    def name_str(node_name):
        for attr in ("get_path", "GetPath"):
            fn = getattr(node_name, attr, None)
            if fn:
                return fn()
        return str(node_name)

    def walk(node, depth):
        node_name = name_str(graph.GetNodeName(node))
        info = ["  " * depth + "node: " + node_name]
        if graph.HasNodeContent(node):
            content = graph.GetNodeContent(node)
            attrs = [a for a in dir(content) if not a.startswith("_")]
            info.append("  " * depth + "  content: " + type(content).__name__ + " " + str(attrs)[:200])
            get_count = getattr(content, "GetVertexCount", None)
            get_pos = getattr(content, "GetPosition", None)
            if get_count and get_pos:
                n = get_count()
                xs, ys, zs = [], [], []
                for i in range(n):
                    p = get_pos(i)
                    xs.append(p.x); ys.append(p.y); zs.append(p.z)
                info.append("  " * depth + "  vertices=%d x[%.3f, %.3f] y[%.3f, %.3f] z[%.3f, %.3f] centroid(%.3f, %.3f, %.3f)"
                            % (n, min(xs), max(xs), min(ys), max(ys), min(zs), max(zs),
                               sum(xs) / n, sum(ys) / n, sum(zs) / n))
                stats["meshes"] += 1
        for line in info:
            log(line)
        if graph.HasNodeChild(node):
            walk(graph.GetNodeChild(node), depth + 1)
        if graph.HasNodeSibling(node):
            walk(graph.GetNodeSibling(node), depth)

    root = graph.GetRoot()
    walk(root, 0)
    log("mesh nodes found: %d" % stats["meshes"])
    if stats["meshes"] == 0:
        raise RuntimeError("no mesh nodes found in scene graph")


ok = True
try:
    main()
except Exception:
    ok = False
    log("EXCEPTION: " + traceback.format_exc())

log("RESULT: " + ("PASS" if ok else "FAIL"))
os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
with open(RESULT_PATH, "w") as f:
    f.write("\n".join(lines))

import azlmbr.legacy.general as g

if ok:
    g.exit_no_prompt()
else:
    os._exit(1)

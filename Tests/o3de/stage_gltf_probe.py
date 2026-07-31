"""stage_gltf_probe.py -- put the glTF fixture where the basis probes can see it.

`probe_gltf_basis.py` and `probe_gltf_vertices.py` compare the glTF and FBX
products of the SAME mesh. The FBX side is staged by M2; the glTF side is this.
Without it both probes fail loudly with "product missing", which is correct but
unhelpful if the staging step lives only in someone's shell history.

It stages `Tests/ue/data/SM_LetterF.glb` -- UE 5.8's own export, committed --
through the REAL importer code (`gltf_source.name_mesh_nodes` +
`assetinfo.write`), not a hand-written sidecar. A probe fed by a hand-made
sidecar measures the sidecar, not the pipeline.

    python Tests/o3de/stage_gltf_probe.py [<project>]          stage
    python Tests/o3de/stage_gltf_probe.py --clean [<project>]   remove

Run AssetProcessorBatch after either, then the probes:

    <O3DE_BIN>/AssetProcessorBatch.exe --project-path=<project> --platforms=pc
    Tests/o3de/run_o3de_python.bat Tests/o3de/probe_gltf_basis.py <result> <project>
    python Tests/o3de/probe_gltf_vertices.py <project>

CLEAN UP WHEN DONE. The staged asset is shared state in a project every other
suite also uses -- run_perf.bat carries a long comment about exactly this class
of leak, where cook-disabled staging left M7's terrain with no collision and a
failure that looked unrelated to anything M7 did.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))   # .../Tests/o3de/<this> -> repo
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

from ueimporter import assetinfo, gltf_source  # noqa: E402

FIXTURE = os.path.join(REPO_ROOT, "Tests", "ue", "data", "SM_LetterF.glb")
STAGE_REL = os.path.join("Assets", "uetoo3de", "glbprobe")
NODE_NAME = "SM_LetterF"
DEFAULT_PROJECT = r"C:\Users\jorge\O3DE\Projects\UEtoO3DETest-Jolt"


def main(argv):
    clean = "--clean" in argv
    positional = [a for a in argv if not a.startswith("-")]
    project = (positional[0] if positional else
               os.environ.get("O3DE_PROJECT_JOLT") or DEFAULT_PROJECT)
    stage_dir = os.path.join(project, STAGE_REL)

    if clean:
        import shutil
        if os.path.isdir(stage_dir):
            shutil.rmtree(stage_dir)
            print("removed %s" % stage_dir)
        else:
            print("nothing staged at %s" % stage_dir)
        print("now re-run AssetProcessorBatch so the products go too")
        return 0

    if not os.path.isfile(FIXTURE):
        print("FAIL: missing %s -- it is committed, so the working tree is "
              "incomplete" % FIXTURE)
        return 1
    if not os.path.isdir(project):
        print("FAIL: no such project %s" % project)
        return 1

    os.makedirs(stage_dir, exist_ok=True)
    staged = os.path.join(stage_dir, os.path.basename(FIXTURE))
    import shutil
    shutil.copyfile(FIXTURE, staged)

    meshes = gltf_source.mesh_node_count(staged)
    if meshes != 1:
        print("FAIL: %s has %d mesh nodes; the probes assume one" % (staged, meshes))
        return 1
    renamed = gltf_source.name_mesh_nodes(staged, NODE_NAME)
    print("staged %s" % staged)
    print("  mesh nodes: %d, renamed: %d, node name now %r"
          % (meshes, renamed, gltf_source.load_document(staged)["nodes"][0].get("name")))

    physics = {"method": "trimesh", "elements": 0, "decompose_hulls": None}
    sidecar = assetinfo.write(staged, NODE_NAME, physics=physics,
                              backends=("jolt", "physx"))
    print("  sidecar %s" % os.path.basename(sidecar))
    print("")
    print("next: AssetProcessorBatch.exe --project-path=%s --platforms=pc" % project)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

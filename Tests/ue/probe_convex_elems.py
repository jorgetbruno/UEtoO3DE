# Probe: can UE's ACTUAL convex decomposition be read out of Python at all?
#
# The manifest carries each convex element's vertex COUNT and AABB, never its
# vertices, so the importer substitutes one convex hull of the whole render
# mesh and every concavity UE decomposed away is filled back in (1,066 times on
# one converted level). Two routes out, and they cost very differently:
#
#   * read the real hulls here and export them, which needs KConvexElem's
#     vertex data to be reachable from Python -- unknown, and this probe is
#     the cheapest way to find out;
#   * cook-time V-HACD in the O3DE gems, which both support: approximate,
#     already implemented, never measured.
#
# Reports, per static mesh: the simple-collision composition (boxes, spheres,
# sphyls, convex) and, for every convex element, whether its vertices can be
# read and how many there are. Also dumps what KConvexElem exposes, because a
# property that exists under a different name is the difference between "not
# possible" and "not tried".
#
# Asserts nothing. Run: Tests/ue/run_ue_python.bat Tests/ue/probe_convex_elems.py
import unreal

OUT = r"D:/Gamedev/UEtoO3DE/Tests/ue/results/convex_elems_probe.txt"

lines = []


def log(message):
    lines.append(str(message))
    unreal.log(str(message))


log("=== what a KConvexElem exposes ===")
try:
    log("  " + ", ".join(sorted(n for n in dir(unreal.KConvexElem)
                                if not n.startswith("_"))))
except Exception as error:  # noqa: BLE001
    log("  unreal.KConvexElem is not exposed: %s" % error)

log("")
log("=== ways a convex element might yield vertices ===")
for name in ("get_convex_vertices", "get_vertices", "vertex_data", "vertices"):
    log("  KConvexElem.%-20s %s"
        % (name, hasattr(unreal.KConvexElem, name)
           if hasattr(unreal, "KConvexElem") else "n/a"))
# The BodySetup-level helpers are the other candidate route.
for holder, name in (("KAggregateGeom", "convex_elems"),
                     ("BodySetup", "agg_geom"),
                     ("StaticMesh", "body_setup")):
    obj = getattr(unreal, holder, None)
    log("  %-16s.%-14s %s" % (holder, name, hasattr(obj, name) if obj else "n/a"))

registry = unreal.AssetRegistryHelpers.get_asset_registry()
registry.wait_for_completion()
meshes = registry.get_assets_by_class(
    unreal.TopLevelAssetPath("/Script/Engine", "StaticMesh"), search_sub_classes=True)

log("")
log("=== simple collision, per static mesh ===")
totals = {"box": 0, "sphere": 0, "sphyl": 0, "convex": 0}
readable = 0
unreadable = 0
for data in meshes:
    path = str(data.get_editor_property("package_name"))
    if path.startswith("/Engine/"):
        continue
    asset = data.get_asset()
    if asset is None:
        continue
    try:
        body_setup = asset.get_editor_property("body_setup")
    except Exception as error:  # noqa: BLE001
        log("  %-48s body_setup unreadable (%s)" % (path, error))
        continue
    if body_setup is None:
        log("  %-48s no body setup" % path)
        continue
    try:
        geom = body_setup.get_editor_property("agg_geom")
    except Exception as error:  # noqa: BLE001
        log("  %-48s agg_geom unreadable (%s)" % (path, error))
        continue

    counts = {}
    for key, prop in (("box", "box_elems"), ("sphere", "sphere_elems"),
                      ("sphyl", "sphyl_elems"), ("convex", "convex_elems")):
        try:
            elems = geom.get_editor_property(prop)
        except Exception:  # noqa: BLE001
            elems = []
        counts[key] = len(elems) if elems else 0
        totals[key] += counts[key]
        if key == "convex" and counts[key]:
            for index, elem in enumerate(elems):
                got = None
                for accessor in ("vertex_data", "vertices"):
                    try:
                        got = elem.get_editor_property(accessor)
                        if got is not None:
                            break
                    except Exception:  # noqa: BLE001
                        continue
                if got is None:
                    unreadable += 1
                    log("    %s convex[%d]: vertices NOT readable" % (path, index))
                else:
                    readable += 1
                    log("    %s convex[%d]: %d vertices readable"
                        % (path, index, len(got)))
    log("  %-48s box=%d sphere=%d sphyl=%d convex=%d"
        % (path, counts["box"], counts["sphere"], counts["sphyl"], counts["convex"]))

log("")
log("totals: %r" % totals)
log("convex elements with readable vertices: %d | unreadable: %d"
    % (readable, unreadable))
log("")
log("Reading it: no convex elements at all in this project means the real-hull "
    "route cannot be developed or tested here regardless of the API, and "
    "cook-time V-HACD is the only route that can be measured on content that "
    "exists.")

with open(OUT, "w") as handle:
    handle.write("\n".join(lines))
unreal.log("PROBE WROTE %s" % OUT)

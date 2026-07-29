"""
probe_terrain_misses.py — is a terrain sampling miss "outside the landscape"
or "the trace is broken"?

`mesh_export._terrain_grid` samples a Landscape's height on a regular grid by
tracing each collision component, and refuses to ship the result if more than
5% of samples come back empty:

    terrain grid 190x159 (200 cm spacing), 30210 samples, 3040 misses
    MeshExportError: terrain sampling missed 3040 of 30210 points (>5%);
                     the collision lookup or trace is broken

The refusal is right in principle -- a guessed surface is worse than no
surface -- but the count it refuses on cannot tell two very different things
apart, because `height()` returns None for both:

  A. NO COMPONENT covers (x, y). The grid spans the landscape's BOUNDING BOX,
     so any landscape that is not a filled rectangle has samples in the gaps.
     Nothing is broken; there is simply no terrain there.
  B. A component covers (x, y) but the trace returned no hit. That is the
     broken case the message describes -- missing collision, a bad trace
     range, a physics scene that never came up.

10% misses means very different things in those two worlds. This measures the
split, and reports where the misses are, so the fix is chosen from evidence
rather than from whichever cause is easier to imagine.

Args:  --map=/Game/... --result=<path>   (forward slashes)
Run:   UnrealEditor.exe <project>.uproject \\
           -ExecutePythonScript="<repo>/Tests/ue/probe_terrain_misses.py ..." \\
           -unattended -nop4 -nosplash
"""

import os
import sys
import traceback

import unreal

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE = os.path.join(REPO, "UE", "UEtoO3DEFixture", "Plugins", "UEO3DEExporter",
                       "Content", "Python")
if PACKAGE not in sys.path:
    sys.path.insert(0, PACKAGE)


def parse_args(argv):
    options = {"map": "", "result": ""}
    unknown = []
    for token in argv:
        if not token.startswith("--"):
            unknown.append(token)
            continue
        key, sep, value = token[2:].partition("=")
        if key not in options or not sep:
            unknown.append(token)
        else:
            options[key] = value
    if unknown:
        raise ValueError("unrecognized argument(s): %s"
                         % ", ".join(repr(u) for u in unknown))
    return options


def main():
    options = parse_args(sys.argv[1:])
    lines = []
    status = "PASS"

    def log(message):
        lines.append(str(message))
        unreal.log("[probe_terrain] " + str(message))

    try:
        from ueo3de import mesh_export

        if options["map"]:
            unreal.EditorLoadingAndSavingUtils.load_map(options["map"])

        actors = unreal.EditorLevelLibrary.get_all_level_actors()
        landscapes = [a for a in actors
                      if a.get_class().get_name() in ("Landscape", "LandscapeProxy",
                                                      "LandscapeStreamingProxy")]
        log("level actors: %d, landscapes: %d" % (len(actors), len(landscapes)))
        if not landscapes:
            log("no Landscape in this level; nothing to measure")
            raise SystemExit(0)

        for actor in landscapes:
            log("")
            log("=== %s (%s) ===" % (actor.get_actor_label(),
                                     actor.get_class().get_name()))
            origin, extent = actor.get_actor_bounds(False)
            z_top = origin.z + extent.z + 1000.0
            z_bottom = origin.z - extent.z - 1000.0
            log("  bounds origin (%.0f, %.0f, %.0f) extent (%.0f, %.0f, %.0f)"
                % (origin.x, origin.y, origin.z, extent.x, extent.y, extent.z))

            components = list(actor.get_components_by_class(
                unreal.LandscapeHeightfieldCollisionComponent) or [])
            log("  heightfield collision components: %d" % len(components))
            if not components:
                log("  -> no components at all; that is case B in the extreme")
                continue

            lookup = []
            for component in components:
                bounds = unreal.SystemLibrary.get_component_bounds(component)
                lookup.append((component, bounds[0], bounds[1]))

            # Total area the components actually cover, against the area the
            # grid spans. A non-rectangular landscape shows up here first.
            covered = 0.0
            for _c, c_origin, c_extent in lookup:
                covered += (2 * c_extent.x) * (2 * c_extent.y)
            spanned = (2 * extent.x) * (2 * extent.y)
            log("  component area / bounding-box area = %.1f%%  (%.0f of %.0f m^2)"
                % (100.0 * covered / spanned if spanned else 0.0,
                   covered / 10000.0, spanned / 10000.0))

            spacing = mesh_export.TERRAIN_SPACING_CM
            min_x, max_x = origin.x - extent.x, origin.x + extent.x
            min_y, max_y = origin.y - extent.y, origin.y + extent.y
            nx = max(2, int(round((max_x - min_x) / spacing)))
            ny = max(2, int(round((max_y - min_y) / spacing)))

            outside = 0     # case A: no component covers this (x, y)
            failed = 0      # case B: a component covers it, trace found nothing
            hit = 0
            for j in range(ny + 1):
                y = min_y + (max_y - min_y) * j / ny
                for i in range(nx + 1):
                    x = min_x + (max_x - min_x) * i / nx
                    component = None
                    for candidate, c_origin, c_extent in lookup:
                        if (abs(x - c_origin.x) <= c_extent.x + 1.0
                                and abs(y - c_origin.y) <= c_extent.y + 1.0):
                            component = candidate
                            break
                    if component is None:
                        outside += 1
                        continue
                    z = mesh_export._trace_component_height(
                        component, x, y, z_top, z_bottom)
                    if z is None:
                        failed += 1
                    else:
                        hit += 1
            total = (nx + 1) * (ny + 1)
            log("  grid %dx%d = %d samples" % (nx + 1, ny + 1, total))
            log("    hit                              %6d  %5.1f%%"
                % (hit, 100.0 * hit / total))
            log("    A: outside every component       %6d  %5.1f%%"
                % (outside, 100.0 * outside / total))
            log("    B: covered but trace found none  %6d  %5.1f%%"
                % (failed, 100.0 * failed / total))
            log("")
            if failed == 0 and outside:
                log("  VERDICT: every miss is case A. The landscape is not a filled "
                    "rectangle; nothing is broken. The 5%% guard is counting "
                    "empty space as failure.")
            elif failed and not outside:
                log("  VERDICT: every miss is case B -- the trace really is failing "
                    "where terrain exists.")
            elif failed:
                log("  VERDICT: MIXED -- %d outside, %d genuinely failed. The "
                    "guard must count only the %d." % (outside, failed, failed))
            else:
                log("  VERDICT: no misses at all here.")
    except SystemExit:
        pass
    except Exception:
        log("PROBE FAILED")
        log(traceback.format_exc())
        unreal.log_error("[probe_terrain] " + traceback.format_exc())
        status = "FAIL"

    lines.append("RESULT: " + status)
    result_path = options["result"] or os.path.join(REPO, "probe_terrain_result.txt")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(result_path)), exist_ok=True)
        with open(result_path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except Exception:
        unreal.log_error("[probe_terrain] could not write " + str(result_path))

    print("RESULT: " + status)
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception:
        pass
    return 0 if status == "PASS" else 1


raise SystemExit(main())

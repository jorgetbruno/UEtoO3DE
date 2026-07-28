"""
probe_m7_create.py — M7: can Python CREATE a landscape in the fixture?

The fixture discipline wants a scripted, reproducible canary. Landscape
creation from Python is historically hard; this measures what 5.8 exposes:

  1. every unreal.* name mentioning landscape, and every editor/engine
     subsystem with landscape in its class name;
  2. spawn a bare Landscape actor: what does it have, which import-flavoured
     methods exist on it;
  3. the known route, attempted end-to-end: fill a RenderTarget2D with a
     height gradient (flat ramp) and call
     landscape_import_heightmap_from_render_target.

Output: Tests/ue/results/probe_m7_create.txt (runs on the FIXTURE project)
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m7_create.txt"
MAP_PATH = "/Game/Maps/Fixture_01"

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def main():
    names = sorted(n for n in dir(unreal) if "andscape" in n)
    out("unreal landscape names (%d): %r" % (len(names), names[:40]))

    for getter, label in ((unreal.get_editor_subsystem, "editor"),
                          (unreal.get_engine_subsystem, "engine")):
        for name in names:
            cls = getattr(unreal, name, None)
            if cls is None or not isinstance(cls, type):
                continue
            if "Subsystem" in name:
                try:
                    instance = getter(cls)
                    out("%s subsystem %s -> %r" % (label, name, instance))
                    if instance is not None:
                        interesting = [m for m in dir(instance)
                                       if any(k in m.lower() for k in
                                              ("create", "import", "new", "generate"))]
                        out("   methods: %r" % interesting)
                except Exception:
                    pass

    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    out("")
    out("=== spawn a bare Landscape ===")
    landscape = actor_sub.spawn_actor_from_class(
        unreal.Landscape, unreal.Vector(0.0, -3000.0, 0.0))
    out("spawned: %r" % landscape)
    if landscape is not None:
        methods = [m for m in dir(landscape)
                   if "import" in m.lower() or "heightmap" in m.lower()]
        out("import/heightmap methods: %r" % methods)
        components = list(landscape.get_components_by_class(unreal.LandscapeComponent) or [])
        out("components after bare spawn: %d" % len(components))

        out("")
        out("=== attempt heightmap import from a render target ===")
        try:
            target = unreal.RenderTarget2D()
            target.set_editor_property("size_x", 128)
            target.set_editor_property("size_y", 128)
            target.set_editor_property("render_target_format",
                                       unreal.TextureRenderTargetFormat.RTF_RGBA16F)
            worked = landscape.landscape_import_heightmap_from_render_target(
                target, False)
            out("import_heightmap_from_render_target -> %r" % worked)
            components = list(landscape.get_components_by_class(unreal.LandscapeComponent) or [])
            out("components after import: %d" % len(components))
        except Exception as exc:
            out("import RAISED %s: %s" % (type(exc).__name__, str(exc)[:180]))

        actor_sub.destroy_actor(landscape)
    # Never save: this probe must leave the fixture untouched.


status = "PASS"
try:
    main()
except Exception:
    out("FATAL: " + traceback.format_exc())
    status = "FAIL"

_lines.append("RESULT: " + status)
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as handle:
    handle.write("\n".join(_lines) + "\n")
print("RESULT: " + status)
if status != "PASS":
    raise SystemExit(1)

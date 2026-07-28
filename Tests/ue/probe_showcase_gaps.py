"""
probe_showcase_gaps.py — L_Showcase's two remaining gaps, measured in place.

Runs against the EasternProvince project (invoked directly, the way
export_level.bat does).

1. **Blueprint actors** (107 unmapped): what components do they actually
   carry? For each unmapped class in the level: every StaticMeshComponent
   (mesh path, per-slot materials, relative transform, mobility, collision),
   whether any are InstancedStaticMeshComponent / HISM (instance counts), and
   whatever non-mesh components exist (so the export can say what it skipped).

2. **MI_Plaster / MI_Plaster1**: their masters drive the material-attributes
   pin through a MaterialFunctionCall. Dump: which function, whether its
   internal expression list is reachable from Python
   (`function.get_editor_property(...)` candidates), the function's output
   expression and what feeds it -- everything the classifier needs to follow
   the pin one level deeper.

Output: Tests/ue/results/probe_showcase_gaps.txt
"""

import os
import sys
import traceback

import unreal

sys.path.insert(0, "D:/Gamedev/UEtoO3DE/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python")

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_showcase_gaps.txt"
MAP_PATH = "/Game/EasternProvince/Levels/L_Showcase"
PLASTERS = ["/Game/EasternProvince/Materials/MI_Plaster",
            "/Game/EasternProvince/Materials/MI_Plaster1"]

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def probe_blueprints():
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not level_sub.load_level(MAP_PATH):
        raise RuntimeError("failed to load " + MAP_PATH)

    known = ("StaticMeshActor", "Light", "SkyLight", "ExponentialHeightFog",
             "SkyAtmosphere", "PostProcessVolume", "TriggerBase")
    seen_classes = {}
    for actor in actor_sub.get_all_level_actors():
        if any(isinstance(actor, getattr(unreal, name))
               for name in known if getattr(unreal, name, None)):
            continue
        cls = actor.get_class().get_name()
        if cls in seen_classes:
            seen_classes[cls] += 1
            continue
        seen_classes[cls] = 1
        out("--- %s (first: %s) ---" % (cls, actor.get_actor_label()))
        components = actor.get_components_by_class(unreal.SceneComponent)
        for component in components or []:
            kind = component.get_class().get_name()
            line = "    %-34s %s" % (component.get_name(), kind)
            if isinstance(component, unreal.StaticMeshComponent):
                mesh = component.get_editor_property("static_mesh")
                line += " mesh=%s" % (mesh.get_name() if mesh else None)
                if isinstance(component, unreal.InstancedStaticMeshComponent):
                    line += " instances=%d" % component.get_instance_count()
                mobility = component.get_editor_property("mobility")
                line += " mobility=%s slots=%d" % (mobility, component.get_num_materials())
            out(line)
    out("")
    out("class counts: %r" % seen_classes)


def dump_function_call(master, node, depth=0):
    pad = "  " * depth + "    "
    function = node.get_editor_property("material_function")
    out(pad + "function: %s" % (function.get_name() if function else None))
    if function is None:
        return
    # Which property names expose the function's internals?
    for prop in ("function_expressions", "expressions",
                 "function_editor_only_data", "editor_only_data"):
        try:
            value = function.get_editor_property(prop)
            if value is None:
                out(pad + "%s = None" % prop)
                continue
            try:
                items = list(value)
                out(pad + "%s: %d entries" % (prop, len(items)))
                outputs = [x for x in items
                           if x is not None and "FunctionOutput" in x.get_class().get_name()]
                for output in outputs:
                    out(pad + "  output %r %s" % (
                        str(output.get_editor_property("output_name")),
                        output.get_class().get_name()))
                    mel = unreal.MaterialEditingLibrary
                    names = [str(n) for n in
                             (mel.get_material_expression_input_names(output) or [])]
                    inputs = list(mel.get_inputs_for_material_expression(master, output) or [])
                    for name, expr in zip(names, inputs):
                        out(pad + "    input %r -> %s" % (
                            name, expr.get_class().get_name() if expr else None))
                        if expr is not None and depth < 3:
                            walk_expression(master, expr, depth + 2)
            except TypeError:
                out(pad + "%s = %r (not iterable)" % (prop, value))
        except Exception as exc:
            out(pad + "%s RAISED %s" % (prop, type(exc).__name__))


def walk_expression(master, node, depth):
    pad = "  " * depth + "    "
    kind = node.get_class().get_name()
    extra = ""
    for prop in ("parameter_name", "texture"):
        try:
            value = node.get_editor_property(prop)
            if value is not None:
                extra += " %s=%s" % (prop, getattr(value, "get_name", lambda: value)())
        except Exception:
            pass
    out(pad + kind + extra)
    if depth > 6:
        return
    if kind == "MaterialExpressionMaterialFunctionCall":
        dump_function_call(master, node, depth)
        return
    mel = unreal.MaterialEditingLibrary
    try:
        names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
        inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
    except Exception:
        return
    for name, expr in zip(names, inputs):
        if expr is not None:
            out(pad + "  %s:" % name)
            walk_expression(master, expr, depth + 1)


def probe_plaster():
    mel = unreal.MaterialEditingLibrary
    for path in PLASTERS:
        out("=== %s ===" % path)
        instance = unreal.EditorAssetLibrary.load_asset(path)
        if instance is None:
            out("  MISSING")
            continue
        master = instance
        while isinstance(master, unreal.MaterialInstance):
            master = master.get_editor_property("parent")
        out("  master: %s  use_material_attributes: %s"
            % (master.get_name(),
               master.get_editor_property("use_material_attributes")))
        node = mel.get_material_property_input_node(
            master, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
        if node is None:
            out("  attributes pin: None")
            continue
        out("  attributes pin: %s" % node.get_class().get_name())
        if node.get_class().get_name() == "MaterialExpressionMaterialFunctionCall":
            dump_function_call(master, node)
        else:
            walk_expression(master, node, 0)
        out("")


def main():
    out("############ 1. Blueprint actors ############")
    probe_blueprints()
    out("")
    out("############ 2. plaster masters ############")
    probe_plaster()


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

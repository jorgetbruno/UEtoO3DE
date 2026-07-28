"""
probe_plaster_inputs.py — what feeds MM_Building's function calls, from OUTSIDE.

probe_showcase_gaps measured that a UMaterialFunction's internal expression
list is NOT reachable from Python (every candidate property raised). What IS
reachable is the function CALL's input pins in the outer graph -- the same
surface the M4 classifier already walks for scalar channels. This dumps, for
MI_Plaster's master (MM_Building):

  1. which branch of each static switch is LIVE for the instance
     (get_material_instance_static_switch_parameter_value);
  2. the input NAMES and connected expressions of the MF_BaseMaterial_Simple
     and MF_MaterialBlend calls -- if those pins are named like material
     attributes channels (BaseColor/Normal/ORM/...), the classifier can treat
     the call as a MakeMaterialAttributes lookalike.

Output: Tests/ue/results/probe_plaster_inputs.txt
"""

import os
import sys
import traceback

import unreal

sys.path.insert(0, "D:/Gamedev/UEtoO3DE/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python")
from ueo3de import material_export  # noqa: E402

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_plaster_inputs.txt"
INSTANCE_PATH = "/Game/EasternProvince/Materials/MI_Plaster"

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def dump_call(master, node, label, depth=0):
    pad = "  " * depth
    mel = unreal.MaterialEditingLibrary
    function = node.get_editor_property("material_function")
    out(pad + "CALL %s -> %s" % (label, function.get_name() if function else None))
    names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
    inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
    for index, name in enumerate(names):
        expr = inputs[index] if index < len(inputs) else None
        kind = expr.get_class().get_name() if expr else None
        extra = ""
        if expr is not None:
            for prop in ("parameter_name", "texture"):
                try:
                    value = expr.get_editor_property(prop)
                    if value is not None:
                        extra += " %s=%s" % (prop, getattr(value, "get_name", lambda: value)())
                except Exception:
                    pass
        out(pad + "  input[%d] %-28r -> %s%s" % (index, name, kind, extra))
        # One level into interesting feeders: another call, or math.
        if expr is not None and kind == "MaterialExpressionMaterialFunctionCall" and depth < 2:
            dump_call(master, expr, name, depth + 2)


def main():
    mel = unreal.MaterialEditingLibrary
    instance = unreal.EditorAssetLibrary.load_asset(INSTANCE_PATH)
    master = instance
    while isinstance(master, unreal.MaterialInstance):
        master = master.get_editor_property("parent")
    out("master: %s" % master.get_name())

    for switch in ("EnableBlendEffects?", "01_EnableGroundBlend?",
                   "02_EnableAOBlend?", "03_EnableGrungeBlend?"):
        try:
            value = mel.get_material_instance_static_switch_parameter_value(instance, switch)
            out("switch %-26r = %s" % (switch, value))
        except Exception as exc:
            out("switch %-26r RAISED %s" % (switch, type(exc).__name__))

    node = mel.get_material_property_input_node(
        master, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
    node, _ = material_export._follow(master, node, instance)
    out("attributes pin resolves (instance-aware) to: %s" % node.get_class().get_name())
    if node.get_class().get_name() == "MaterialExpressionMaterialFunctionCall":
        dump_call(master, node, "<attributes>")
    out("")

    # Dump BOTH functions regardless of which branch is live, so the
    # classifier handles either configuration.
    def find_calls(start, depth=0, seen=None):
        seen = seen if seen is not None else set()
        if start is None or start.get_name() in seen or depth > 12:
            return []
        seen.add(start.get_name())
        found = []
        if start.get_class().get_name() == "MaterialExpressionMaterialFunctionCall":
            found.append(start)
        try:
            inputs = list(mel.get_inputs_for_material_expression(master, start) or [])
        except Exception:
            inputs = []
        for expr in inputs:
            found.extend(find_calls(expr, depth + 1, seen))
        return found

    root = mel.get_material_property_input_node(
        master, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
    calls = find_calls(root)
    out("all function calls beneath the attributes pin: %d" % len(calls))
    seen_functions = set()
    for call in calls:
        function = call.get_editor_property("material_function")
        name = function.get_name() if function else "?"
        if name in seen_functions:
            continue
        seen_functions.add(name)
        dump_call(master, call, "(reachable)")
        out("")


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

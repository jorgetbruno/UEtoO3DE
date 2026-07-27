"""
probe_m4_tree.py — dump MM_Master's BaseColor expression subtree, depth 8.

Run in EasternProvince. Output: Tests/ue/results/probe_m4_tree.txt
"""

import os
import sys
import traceback

import unreal

sys.path.insert(0, "D:/Gamedev/UEtoO3DE/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python")
from ueo3de import material_export  # noqa: E402

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m4_tree.txt"
MASTER = "/Game/EasternProvince/Materials/MM_Master"

_lines = []


def out(msg=""):
    _lines.append(str(msg))


def dump(master, node, indent, depth):
    mel = unreal.MaterialEditingLibrary
    if node is None:
        out(indent + "-")
        return
    kind = node.get_class().get_name()
    extra = ""
    for prop in ("parameter_name", "material_function", "texture"):
        try:
            value = node.get_editor_property(prop)
            if value is not None:
                extra += " %s=%s" % (prop, getattr(value, "get_name", lambda: value)())
        except Exception:
            pass
    out(indent + kind + extra)
    if depth <= 0:
        out(indent + "  ...")
        return
    try:
        names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
        inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
    except Exception as exc:
        out(indent + "  <inputs raised %r>" % exc)
        return
    for index, part in enumerate(inputs):
        label = names[index] if index < len(names) else "[%d]" % index
        out(indent + "  " + label + ":")
        dump(master, part, indent + "    ", depth - 1)


def main():
    mel = unreal.MaterialEditingLibrary
    master = unreal.EditorAssetLibrary.load_asset(MASTER)
    node = mel.get_material_property_input_node(
        master, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
    node, _ = material_export._follow(master, node, None)
    names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
    inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
    for name, expr in zip(names, inputs):
        if name == "BaseColor":
            out("BaseColor subtree:")
            dump(master, expr, "  ", 8)


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

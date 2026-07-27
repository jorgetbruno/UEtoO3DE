"""
probe_m4_matattrs.py — how does MM_Master drive its outputs?

The S4.0 sweep marked MM_Master [OK] with zero driven properties -- vacuously
supported. Its instances classify to empty material_data. Hypothesis: the
master sets `use_material_attributes` and feeds everything through the single
MaterialAttributes pin (MakeMaterialAttributes), which
`get_material_property_input_node(<individual property>)` cannot see.

Dumps, for MM_Master and MM_MeshBasic:
  * use_material_attributes
  * the MP_MATERIAL_ATTRIBUTES input node and its class
  * that node's input names and connected expression classes, via
    get_material_expression_input_names + get_inputs_for_material_expression

Run in the EasternProvince project.
Output: Tests/ue/results/probe_m4_matattrs.txt
"""

import os
import traceback

import unreal

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m4_matattrs.txt"
TARGETS = ["/Game/EasternProvince/Materials/MM_Master",
           "/Game/EasternProvince/Materials/MM_MeshBasic",
           "/Game/EasternProvince/Materials/MM_Building"]

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_MATTR] " + str(msg))


def main():
    mel = unreal.MaterialEditingLibrary
    for path in TARGETS:
        material = unreal.EditorAssetLibrary.load_asset(path)
        if material is None:
            out(path + ": <not loadable>")
            continue
        out("=== " + path + " ===")
        try:
            out("  use_material_attributes = %r"
                % material.get_editor_property("use_material_attributes"))
        except Exception as exc:
            out("  use_material_attributes: %r" % exc)

        prop = getattr(unreal.MaterialProperty, "MP_MATERIAL_ATTRIBUTES", None)
        out("  MP_MATERIAL_ATTRIBUTES enum present: %s" % (prop is not None))
        if prop is None:
            continue
        node = mel.get_material_property_input_node(material, prop)
        out("  attributes input node: %r" % (node.get_class().get_name() if node else None))
        if node is None:
            continue

        try:
            names = list(mel.get_material_expression_input_names(node) or [])
        except Exception as exc:
            names = []
            out("  input names raised: %r" % exc)
        try:
            inputs = list(mel.get_inputs_for_material_expression(material, node) or [])
        except Exception as exc:
            inputs = []
            out("  inputs raised: %r" % exc)
        out("  %d input names, %d connected inputs" % (len(names), len(inputs)))
        for index in range(max(len(names), len(inputs))):
            name = names[index] if index < len(names) else "<?>"
            expr = inputs[index] if index < len(inputs) else None
            out("    %-18s <- %s" % (name, expr.get_class().get_name() if expr else "-"))
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

"""
probe_m4_funcall.py — what feeds MM_Master's base-colour function call?

The passthrough sees "0 texture inputs". Dump everything about the call:
function name, input names/types, connected expression kinds (raw and after
_follow), and for good measure the function's own input parameter defaults.

Run in EasternProvince. Output: Tests/ue/results/probe_m4_funcall.txt
"""

import os
import sys
import traceback

import unreal

sys.path.insert(0, "D:/Gamedev/UEtoO3DE/UE/UEtoO3DEFixture/Plugins/UEO3DEExporter/Content/Python")
from ueo3de import material_export  # noqa: E402

OUT_PATH = "D:/Gamedev/UEtoO3DE/Tests/ue/results/probe_m4_funcall.txt"
MASTER = "/Game/EasternProvince/Materials/MM_Master"
INSTANCE = "/Game/EasternProvince/Materials/MI_Plaster"

_lines = []


def out(msg=""):
    _lines.append(str(msg))
    unreal.log("[PROBE_FN] " + str(msg))


def main():
    mel = unreal.MaterialEditingLibrary
    master = unreal.EditorAssetLibrary.load_asset(MASTER)
    instance = unreal.EditorAssetLibrary.load_asset(INSTANCE)

    prop = unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES
    node = mel.get_material_property_input_node(master, prop)
    node, _ = material_export._follow(master, node, instance)
    out("attributes node: %s" % node.get_class().get_name())

    names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
    inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
    base_expr = None
    for name, expr in zip(names, inputs):
        if name == "BaseColor":
            base_expr = expr
            break
    out("BaseColor raw: %s" % (base_expr.get_class().get_name() if base_expr else None))
    followed, hint = material_export._follow(master, base_expr, instance)
    out("BaseColor followed: %s (hint %r)" % (followed.get_class().get_name(), hint))

    call = followed
    fn_asset = call.get_editor_property("material_function")
    out("function: %r" % (fn_asset.get_name() if fn_asset else None))

    for getter in ("get_inputs_for_material_function_expression",
                   "get_inputs_for_material_expression"):
        fn = getattr(mel, getter, None)
        if fn is None:
            out("%s: absent" % getter)
            continue
        try:
            parts = list(fn(master, call) or [])
        except Exception as exc:
            out("%s raised: %r" % (getter, exc))
            continue
        out("%s -> %d entries" % (getter, len(parts)))
        for index, part in enumerate(parts):
            if part is None:
                out("   [%d] None" % index)
                continue
            kind = part.get_class().get_name()
            f2, h2 = material_export._follow(master, part, instance)
            out("   [%d] %-44s -> followed %s (hint %r)"
                % (index, kind, f2.get_class().get_name() if f2 else None, h2))

    try:
        input_names = [str(n) for n in (mel.get_material_expression_input_names(call) or [])]
        out("call input names: %r" % input_names)
        input_types = list(mel.get_material_expression_input_types(call) or [])
        out("call input types: %r" % [str(t) for t in input_types])
    except Exception as exc:
        out("input names/types raised %r" % exc)

    # inside the function: its FunctionInputs and their defaults
    if fn_asset is not None:
        for attr in ("get_editor_property",):
            try:
                exprs = fn_asset.get_editor_property("function_expressions")
                out("function_expressions: %d" % len(exprs or []))
            except Exception as exc:
                out("function_expressions raised %r" % exc)


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

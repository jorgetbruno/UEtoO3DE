"""
rebuild_unsupported_material.py — keep M_Fixture_Unsupported unsupportable.

The fixture's deliberately-unsupported material was Lerp(texture, constant,
vertexcolor) -> BaseColor. M4's texture-DFS approximation now legitimately
recovers the texture beneath such Lerps (that is what makes real foliage
convert), so the old construction converts too -- the canary went blind, the
same failure shape as the Y-symmetric mirror canary in M1.

Rewire BaseColor to Lerp(VertexColor, Constant3Vector, Constant): rich enough
to exercise the classifier, with NO texture anywhere beneath, so no present or
future approximation can rescue it. The actor reference and asset identity are
untouched -- only the property connection changes (the old expressions stay in
the graph, disconnected, which the classifier never sees).

Run:  run_ue_python.bat rebuild_unsupported_material.py
"""

import traceback

import unreal

PATH = "/Game/Materials/M_Fixture_Unsupported"


def main():
    mel = unreal.MaterialEditingLibrary
    material = unreal.EditorAssetLibrary.load_asset(PATH)
    if material is None:
        raise RuntimeError("missing " + PATH)

    vertex_color = mel.create_material_expression(
        material, unreal.MaterialExpressionVertexColor, -900, -300)
    constant_color = mel.create_material_expression(
        material, unreal.MaterialExpressionConstant3Vector, -900, -100)
    constant_color.set_editor_property("constant", unreal.LinearColor(0.0, 0.5, 1.0, 1.0))
    alpha = mel.create_material_expression(
        material, unreal.MaterialExpressionConstant, -900, 100)
    alpha.set_editor_property("r", 0.5)
    lerp = mel.create_material_expression(
        material, unreal.MaterialExpressionLinearInterpolate, -500, -100)
    mel.connect_material_expressions(vertex_color, "", lerp, "A")
    mel.connect_material_expressions(constant_color, "", lerp, "B")
    mel.connect_material_expressions(alpha, "", lerp, "Alpha")
    mel.connect_material_property(lerp, "", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.recompile_material(material)

    if not unreal.EditorAssetLibrary.save_asset(PATH):
        raise RuntimeError("save failed")

    node = mel.get_material_property_input_node(material, unreal.MaterialProperty.MP_BASE_COLOR)
    unreal.log("[REBUILD_UNSUP] BaseColor now driven by: " + node.get_class().get_name())


try:
    main()
except Exception:
    unreal.log_error("[REBUILD_UNSUP] " + traceback.format_exc())
    print("RESULT: FAIL")
    raise
print("RESULT: PASS")

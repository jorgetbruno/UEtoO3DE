"""
test_material_blends.py — layered master materials resolve to their own surface.

Pure: no editor. `unreal` is stubbed with a fake material graph that answers
the same MaterialEditingLibrary calls the converter makes.
Run: python Tests/perf/test_material_blends.py

WHY THIS EXISTS. NYC1950's master materials blend a second material layer over
the surface and switch it OFF on the Lerp *alpha* (`Blend Material ON`, false
in every instance). The converter followed switches but not blends, so its
nearest-texture search reached the disabled layer's placeholders: every road,
pavement and brick surface of a 51,776-entity city imported `T_White`, and car
glass shipped its dirt mask as colour and opacity -- all without an error,
because a placeholder is a texture. Measured over the pack's 798 materials:
117 channels moved from a placeholder to the authored texture, 1 the other way.

Each case below is a shape traced in that pack.
"""

import os
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- a fake material graph behind the MaterialEditingLibrary calls --------------


class FakeClass(object):
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class Node(object):
    """An expression: kind, editor properties, and named input pins."""

    def __init__(self, kind, pins=(), **props):
        self.kind = kind
        self.pins = list(pins)            # [(name, Node or None)]
        self.props = props

    def get_class(self):
        return FakeClass(self.kind)

    def get_editor_property(self, name):
        if name not in self.props:
            raise Exception("no property %s on %s" % (name, self.kind))
        return self.props[name]


class LinearColor(object):
    def __init__(self, r, g, b):
        self.r, self.g, self.b = r, g, b


class MaterialInstanceConstant(object):
    def __init__(self, switches=None, scalars=None, textures=None, vectors=None):
        self.switches = switches or {}
        self.scalars = scalars or {}
        self.textures = textures or {}
        self.vectors = vectors or {}


class MaterialInstanceDynamic(object):
    pass


class MEL(object):
    @staticmethod
    def get_material_expression_input_names(node):
        return [name for name, _ in node.pins]

    @staticmethod
    def get_inputs_for_material_expression(master, node):
        return [expr for _, expr in node.pins]

    @staticmethod
    def get_material_instance_static_switch_parameter_value(instance, name):
        return instance.switches[name]

    @staticmethod
    def get_material_instance_scalar_parameter_value(instance, name):
        return instance.scalars[name]

    @staticmethod
    def get_material_instance_texture_parameter_value(instance, name):
        return instance.textures.get(name)

    @staticmethod
    def get_material_instance_vector_parameter_value(instance, name):
        return instance.vectors[name]


stub = types.ModuleType("unreal")
stub.MaterialEditingLibrary = MEL
stub.MaterialInstanceConstant = MaterialInstanceConstant
stub.MaterialInstanceDynamic = MaterialInstanceDynamic
stub.MaterialInstance = MaterialInstanceConstant
sys.modules["unreal"] = stub

sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import material_export as me  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def tex(param, texture):
    return Node("MaterialExpressionTextureSampleParameter2D",
                parameter_name=param, texture=texture)


def switch(param, true=None, false=None):
    return Node("MaterialExpressionStaticSwitchParameter",
                [("True", true), ("False", false)],
                parameter_name=param, default_value=False)


def lerp(a=None, b=None, alpha=None, const_a=0.0, const_b=1.0, const_alpha=0.5):
    return Node("MaterialExpressionLinearInterpolate",
                [("A", a), ("B", b), ("Alpha", alpha)],
                const_a=const_a, const_b=const_b, const_alpha=const_alpha)


def scalar(param, default=0.0):
    return Node("MaterialExpressionScalarParameter",
                parameter_name=param, default_value=default)


def constant(value):
    return Node("MaterialExpressionConstant", r=value)


def picked(node, instance, role="basecolor"):
    found, _hint = me._find_texture(None, node, instance, role_suffix=role)
    return None if found is None else found.props["texture"]


# --- 1. the NYC pavement: a blend layer switched off on the ALPHA --------------
# Lerp(A = surface, B = second layer's placeholder, Alpha = switch).
# The placeholder is SHALLOWER than the surface texture, which is exactly why
# the old breadth-first search returned it.
instance = MaterialInstanceConstant(switches={"Blend Material ON": False})
surface = Node("MaterialExpressionMultiply",
               [("A", tex("BaseColor Texture", "T_Sidewalk_B")), ("B", constant(1.0))])
pavement = lerp(a=surface,
                b=tex("MB_Base Color", "T_White"),
                alpha=switch("Blend Material ON", true=scalar("Blend Mask", 1.0),
                             false=constant(0.0)))
check(picked(pavement, instance) == "T_Sidewalk_B",
      "a blend switched off on its alpha must resolve to the surface, got %r"
      % picked(pavement, instance))

on = MaterialInstanceConstant(switches={"Blend Material ON": True})
full = lerp(a=surface, b=tex("MB_Base Color", "T_Blend_B"),
            alpha=switch("Blend Material ON", true=constant(1.0), false=constant(0.0)))
check(picked(full, on) == "T_Blend_B",
      "a blend switched fully ON must resolve to the blended layer, got %r"
      % picked(full, on))

# the alpha folds through an instance scalar, too (NYC asphalt: AO overlay = 1.0)
by_scalar = MaterialInstanceConstant(scalars={"AO BaseColor Intesity": 1.0})
ao_on = lerp(a=tex("BaseColor Texture", "T_Before_AO"),
             b=tex("BaseColor Texture", "T_After_AO"),
             alpha=scalar("AO BaseColor Intesity"))
check(picked(ao_on, by_scalar) == "T_After_AO",
      "an alpha that folds to 1 through an instance scalar must take B")

# --- 2. a blend that really blends is searched surface-first -------------------
# Alpha is a dirt mask: nothing decides it. The surface (A) wins even though
# the overlay (B) texture is shallower, and the alpha mask itself is never a
# colour candidate.
dirt = MaterialInstanceConstant()
deep_surface = Node("MaterialExpressionDesaturation",
                    [("None", Node("MaterialExpressionMultiply",
                                   [("A", tex("BaseColor Texture", "T_Brick_B")),
                                    ("B", constant(0.9))]))])
overlay = lerp(a=deep_surface, b=tex("Dirt Color", "T_DirtColor"),
               alpha=tex("Main Variation", "T_DirtMask"))
check(picked(overlay, dirt) == "T_Brick_B",
      "an undecided blend must search its surface before its overlay, got %r"
      % picked(overlay, dirt))
mask_only = lerp(a=None, b=None, alpha=tex("Main Variation", "T_DirtMask"))
check(picked(mask_only, dirt) is None,
      "a blend's ALPHA is a mask, never a colour source")

# --- 3. a texture named for another channel only wins by default ---------------
# NYC asphalt multiplies its surface by an AO term whose AO_Map sits shallower
# than the surface texture.
ao_times_surface = Node("MaterialExpressionMultiply",
                        [("A", tex("AO_Map", "T_White_linear")),
                         ("B", Node("MaterialExpressionDesaturation",
                                    [("None", tex("Base_Map", "T_Aspahlt_BaseColor"))]))])
check(picked(ao_times_surface, dirt) == "T_Aspahlt_BaseColor",
      "a colour search must pass over a map the master names as AO")
check(picked(tex("AO_Map", "T_OnlyAO"), dirt) == "T_OnlyAO",
      "with nothing else reachable, the foreign-role texture is still used")
check(picked(ao_times_surface, dirt, role="roughness") == "T_White_linear",
      "roughness has no foreign-role list: breadth-first order is unchanged")

# --- 4. a decided pin with nothing connected is a constant, not the other side -
glass_instance = MaterialInstanceConstant(switches={"Use Opacity from Texture": False})
off_to_constant = lerp(a=None, b=tex("Opacity Texture", "T_Glass_DirtMask"),
                       alpha=constant(0.0), const_a=0.1)
check(me._decided_lerp_constant(None, off_to_constant, glass_instance) == 0.1,
      "a lerp decided onto an unconnected A must compile to ConstA")
check(picked(off_to_constant, glass_instance) is None,
      "a lerp decided onto a constant must not search the side it turned off")

unwired = switch("Use Opacity from Texture", true=tex("Opacity Texture", "T_Mask"),
                 false=None)
followed, _hint = me._follow(None, unwired, glass_instance)
check(followed is None,
      "a static switch decided onto an UNCONNECTED pin must stop -- the old "
      "fallback handed back the branch the switch turned off, got %r"
      % (followed and followed.props))

# --- 5. no blends, no names: the old search, exactly ----------------------------
plain = Node("MaterialExpressionMultiply",
             [("A", Node("MaterialExpressionDesaturation",
                         [("None", tex("Texture", "T_Deep"))])),
              ("B", tex("Other", "T_Shallow"))])
check(picked(plain, dirt) == "T_Shallow",
      "a graph with no blends and no role names keeps breadth-first order")

# --- 6. a channel with no texture is constant math, and is evaluated -----------
# NYC1950's MI_NYCB7_Metal2 (249 building parts) and its water had NO texture
# on base colour, so the texture-following converter dropped both materials
# and the entities rendered white. Evaluated, they are colours.
def vector(param):
    return Node("MaterialExpressionVectorParameter", parameter_name=param,
                default_value=LinearColor(0.0, 0.0, 0.0))


def close(a, b, eps=1e-6):
    a = a if isinstance(a, list) else [a]
    b = b if isinstance(b, list) else [b]
    return len(a) == len(b) and all(abs(x - y) <= eps for x, y in zip(a, b))


metal = MaterialInstanceConstant(vectors={"Base Color": LinearColor(0.2, 0.1, 0.0)},
                                 scalars={"Saturation Multiplier": 0.5})
desaturated = Node("MaterialExpressionDesaturation",
                   [("None", vector("Base Color")), ("Fraction", scalar("Saturation Multiplier"))])
grey = 0.2 * 0.3 + 0.1 * 0.59          # UE's luminance factors
expected = [c + (grey - c) * 0.5 for c in (0.2, 0.1, 0.0)]
folded = me._fold_constant(None, desaturated, metal)
check(close(folded, expected),
      "Desaturation(Base Color, Saturation Multiplier) must fold to the desaturated "
      "colour %r, got %r" % (expected, folded))

water = MaterialInstanceConstant(vectors={"WaterColor1": LinearColor(0.5, 0.25, 1.0),
                                          "WaterColor2": LinearColor(0.0, 0.0, 0.0)})
fresnel = Node("MaterialExpressionFresnel", [("ExponentIn", None)])
water_graph = Node("MaterialExpressionPower",
                   [("Base", lerp(a=vector("WaterColor1"), b=vector("WaterColor2"), alpha=fresnel)),
                    ("Exponent", None)], const_exponent=2.0)
check(close(me._fold_constant(None, water_graph, water), [0.25, 0.0625, 1.0]),
      "Power(Lerp(WaterColor1, WaterColor2, Fresnel), 2): a view-dependent alpha takes "
      "the surface colour, then the power applies; got %r"
      % (me._fold_constant(None, water_graph, water),))

tinted = Node("MaterialExpressionMultiply", [("A", vector("WaterColor1")), ("B", constant(0.5))])
check(close(me._fold_constant(None, tinted, water), [0.25, 0.125, 0.5]),
      "colour x constant must fold to the product")
exact = lerp(a=constant(0.2), b=constant(0.6), alpha=constant(0.25))
check(close(me._fold_constant(None, exact, water), 0.3),
      "a lerp with a constant alpha is evaluated exactly, not taken as its surface")

check(me._fold_constant(None, Node("MaterialExpressionMultiply",
                                   [("A", tex("BaseColor Texture", "T_B")), ("B", constant(2.0))]),
                        water) is None,
      "a graph with a texture is not constant math: folding must refuse it")
check(me._fold_constant(None, fresnel, water) is None,
      "a view-dependent node on its own is not a constant")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

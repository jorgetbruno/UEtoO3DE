"""
test_landscape_layer.py — a Landscape's layer blend exports as one of its layers.

Pure: no editor. Run: python Tests/perf/test_landscape_layer.py

WHY THIS EXISTS. NYC1950's MI_SummerLandscape blends painted layers (Dirt,
Stone, Snow, Grass, Pavement, plus an auto layer) through material functions
Python cannot walk, so the Landscape exported with no material and the city's
ground rendered white. The layers' textures ARE reachable as named parameters
("Dirt_BC", "Dirt_N", "Dirt_HRAO"), so one layer stands in for the ground.
The names below are that material's real parameter list.
"""

import os
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("unreal", types.ModuleType("unreal"))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueo3de import param_roles as pr  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


NYC = ["Dirt_BC", "Dirt_Mask", "Dirt_N", "Dirt_HRAO", "Grass_BC", "Grass_Mask", "Grass_N",
       "Grass_HRAO", "Stone_BC", "Stone_Mask", "Stone_N", "Stone_HRAO", "Snow_BC", "Snow_Mask",
       "Snow_N", "HRAO", "Normal", "BC"]
ORDER = ["Auto", "Dirt", "Stone", "Snow", "Grass", "Pavement"]

# --- the default: the blend's first layer with its own textures ---------------
layer, roles = pr.pick_landscape_layer(ORDER, NYC)
check(layer == "Dirt",
      "the auto layer has no textures of its own, so the default is the next layer, Dirt; "
      "got %r (the user confirmed the NYC ground reads as dark soil)" % layer)
check(roles == {"basecolor": "Dirt_BC", "normal": "Dirt_N", "packed": "Dirt_HRAO"},
      "Dirt's roles must be its BC, N and HRAO; got %r" % (roles,))
check("Dirt_Mask" not in roles.values(), "a layer's blend mask is not a surface texture")

# --- the knob --------------------------------------------------------------------
check(pr.pick_landscape_layer(ORDER, NYC, " grass ")[0] == "Grass",
      "UEO3DE_LANDSCAPE_LAYER picks a layer case- and space-insensitively")
for bad in ("Pavement", "Lava"):
    try:
        pr.pick_landscape_layer(ORDER, NYC, bad)
        check(False, "a requested layer without its own textures (%r) must raise" % bad)
    except ValueError as error:
        check("Dirt" in str(error) and "Grass" in str(error),
              "the refusal must list the layers that can be chosen: %s" % error)
check(pr.pick_landscape_layer(["Auto"], NYC) == (None, {}),
      "a blend with no textured layer yields nothing rather than a guess")

# --- the prefix is a whole word, not a substring --------------------------------
check(pr.pick_layer_roles(["Grassland_BC", "Grass_BC"], "Grass") == {"basecolor": "Grass_BC"},
      "'Grassland_BC' must not be read as the Grass layer's base colour")
check(pr.pick_layer_roles(["Snow_Mask", "Snow_H"], "Snow") == {},
      "masks and height maps carry no StandardPBR role")

# --- HRAO: measured channel order ------------------------------------------------
order, token = pr.packed_channel_order("Dirt_HRAO")
check(token == "hrao" and order == {"R": "height", "G": "roughness", "B": "ao"},
      "HRAO is R=height, G=roughness, B=AO (measured on NYC's textures); got %r %r"
      % (order, token))
check(pr.packed_channel_order("T_Wall_ORM") == ({"R": "ao", "G": "roughness", "B": "metallic"}, "orm"),
      "adding HRAO must not change ORM")
check(pr.pick_parameter_roles(["Dirt_BC"]) == {},
      "the ordinary by-name classifier still refuses 'dirt' as a secondary layer")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

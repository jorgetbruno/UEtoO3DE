"""
param_roles.py — map material PARAMETER NAMES to texture roles. PURE.

The fallback of last resort for `use_material_attributes` masters whose
attributes pin ends in a material-function call with no classifiable input
pins (measured on EasternProvince's MM_Building: `MF_BaseMaterial_Simple`
takes no outer-graph inputs at all -- its textures are parameter nodes INSIDE
the function, and UMaterialFunction internals are not reachable from Python;
every candidate property raised, `Tests/ue/results/probe_showcase_gaps.txt`).

What IS reachable is the flat parameter list the master exposes -- parameter
nodes inside functions surface there too. So the classifier's last resort is
to pick, per role, the best-named texture parameter and resolve its value from
the instance. This is a heuristic over ARTIST-CHOSEN names and says so in the
report (`MAT_PARAMS_BY_NAME`); the rules below are deliberately conservative:

  * a name only qualifies if it carries a role token ("basecolor", "normal",
    "orm", ...); short, collision-prone tokens (orm/arm/ao) must sit on a
    word boundary so "armor" and "chaos" never qualify;
  * names that look like secondary layers (blend, grunge, ground, detail,
    macro, dirt, noise) never qualify -- wrongly picking the mud-blend albedo
    for the wall is worse than staying grey;
  * among qualifiers the SHORTEST name wins: base textures are named plainly,
    variants accrete prefixes.
"""

import re

# role -> tokens that positively identify it. Order matters: earlier roles
# claim their parameter first, so an "ORM" parameter is taken by the packed
# role and never falls through to "roughness" by substring accident.
# `word` tokens must sit on a word boundary; `loose` tokens may appear
# anywhere in the normalized name.
ROLE_TOKENS = (
    ("orm", {"word": ("orm", "mra", "arm", "rma"), "loose": ()}),
    ("normal", {"word": (), "loose": ("normal",)}),
    ("basecolor", {"word": (), "loose": ("basecolor", "albedo", "diffuse", "color")}),
    ("roughness", {"word": (), "loose": ("roughness",)}),
    ("metallic", {"word": (), "loose": ("metallic", "metalness")}),
    ("ao", {"word": ("ao",), "loose": ("ambientocclusion", "occlusion")}),
)

# A name containing any of these is a secondary layer, never the base surface.
EXCLUDE_TOKENS = ("blend", "grunge", "ground", "detail", "macro", "dirt",
                  "noise", "mask", "overlay", "puddle", "moss", "snow")

# THE PACKING TOKEN NAMES THE CHANNEL ORDER, AND THE FOUR ARE NOT THE SAME.
# They were all treated as ORM, which is correct for exactly half of them:
#
#     ORM  Occlusion, Roughness, Metallic   R=ao        G=roughness  B=metallic
#     ARM  AO,        Roughness, Metallic   R=ao        G=roughness  B=metallic
#     RMA  Roughness, Metallic,  AO         R=roughness G=metallic   B=ao
#     MRA  Metallic,  Roughness, AO         R=metallic  G=roughness  B=ao
#
# Measured on Docks/VOL4_Albert: 53 textures named `*_RMA`, every one split as
# if it were ORM -- so roughness received metallic data, metallic received AO,
# and AO received roughness. ALL THREE CHANNELS WRONG on every PBR surface in
# the level, which is what "the materials are all weird" looked like.
#
# This only bites materials classified BY NAME (`MAT_PARAMS_BY_NAME`), where
# there is no ComponentMask in the graph to read the truth from -- 59 of 62
# materials on that level. When the graph IS walkable the channel comes from
# the mask and none of this applies.
PACKED_CHANNEL_ORDER = {
    "orm": {"R": "ao", "G": "roughness", "B": "metallic"},
    "arm": {"R": "ao", "G": "roughness", "B": "metallic"},
    "rma": {"R": "roughness", "G": "metallic", "B": "ao"},
    "mra": {"R": "metallic", "G": "roughness", "B": "ao"},
    # Height, Roughness, AO. Measured on NYC1950's landscape layers
    # (T_DarkSoil/T_Grass2/T_GravelPath_HRAO): R is a constant 0.5 on the flat
    # soil and grass and varies only on gravel (height), G is 0.38 on wet soil
    # and 0.74 on grass (roughness), B averages 0.83-0.91 with dark crevices
    # and a 1.0 maximum (AO). Height has no StandardPBR slot and is skipped.
    "hrao": {"R": "height", "G": "roughness", "B": "ao"},
}
# What a name with no recognisable token falls back to. ORM is the most common
# convention and the previous behaviour, so this is the conservative default --
# but the caller reports it, because a guess that is silent is how the RMA
# levels shipped wrong.
DEFAULT_PACKING = "orm"


def packing_token(name):
    """Which packed convention this parameter name declares, or None.

    Word-boundary matched, like the role token itself: "armor" must not read
    as ARM. Longest token first is unnecessary here (all four are 3 letters)
    but the word split already prevents substring accidents.
    """
    words = _words(name)
    for token in PACKED_CHANNEL_ORDER:
        if token in words:
            return token
    return None


def packed_channel_order(name):
    """`{channel: role}` for a packed texture parameter, and whether it was known.

    Returns `(order, token)`; `token` is None when the name declared nothing
    and the ORM default was assumed.
    """
    token = packing_token(name)
    if token is None:
        return dict(PACKED_CHANNEL_ORDER[DEFAULT_PACKING]), None
    return dict(PACKED_CHANNEL_ORDER[token]), token


def _normalize(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _words(name):
    """The name split on case/underscore/digit boundaries, lowercased."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))
    return [w.lower() for w in re.split(r"[^A-Za-z]+", spaced) if w]


def _qualifies(name, tokens):
    normalized = _normalize(name)
    if any(token in normalized for token in EXCLUDE_TOKENS):
        return False
    if any(token in normalized for token in tokens["loose"]):
        return True
    words = _words(name)
    return any(token in words for token in tokens["word"])


# Suffix words a landscape layer's texture parameters use, after the layer name
# ("Dirt_BC", "Grass_N", "Stone_HRAO"). Packed tokens come from
# PACKED_CHANNEL_ORDER.
LAYER_ROLE_WORDS = {
    "basecolor": ("bc", "basecolor", "albedo", "diffuse", "d"),
    "normal": ("n", "normal", "nrm"),
    "roughness": ("r", "rough", "roughness"),
    "ao": ("ao", "occlusion"),
}


def pick_layer_roles(names, layer):
    """{role: parameter name} for one landscape layer's textures.

    A parameter belongs to `layer` when its first word IS the layer's name, so
    "Dirt_BC" is the Dirt layer's base colour and "Dirt_Mask" is nothing. This
    is the opposite of pick_parameter_roles, which refuses "dirt" outright as a
    secondary layer: on a Landscape the layers ARE the surfaces.
    """
    wanted = _words(layer)
    chosen = {}
    for name in names:
        words = _words(name)
        if len(words) <= len(wanted) or words[:len(wanted)] != wanted:
            continue
        rest = words[len(wanted):]
        joined = "".join(rest)
        role = None
        if any(token in rest for token in PACKED_CHANNEL_ORDER) or joined in PACKED_CHANNEL_ORDER:
            role = "packed"
        else:
            for candidate, tokens in LAYER_ROLE_WORDS.items():
                if joined in tokens or (len(rest) == 1 and rest[0] in tokens):
                    role = candidate
                    break
        if role is not None and role not in chosen:
            chosen[role] = name
    return chosen


def pick_landscape_layer(layer_order, names, requested=None):
    """(layer, roles) for the layer a Landscape material is exported as.

    A painted layer blend cannot be one StandardPBR material, so one layer
    stands in for the ground. `requested` (UEO3DE_LANDSCAPE_LAYER) picks it;
    otherwise the FIRST layer in the blend that has a base colour texture of
    its own. An auto layer (NYC1950's "Auto", blended from others by slope)
    has none and is passed over. Raises ValueError for a requested layer that
    does not exist or has no textures -- a typo must not silently fall back.
    """
    usable = []
    for layer in layer_order:
        roles = pick_layer_roles(names, layer)
        if "basecolor" in roles:
            usable.append((layer, roles))
    if requested:
        for layer, roles in usable:
            if layer.lower() == str(requested).strip().lower():
                return layer, roles
        raise ValueError(
            "UEO3DE_LANDSCAPE_LAYER=%r is not a layer with textures; choose one of %s"
            % (requested, ", ".join(layer for layer, _roles in usable) or "none"))
    if not usable:
        return None, {}
    return usable[0]


def pick_parameter_roles(names):
    """{role: parameter name} for the best candidate per role.

    `names` is every texture parameter name the master exposes. A parameter
    is claimed by at most one role (first role in ROLE_TOKENS order wins).
    """
    chosen = {}
    claimed = set()
    for role, tokens in ROLE_TOKENS:
        candidates = [name for name in names
                      if name not in claimed and _qualifies(name, tokens)]
        if candidates:
            best = min(candidates, key=lambda n: (len(_normalize(n)), str(n)))
            chosen[role] = best
            claimed.add(best)
    return chosen

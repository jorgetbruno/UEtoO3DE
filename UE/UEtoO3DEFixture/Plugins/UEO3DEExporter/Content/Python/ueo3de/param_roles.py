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

"""
knob_effects.py — say so when an export knob could not act on this level.

Pure: no `unreal`. Evaluated by Tests/ue/export_level.py once every mesh is baked.

WHY THIS EXISTS. UEO3DE_LOD0_RATIO and UEO3DE_LOD_RATIOS budget Nanite
sources only. NYC1950 has no Nanite meshes -- every mesh carries authored LODs
-- so halving both ratios changed exactly nothing, and a three-hour export
wrote a byte-identical copy of the previous one with no hint anywhere. A knob
that parses and then has nothing to act on is indistinguishable from a knob
that worked, unless the export counts what it actually baked and compares.

`stats` counts the static meshes whose LOD chain was built:
    nanite    Nanite-enabled source meshes
    authored  non-Nanite meshes with more than one authored LOD
    single    non-Nanite meshes with one LOD
"""

import os


def _set(environ, name):
    return str(environ.get(name, "")).strip() != ""


def _float(environ, name, default):
    try:
        return float(str(environ.get(name, "")).strip())
    except ValueError:
        return default


def no_effect_warnings(stats, environ=None):
    """[(knob, message)] for every set knob this export gave nothing to act on."""
    environ = os.environ if environ is None else environ
    nanite = int(stats.get("nanite", 0))
    authored = int(stats.get("authored", 0))
    single = int(stats.get("single", 0))
    total = nanite + authored + single
    if total == 0:
        return []                      # nothing was baked (a reuse export): no evidence
    chain_off = str(environ.get("UEO3DE_LOD_CHAIN", "")).strip().lower() in ("0", "off", "false", "no")
    out = []

    for knob in ("UEO3DE_LOD0_RATIO", "UEO3DE_LOD_RATIOS"):
        if _set(environ, knob) and nanite == 0:
            out.append((knob, "had no effect: none of the %d meshes is Nanite, and this ratio "
                              "only budgets a Nanite source (%d carry authored LODs, %d have one "
                              "LOD). UEO3DE_LOD_REDUCE scales authored meshes." % (total, authored, single)))
    if _set(environ, "UEO3DE_LOD_RATIOS") and chain_off:
        out.append(("UEO3DE_LOD_RATIOS", "had no effect: UEO3DE_LOD_CHAIN=0 exports LOD 0 only, "
                                         "so there are no far LODs to budget."))
    if _set(environ, "UEO3DE_LOD_REDUCE") and _float(environ, "UEO3DE_LOD_REDUCE", 1.0) < 1.0 \
            and authored + single == 0:
        out.append(("UEO3DE_LOD_REDUCE", "had no effect: all %d meshes are Nanite, and this factor "
                                         "only scales authored LODs. UEO3DE_LOD0_RATIO budgets "
                                         "Nanite meshes." % total))
    if str(environ.get("UEO3DE_NANITE_FALLBACK", "")).strip().lower() in ("1", "on", "true", "yes") \
            and nanite == 0:
        out.append(("UEO3DE_NANITE_FALLBACK", "had no effect: none of the %d meshes is Nanite." % total))
    if _set(environ, "UEO3DE_DEFER_BUILD") and (chain_off or nanite + authored == 0):
        out.append(("UEO3DE_DEFER_BUILD", "had no effect: it defers rebuilds between LOD writes, and "
                                          "this export wrote no LOD chains."))
    return out

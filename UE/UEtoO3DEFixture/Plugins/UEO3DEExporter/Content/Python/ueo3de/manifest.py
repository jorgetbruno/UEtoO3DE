"""
manifest.py — assembly and serialization of `manifest.json`.

PURE. The manifest is the single source of truth for the whole pipeline
(plan constraint 7), so this module owns three things and nothing else:
the schema version, the float rounding policy, and a deterministic byte
layout.

Determinism matters more than it looks: M1's acceptance test diffs the export
against a committed golden file. Anything non-deterministic -- dict ordering,
float noise in the 15th digit, -0.0, a chronological warning order -- would
show up as a spurious diff and train everyone to regenerate the golden
instead of reading it.

  * every float is rounded to 6 decimals (a nanometre at meter scale, far
    below the test's 1e-4 m tolerance)
  * keys are sorted on output
  * entities and assets are sorted by a stable key by the caller
  * `warnings[]` is sorted by (code, subject, detail)
"""

import json

SCHEMA_VERSION = 1
TOOL_NAME = "UEO3DEExporter"
TOOL_VERSION = "0.1.0"

# The Lane A basis map actually applied, recorded so the O3DE importer can
# refuse a manifest produced under a different convention rather than
# silently importing a mirrored level. See lane_a.py.
LANE_A_RULE = "negate_y"

FLOAT_DIGITS = 6


def round_floats(value, digits=FLOAT_DIGITS):
    """Recursively round every float, normalizing -0.0 to 0.0."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        rounded = round(value, digits)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, dict):
        return {k: round_floats(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_floats(v, digits) for v in value]
    return value


def build(level, assets, entities, warning_records, engine_version):
    """Assemble the manifest document.

    `level`    : {"package": "/Game/Maps/Fixture_01", "name": "Fixture_01"}
    `assets`   : list of asset dicts (sorted by the caller)
    `entities` : list of entity dicts (sorted by the caller)
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "tool": TOOL_NAME,
            "tool_version": TOOL_VERSION,
            "engine": "UnrealEngine",
            "engine_version": engine_version,
        },
        "level": {
            "ue_package": level["package"],
            "name": level["name"],
        },
        "units": {
            "length": "meters",
            "angle": "degrees",
            "coordinate_system": "o3de_right_handed_z_up",
            "lane_a_rule": LANE_A_RULE,
        },
        "assets": assets,
        "entities": entities,
        "warnings": warning_records,
    }
    return round_floats(document)


def dumps(document):
    """Serialize with sorted keys and a trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"

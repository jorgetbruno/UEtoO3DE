"""
manifest_io.py — read and vet a UEtoO3DE manifest (plan M2).

PURE. No `azlmbr`, so staging and every test around it run in a plain
interpreter.

The manifest is the single source of truth (plan constraint 7), and the most
valuable thing this module does is *refuse* one it does not understand. Two
checks matter more than the rest:

  * `units.lane_a_rule` and `units.lane_b_rule`. UE is left-handed, O3DE is
    right-handed, and the conversion between them has to be applied to both
    transforms and geometry with the same sign. A manifest exported under a
    different convention -- or by an exporter predating the Lane B fix --
    imports into a level that is silently mirrored: geometrically valid,
    fully functional, and backwards. There is no assertion downstream that
    catches it, so it is caught here.
  * `schema_version`. Importing a newer document with an older importer means
    quietly ignoring fields, which is the same failure with more steps.
"""

import json

SUPPORTED_SCHEMA_VERSION = 5
EXPECTED_LANE_A_RULE = "negate_y"
EXPECTED_LANE_B_RULE = "negate_y_scene_rz180"
EXPECTED_COORDINATE_SYSTEM = "o3de_right_handed_z_up"
EXPECTED_LENGTH_UNIT = "meters"


class ManifestError(Exception):
    """The manifest cannot be imported safely."""


def load(path):
    with open(path, "r") as handle:
        document = json.load(handle)
    verify(document, path)
    return document


def verify(document, path="<manifest>"):
    version = document.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ManifestError(
            "%s: schema_version is %r, this importer supports %r"
            % (path, version, SUPPORTED_SCHEMA_VERSION))

    units = document.get("units") or {}
    for key, expected in (("lane_a_rule", EXPECTED_LANE_A_RULE),
                          ("lane_b_rule", EXPECTED_LANE_B_RULE),
                          ("coordinate_system", EXPECTED_COORDINATE_SYSTEM),
                          ("length", EXPECTED_LENGTH_UNIT)):
        actual = units.get(key)
        if actual != expected:
            raise ManifestError(
                "%s: units.%s is %r, expected %r. Importing anyway would place "
                "correct transforms around mirrored geometry, or rescale a "
                "document that is already in O3DE units."
                % (path, key, actual, expected))

    for required in ("assets", "entities", "warnings", "level"):
        if required not in document:
            raise ManifestError("%s: missing required top-level key %r" % (path, required))

    errors = [w for w in document["warnings"] if w.get("severity") == "error"]
    if errors:
        raise ManifestError(
            "%s: the export reported %d error-severity warning(s): %s"
            % (path, len(errors), ", ".join(sorted({w["code"] for w in errors}))))

    return document


def assets_by_guid(document):
    return {asset["guid"]: asset for asset in document["assets"]}


def static_mesh_assets(document):
    return [asset for asset in document["assets"] if asset["kind"] == "static_mesh"]


def entities_parents_first(document):
    """Entities ordered so a parent always precedes its children.

    O3DE needs the parent's EntityId when creating a child, so the walk order
    is not cosmetic. A cycle (which a well-formed export cannot produce, but a
    hand-edited manifest can) is reported rather than silently truncating the
    level.
    """
    remaining = {entity["id"]: entity for entity in document["entities"]}
    ordered = []
    placed = set()

    while remaining:
        ready = [entity for entity in remaining.values()
                 if entity["parent_id"] is None or entity["parent_id"] in placed]
        if not ready:
            raise ManifestError(
                "parent_id cycle or dangling parent among %d entities: %s"
                % (len(remaining), sorted(e["name"] for e in remaining.values())))
        # Sort for determinism; the manifest is already sorted by actor path.
        ready.sort(key=lambda e: e["ue_actor_path"])
        for entity in ready:
            ordered.append(entity)
            placed.add(entity["id"])
            del remaining[entity["id"]]

    return ordered

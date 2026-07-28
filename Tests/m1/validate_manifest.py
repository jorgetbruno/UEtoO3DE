"""
validate_manifest.py — schema + integrity validator for `manifest.json` (plan M1).

Runs in a plain Python 3 interpreter. No third-party packages: `jsonschema` is
not installed with UE's or O3DE's Python and adding a pip dependency to a
pipeline whose whole point is running headless in CI is a bad trade. The
draft-07 subset implemented below is exactly the subset the manifest schema
uses, and `--self-test` proves the validator itself rejects what it should --
a validator that silently passes everything is worse than none.

Two layers of checking:

  1. structural — the JSON Schema at Schema/manifest.schema.json
  2. referential — things a schema cannot express: every GUID an entity
     references exists, every parent_id resolves, every warning code is in the
     catalogue, and every asset's GUID and sanitized path actually match what
     `ueo3de.naming` derives from its UE path. That last one is what catches
     the exporter and the manifest drifting apart.

Usage:
    python validate_manifest.py <manifest.json> [--schema <path>]
    python validate_manifest.py --self-test
Exit code 0 on success, 1 on any error.
"""

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_ROOT = os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                            "UEO3DEExporter", "Content", "Python")
DEFAULT_SCHEMA = os.path.join(REPO_ROOT, "Schema", "manifest.schema.json")

if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from ueo3de import manifest as manifest_module  # noqa: E402
from ueo3de import naming  # noqa: E402
from ueo3de.warnings import CODES  # noqa: E402


# ---------------------------------------------------------------------------
# minimal draft-07 subset validator
# ---------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _type_matches(value, expected):
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    python_type = _TYPES.get(expected)
    if python_type is None:
        return True
    if python_type is not bool and isinstance(value, bool):
        # In JSON a boolean is not a string/number/object; Python says bool is int.
        return python_type is bool
    return isinstance(value, python_type)


def _resolve(ref, root):
    if not ref.startswith("#/"):
        raise ValueError("only local refs are supported: " + ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate_schema(value, schema, root, path="$"):
    """Return a list of human-readable error strings."""
    errors = []

    if "$ref" in schema:
        return validate_schema(value, _resolve(schema["$ref"], root), root, path)

    if "type" in schema:
        expected = schema["type"]
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, option) for option in options):
            errors.append("%s: expected type %s, got %s"
                          % (path, "/".join(options), type(value).__name__))
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append("%s: expected const %r, got %r" % (path, schema["const"], value))
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r is not one of %r" % (path, value, schema["enum"]))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: shorter than minLength %d" % (path, schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: %r does not match %s" % (path, value, schema["pattern"]))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: %r below minimum %r" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append("%s: %r above maximum %r" % (path, value, schema["maximum"]))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: %d items, minItems %d" % (path, len(value), schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: %d items, maxItems %d" % (path, len(value), schema["maxItems"]))
        if "items" in schema:
            for index, item in enumerate(value):
                errors += validate_schema(item, schema["items"], root,
                                          "%s[%d]" % (path, index))

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing required property '%s'" % (path, key))
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors += validate_schema(item, properties[key], root,
                                          "%s.%s" % (path, key))
            elif schema.get("additionalProperties") is False:
                errors.append("%s: unexpected property '%s'" % (path, key))

    return errors


# ---------------------------------------------------------------------------
# referential integrity — the checks a schema cannot express
# ---------------------------------------------------------------------------

def validate_references(document):
    errors = []

    assets = {a["guid"]: a for a in document["assets"]}
    if len(assets) != len(document["assets"]):
        errors.append("assets: duplicate GUID")

    seen_paths = {}
    for asset in document["assets"]:
        ue_path = asset["ue_path"]

        if asset["kind"] == "texture":
            # Texture identities are role-keyed: the same UE texture exported
            # for two roles is two files with two GUIDs (the Atom image
            # builder picks its colour-space preset from the role suffix).
            role_key = asset["role"] if not asset.get("channel") \
                else "%s@%s" % (asset["role"], asset["channel"])
            expected_guid = naming.asset_guid(ue_path + "#" + role_key)
            # The role stays the filename SUFFIX (it selects the Atom image
            # preset); a channel split adds an infix between stem and role,
            # so the stem is a prefix and the role is a suffix, not one
            # contiguous string.
            expected_prefix = naming.sanitize_path(ue_path) + "_"
            if not asset["o3de_relative_path"].endswith("_%s.tga" % asset["role"]):
                errors.append("texture %s: path %r does not end with its role "
                              "suffix %r, so the Atom image preset will not "
                              "match" % (ue_path, asset["o3de_relative_path"],
                                         asset["role"]))
        else:
            expected_guid = naming.asset_guid(ue_path)
            expected_prefix = naming.sanitize_path(ue_path) + "."

        if asset["guid"] != expected_guid:
            errors.append("asset %s: guid %s does not match its derivation (%s)"
                          % (ue_path, asset["guid"], expected_guid))
        if not asset["o3de_relative_path"].startswith(expected_prefix):
            errors.append("asset %s: path %r is not the sanitization of its UE path (%r)"
                          % (ue_path, asset["o3de_relative_path"], expected_prefix))

        owner = seen_paths.get(asset["o3de_relative_path"])
        if owner is not None:
            errors.append("asset path collision: %s and %s both claim %s"
                          % (owner, ue_path, asset["o3de_relative_path"]))
        seen_paths[asset["o3de_relative_path"]] = ue_path

    entity_ids = set()
    for entity in document["entities"]:
        if entity["id"] in entity_ids:
            errors.append("entity %s: duplicate id" % entity["name"])
        entity_ids.add(entity["id"])
        expected_id = naming.entity_id(entity["ue_actor_path"])
        if entity["id"] != expected_id:
            errors.append("entity %s: id does not match uuid5 of its actor path"
                          % entity["name"])

    for entity in document["entities"]:
        parent = entity.get("parent_id")
        if parent is not None and parent not in entity_ids:
            errors.append("entity %s: parent_id %s does not resolve"
                          % (entity["name"], parent))

        mesh = entity.get("mesh")
        if mesh is not None:
            if mesh["asset_guid"] not in assets:
                errors.append("entity %s: mesh asset_guid does not resolve" % entity["name"])
            elif assets[mesh["asset_guid"]]["kind"] != "static_mesh":
                errors.append("entity %s: mesh asset_guid points at a non-mesh asset"
                              % entity["name"])
            for slot in mesh["material_slots"]:
                guid = slot["material_guid"]
                if guid is None:
                    continue
                if guid not in assets:
                    errors.append("entity %s: material slot %d does not resolve"
                                  % (entity["name"], slot["index"]))
                elif assets[guid]["kind"] != "material":
                    errors.append("entity %s: material slot %d points at a non-material"
                                  % (entity["name"], slot["index"]))

        skeletal = entity.get("skeletal")
        if skeletal is not None:
            if skeletal["asset_guid"] not in assets:
                errors.append("entity %s: skeletal asset_guid does not resolve"
                              % entity["name"])
            elif assets[skeletal["asset_guid"]]["kind"] != "skeletal_mesh":
                errors.append("entity %s: skeletal asset_guid points at a "
                              "non-skeletal asset" % entity["name"])
            anim_guid = skeletal.get("animation_guid")
            if anim_guid is not None:
                if anim_guid not in assets:
                    errors.append("entity %s: animation_guid does not resolve"
                                  % entity["name"])
                elif assets[anim_guid]["kind"] != "animation":
                    errors.append("entity %s: animation_guid points at a "
                                  "non-animation asset" % entity["name"])

        decal = entity.get("decal")
        if decal is not None and decal.get("material_guid") is not None:
            target = assets.get(decal["material_guid"])
            if target is None:
                errors.append("entity %s: decal material_guid does not resolve"
                              % entity["name"])
            elif target["kind"] != "material":
                errors.append("entity %s: decal material_guid points at a "
                              "non-material" % entity["name"])

        physics = entity.get("physics")
        if physics is not None:
            source = physics.get("shapes_from_asset")
            if source is not None and source not in assets:
                errors.append("entity %s: shapes_from_asset does not resolve"
                              % entity["name"])

    # Skeletal assets: the bone table must be internally consistent (M8) --
    # the artifact test asserts these names against the .actor product bytes.
    for asset in document["assets"]:
        if asset["kind"] != "skeletal_mesh":
            continue
        names = asset.get("bone_names") or []
        if len(names) != asset.get("bone_count"):
            errors.append("skeletal %s: bone_count %r != len(bone_names) %d"
                          % (asset["ue_path"], asset.get("bone_count"), len(names)))

    # material_data texture references must resolve to texture assets (M4).
    for asset in document["assets"]:
        data = asset.get("material_data")
        if not data:
            continue
        for key, spec in (data.get("properties") or {}).items():
            guid = spec.get("texture_guid")
            if guid is None:
                continue
            target = assets.get(guid)
            if target is None:
                errors.append("material %s: %s references texture %s which is "
                              "not in assets[]" % (asset["ue_path"], key, guid))
            elif target["kind"] != "texture":
                errors.append("material %s: %s references a non-texture asset"
                              % (asset["ue_path"], key))

    for record in document["warnings"]:
        if record["code"] not in CODES:
            errors.append("warning: unknown code %r (not in ueo3de.warnings.CODES)"
                          % record["code"])

    if document["schema_version"] not in manifest_module.SUPPORTED_SCHEMA_VERSIONS:
        errors.append("schema_version %r is not one of the supported %r"
                      % (document["schema_version"],
                         manifest_module.SUPPORTED_SCHEMA_VERSIONS))
    for key, expected in (("lane_a_rule", manifest_module.LANE_A_RULE),
                          ("lane_b_rule", manifest_module.LANE_B_RULE),
                          ("lane_b_skeletal_rule",
                           manifest_module.LANE_B_SKELETAL_RULE)):
        if document["units"].get(key) != expected:
            errors.append("units.%s %r does not match the exporter's %r"
                          % (key, document["units"].get(key), expected))

    return errors


def validate(document, schema):
    errors = validate_schema(document, schema, schema)
    if errors:
        # Referential checks assume a structurally valid document.
        return errors
    return validate_references(document)


def load_schema(path=DEFAULT_SCHEMA):
    with open(path, "r") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# self-test: prove the validator rejects what it should
# ---------------------------------------------------------------------------

def _self_test():
    schema = load_schema()
    failures = []

    def expect_errors(label, document, must_mention):
        errors = validate(document, schema)
        if not errors:
            failures.append(label + ": expected an error, got none")
        elif must_mention and not any(must_mention in e for e in errors):
            failures.append("%s: no error mentioned %r; got %r"
                            % (label, must_mention, errors))

    minimal = {
        "schema_version": manifest_module.SCHEMA_VERSION,
        "generator": {"tool": "t", "tool_version": "0", "engine": "e", "engine_version": "0"},
        "level": {"ue_package": "/Game/Maps/X", "name": "X"},
        "units": {"length": "meters", "angle": "degrees",
                  "coordinate_system": "o3de_right_handed_z_up",
                  "lane_a_rule": manifest_module.LANE_A_RULE,
                  "lane_b_rule": manifest_module.LANE_B_RULE,
                  "lane_b_skeletal_rule": manifest_module.LANE_B_SKELETAL_RULE},
        "assets": [], "entities": [], "warnings": [],
    }
    if validate(minimal, schema):
        failures.append("minimal valid document was rejected: %r" % validate(minimal, schema))

    bad = json.loads(json.dumps(minimal))
    bad["schema_version"] = manifest_module.SCHEMA_VERSION + 1
    expect_errors("newer-than-supported schema_version", bad, "is not one of")

    # The supported range must actually be validatable: a v6 document
    # failing STRUCTURALLY suppressed every referential check for half the
    # range the importer accepts (manifest_io.SUPPORTED_SCHEMA_VERSIONS).
    older = json.loads(json.dumps(minimal))
    older["schema_version"] = min(manifest_module.SUPPORTED_SCHEMA_VERSIONS)
    if validate(older, schema):
        failures.append("a supported older schema_version was rejected: %r"
                        % validate(older, schema))

    bad = json.loads(json.dumps(minimal))
    bad["units"]["lane_a_rule"] = "identity"
    expect_errors("wrong lane_a_rule", bad, "const")

    # A manifest whose geometry was not reflected the same way its transforms
    # were places correct transforms around mirrored meshes, and nothing
    # downstream notices. This is the check that refuses it.
    bad = json.loads(json.dumps(minimal))
    bad["units"]["lane_b_rule"] = "none"
    expect_errors("wrong lane_b_rule", bad, "const")

    bad = json.loads(json.dumps(minimal))
    del bad["warnings"]
    expect_errors("missing warnings", bad, "required")

    bad = json.loads(json.dumps(minimal))
    bad["surprise"] = 1
    expect_errors("unexpected top-level property", bad, "unexpected")

    entity = {
        "id": "00000000-0000-5000-8000-000000000000",
        "name": "E", "ue_class": "C", "ue_actor_path": "/p", "kind": "static_mesh",
        "parent_id": None, "mobility": "static",
        "transform": {
            "world": {"translation": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]},
            "local": {"translation": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]},
        },
    }

    bad = json.loads(json.dumps(minimal))
    negative = json.loads(json.dumps(entity))
    negative["transform"]["world"]["scale"] = [1, -1, 1]
    bad["entities"] = [negative]
    expect_errors("negative scale", bad, "minimum")

    bad = json.loads(json.dumps(minimal))
    short = json.loads(json.dumps(entity))
    short["transform"]["world"]["rotation"] = [0, 0, 1]
    bad["entities"] = [short]
    expect_errors("three-component quaternion", bad, "minItems")

    bad = json.loads(json.dumps(minimal))
    orphan = json.loads(json.dumps(entity))
    orphan["parent_id"] = "11111111-1111-5111-8111-111111111111"
    bad["entities"] = [orphan]
    expect_errors("dangling parent_id", bad, "parent_id")

    bad = json.loads(json.dumps(minimal))
    bad["warnings"] = [{"code": "NOT_A_REAL_CODE", "severity": "warn",
                        "subject": "s", "detail": "d"}]
    expect_errors("unknown warning code", bad, "unknown code")

    bad = json.loads(json.dumps(minimal))
    bad["assets"] = [{"guid": "00000000-0000-5000-8000-000000000000", "kind": "material",
                      "ue_path": "/Game/M", "name": "M",
                      "o3de_relative_path": "uetoo3de/game/m.material"}]
    expect_errors("guid not derived from the UE path", bad, "derivation")

    bad = json.loads(json.dumps(minimal))
    bad["assets"] = [{"guid": naming.asset_guid("/Game/M"), "kind": "material",
                      "ue_path": "/Game/M", "name": "M",
                      "o3de_relative_path": "uetoo3de/Game/M.material"}]
    expect_errors("unsanitized path", bad, "does not match")

    # M8: a skeletal rule mismatch means the importer's Rz180 composition no
    # longer matches how the geometry was produced -- refuse, don't yaw wrong.
    bad = json.loads(json.dumps(minimal))
    bad["units"]["lane_b_skeletal_rule"] = "none"
    expect_errors("wrong lane_b_skeletal_rule", bad, "const")

    # M8: a skeletal entity whose animation_guid points at a mesh would author
    # a Simple Motion component around the wrong product kind.
    bad = json.loads(json.dumps(minimal))
    skel_guid = naming.asset_guid("/Game/SK.SK")
    bad["assets"] = [{
        "guid": skel_guid, "kind": "skeletal_mesh", "ue_path": "/Game/SK",
        "name": "SK", "o3de_relative_path": "uetoo3de/game/sk.fbx",
        "bone_count": 2, "bone_names": ["Root", "Hips"],
        "material_slot_names": [], "material_slot_material_names": [],
    }]
    wrong = json.loads(json.dumps(entity))
    wrong["kind"] = "skeletal_mesh"
    wrong["id"] = naming.entity_id("/p")
    wrong["skeletal"] = {"asset_guid": skel_guid, "animation_guid": skel_guid,
                         "loop": True, "play": True, "material_slots": []}
    bad["entities"] = [wrong]
    expect_errors("animation_guid at a non-animation", bad, "non-animation")

    # M9 review: two texture entries writing ONE file. The same texture can
    # be requested for one role both whole and as an ORM channel split; a
    # role-only filename made them collide and one silently overwrote the
    # other's image data (measured on L_Showcase's T_Grass_ORM).
    bad = json.loads(json.dumps(minimal))
    tex = {"guid": naming.asset_guid("/Game/T#ao"), "kind": "texture",
           "ue_path": "/Game/T", "name": "T",
           "o3de_relative_path": "uetoo3de/game/t_ao.tga",
           "role": "ao", "channel": None, "srgb": False}
    split = json.loads(json.dumps(tex))
    split["guid"] = naming.asset_guid("/Game/T#ao@R")
    split["channel"] = "R"                       # same file as the whole one
    bad["assets"] = [tex, split]
    expect_errors("two textures claiming one file", bad, "collision")

    # ... and the role must stay the filename SUFFIX, or the Atom image
    # preset stops matching and the texture imports with the wrong settings.
    bad = json.loads(json.dumps(minimal))
    mis = json.loads(json.dumps(tex))
    mis["guid"] = naming.asset_guid("/Game/T#ao@R")
    mis["channel"] = "R"
    mis["o3de_relative_path"] = "uetoo3de/game/t_ao_r.tga"
    bad["assets"] = [mis]
    expect_errors("role no longer the filename suffix", bad, "role suffix")

    # M8: bone_count must equal len(bone_names) -- the .actor byte assertion
    # keys off the names, and a silent mismatch would weaken it.
    bad = json.loads(json.dumps(minimal))
    bad["assets"] = [{
        "guid": skel_guid, "kind": "skeletal_mesh", "ue_path": "/Game/SK",
        "name": "SK", "o3de_relative_path": "uetoo3de/game/sk.fbx",
        "bone_count": 3, "bone_names": ["Root", "Hips"],
        "material_slot_names": [], "material_slot_material_names": [],
    }]
    expect_errors("bone_count mismatch", bad, "bone_count")

    for failure in failures:
        print("SELF-TEST FAIL: " + failure)
    if failures:
        return 1
    print("validator self-test: %d rejection cases pass" % 19)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", help="path to manifest.json")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--self-test", action="store_true",
                        help="check that the validator rejects malformed documents")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.manifest:
        parser.error("a manifest path is required unless --self-test is given")

    with open(args.manifest, "r") as handle:
        document = json.load(handle)
    errors = validate(document, load_schema(args.schema))
    for error in errors:
        print("INVALID: " + error)
    if errors:
        print("%s: %d validation error(s)" % (args.manifest, len(errors)))
        return 1
    print("%s: valid (%d entities, %d assets, %d warnings)"
          % (args.manifest, len(document["entities"]), len(document["assets"]),
             len(document["warnings"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

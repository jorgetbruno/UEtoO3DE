"""
report.py — the importer's own warning channel.

The manifest's `warnings[]` belongs to the exporter: things UE had that the
interchange format could not carry. This is the other half -- things the
manifest carried faithfully that O3DE cannot represent the same way. They are
separate on purpose, because they are fixed in different places.

Same discipline as the exporter's catalogue (plan constraint 9): machine-
readable codes, an explicit catalogue, and tests that assert on codes rather
than on English. M10 turns this into the import dialog's summary.
"""

import json

INFO = "info"
WARN = "warn"
ERROR = "error"

CODES = {
    "XFORM_NONUNIFORM_SCALE_COMPONENT": (
        INFO, "AZ::Transform carries a single uniform scale, so a non-uniform "
              "UE scale is placed on an EditorNonUniformScaleComponent."),
    "XFORM_NONUNIFORM_SCALE_NOT_INHERITED": (
        WARN, "O3DE applies non-uniform scale at the component rather than in "
              "the transform hierarchy, so it does not reach child entities "
              "the way UE's does."),
    "MESH_MISSING": (
        WARN, "Entity is a static mesh actor in UE but carries no mesh "
              "reference; imported as a transform-only placeholder."),
    "ENTITY_KIND_DEFERRED": (
        INFO, "Entity kind is recognized but is imported by a later milestone; "
              "created as a transform-only placeholder so the hierarchy and "
              "its position survive."),

    # --- materials (M4 slot fidelity) ---
    "MAT_SLOT_UNMATCHED": (
        WARN, "A converted material had no matching slot label on the entity's "
              "model; that slot keeps the model's own default material. The "
              "label is the UE material asset name via the FBX."),
    "MAT_SLOT_UNUSED": (
        INFO, "A slot's material matched nothing and every model slot is "
              "already assigned: the mesh asset lists a slot that no render "
              "triangle uses, so the bake dropped it. Nothing was lost."),
    "MAT_SLOT_BY_ELIMINATION": (
        INFO, "A material matched no slot label (the mesh asset's slot has "
              "no default material, so the FBX carries no name for it), but "
              "exactly one model slot was unclaimed -- assigned by "
              "elimination."),
    "MAT_SLOT_LABEL_AMBIGUOUS": (
        WARN, "Two material slots on one mesh resolve to the same label but "
              "different materials; only the first can be assigned, because "
              "the FBX carries material names, not UE slot names."),
    "MAT_MODEL_NOT_READY": (
        WARN, "The entity's model asset did not stream in within the wait "
              "budget, so per-slot assignment fell back to the default slot "
              "with the first slot's material."),

    # --- lights (M5) ---
    "LIGHT_INTENSITY_APPROX": (
        WARN, "UE intensity units with no exact photometric meaning "
              "(unitless, nits) were converted with UE's own internal factor "
              "and an implicit 1 m^2 surface; brightness is approximate."),
    "LIGHT_RADIUS_EXPLICIT": (
        INFO, "UE's explicit attenuation radius was applied (Atom defaults to "
              "deriving the influence radius from intensity). Faithful to UE; "
              "differs from what a native O3DE light would do."),
    "LIGHT_SHADOWS_UNSUPPORTED": (
        WARN, "The UE light casts shadows but the mapped Atom light type does "
              "not support them; imported without shadows rather than with a "
              "flag that reads back true and does nothing."),
    "LIGHT_SOURCE_RADIUS_DROPPED": (
        INFO, "UE source radius made this an area light; imported as a "
              "punctual light, so soft shadow and specular width are lost."),
    "LIGHT_TEMPERATURE_DROPPED": (
        INFO, "UE colour temperature is not represented on Atom's light "
              "components; only the RGB colour carries over."),
    "LIGHT_TYPE_UNSUPPORTED": (
        WARN, "UE light class has no v1 mapping (rect/area lights); the "
              "entity is created with its transform but no light component."),

    # --- environment (M6) ---
    "ENV_SKYLIGHT_APPROX": (
        WARN, "UE's image-based skylight has no exportable irradiance images "
              "(Atom's Global Skylight needs diffuse+specular assets), so a "
              "Physical Sky is authored instead. Lighting is approximate."),
    "ENV_SKY_ATMOSPHERE_APPROX": (
        WARN, "UE SkyAtmosphere's scattering parameters have no Atom "
              "equivalent; a default-turbidity Physical Sky stands in."),
    "ENV_SKY_DUPLICATE": (
        INFO, "More than one actor maps to the sky; only the first is "
              "authored, because two Physical Sky components fight."),
    "ENV_FOG_APPROX": (
        WARN, "UE fog is exponential in height, Atom's is a distance ramp "
              "with a height band. Density and range are approximated."),
    "ENV_POSTPROCESS_UNBOUNDED": (
        WARN, "A bounded UE post-process volume becomes a level-wide PostFX "
              "layer; bounded PostFX needs a shape plus a weight modifier."),
    "ENV_POSTPROCESS_DISABLED": (
        INFO, "The UE post-process volume is disabled; no layer authored."),
    "ENV_BLOOM_THRESHOLD_APPROX": (
        INFO, "UE's negative bloom threshold is a 'no threshold' sentinel "
              "with no Atom equivalent; 0.0 is used."),
    "ENV_TYPE_UNSUPPORTED": (
        WARN, "Environment actor type has no v1 mapping; the entity keeps "
              "its transform only."),

    # --- physics (M3) ---
    "PHYS_SHAPE_APPROXIMATED": (
        WARN, "A collision shape could not be authored exactly on this backend "
              "(unsupported kind, non-uniform scale on a shape without a "
              "per-axis image, or a degenerate dimension) and was substituted. "
              "The same UE level legitimately differs per backend; this makes "
              "it visible."),
    "PHYS_PROFILE_FALLBACK": (
        WARN, "UE collision profile has no entry in collision_profiles.json; "
              "the named fallback layer was used. Channel semantics are lossy "
              "by design -- see the file."),
    "MASS_FROM_DENSITY": (
        INFO, "UE body had no explicit mass override; the backend derives mass "
              "from shape volume and its default density, which will not match "
              "UE's derived mass exactly."),
    "PHYS_MESH_FROM_RENDER": (
        INFO, "No simple collision primitives; a mesh collider was baked from "
              "the entity's render geometry (triangle mesh on static bodies, "
              "convex hull on dynamic ones)."),
}


class Report:
    def __init__(self):
        self._records = []
        self._seen = set()
        self.counters = {}

    def warn(self, code, subject, detail, severity=None):
        if code not in CODES:
            raise KeyError("unknown importer warning code: " + repr(code))
        if severity is None:
            severity = CODES[code][0]
        key = (code, severity, str(subject), str(detail))
        if key in self._seen:
            return
        self._seen.add(key)
        self._records.append({
            "code": code,
            "severity": severity,
            "subject": str(subject),
            "detail": str(detail),
        })

    def count(self, name, amount=1):
        self.counters[name] = self.counters.get(name, 0) + amount

    def records(self):
        return sorted(self._records,
                      key=lambda r: (r["code"], r["subject"], r["detail"]))

    def has_errors(self):
        return any(r["severity"] == ERROR for r in self._records)

    def to_dict(self):
        return {
            "counters": dict(sorted(self.counters.items())),
            "warnings": self.records(),
        }

    def write(self, path):
        with open(path, "w") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def __len__(self):
        return len(self._records)

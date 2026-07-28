"""
warnings.py — the structured `manifest.warnings[]` channel (plan constraint 9).

"Never silently drop data." Anything the converter cannot map becomes a record
with a machine-readable **code**; tests assert on codes and never on English
strings, so the wording below can be improved without breaking a single test.

`CODES` is the single catalogue. The validator rejects any manifest carrying a
code that is not listed here, which turns a typo'd code -- the one class of bug
that would otherwise sail through both the schema and the golden diff -- into a
test failure. MAPPING.md renders this same table for humans (plan M11).
"""

INFO = "info"
WARN = "warn"
ERROR = "error"

SEVERITIES = (INFO, WARN, ERROR)

# code -> (default severity, meaning). Milestones append; nothing is renamed.
CODES = {
    # --- level / export integrity (M1) ---
    "LEVEL_WORLD_PARTITION": (
        ERROR, "Level is World Partition enabled; actor iteration would yield "
               "an almost-empty list. Out of v1 scope."),
    "LEVEL_WP_DETECT_FAILED": (
        ERROR, "World Partition detection itself failed, so an empty actor "
               "list cannot be distinguished from an unloaded partitioned level."),
    "LEVEL_EXTERNAL_ACTORS": (
        WARN, "Level uses One File Per Actor without World Partition. Actors "
              "still enumerate, but the export is untested against this layout."),
    "ASSET_PATH_COLLISION": (
        ERROR, "Two distinct UE assets sanitize onto the same O3DE-relative "
               "path; one would silently overwrite the other."),

    # --- transforms (M1, Lane A) ---
    "XFORM_NEGATIVE_SCALE": (
        WARN, "UE actor carries a mirror (odd negative scale axes) inside an "
              "attach hierarchy, where folding it into a mesh variant would "
              "break the children's frames; absolute value exported and the "
              "mirror is lost. Flat actors take the mirrored-variant path "
              "(XFORM_MIRRORED_MESH_VARIANT) instead."),

    # --- actor coverage (M1) ---
    "ACTOR_CLASS_UNMAPPED": (
        WARN, "Actor class has no mapping in v1; exported as a placeholder "
              "entity carrying name, class and transform only."),
    "ACTOR_DEFERRED": (
        INFO, "Actor class is recognized but is owned by a later milestone; "
              "exported as a placeholder with its transform preserved."),
    "ACTOR_COMPONENTS_EXTRACTED": (
        INFO, "Unmapped actor class (a Blueprint) whose StaticMeshComponents "
              "were exported as child entities; scripted behaviour and any "
              "non-mesh components do not carry over."),

    # --- negative scale fidelity (M4.5) ---
    "XFORM_MIRRORED_MESH_VARIANT": (
        INFO, "Odd number of negative scale axes: the signs were folded into "
              "the rotation and the entity references a mirror-X mesh "
              "variant baked for it. Geometry is faithful; the mesh asset is "
              "doubled."),

    # --- meshes and materials (M1 records, M2/M4 consume) ---
    "MESH_SLOT_EMPTY": (
        INFO, "Static mesh material slot has no material assigned; the O3DE "
              "importer will leave the slot on its default material."),
    "MAT_EXPR_UNSUPPORTED": (
        WARN, "A material property is driven by an expression outside the v1 "
              "subset. If the property is base colour the whole material falls "
              "back to the default; otherwise only that property is dropped."),
    "MAT_FUNCTION_PASSTHROUGH": (
        INFO, "A channel driven by unsupported math (function call, contrast, "
              "desaturation, blend chain) was approximated by the nearest "
              "texture beneath it; the surrounding math is dropped."),
    "MAT_BLEND_UNSUPPORTED": (
        WARN, "UE blend mode outside Opaque/Masked/Translucent; imported as "
              "translucent."),
    "MAT_PARAMS_BY_NAME": (
        WARN, "The master's graph dead-ends in a material function whose "
              "internals Python cannot walk; the material was classified "
              "from its texture parameter NAMES instead. Role assignment is "
              "heuristic -- check the named parameters."),

    # --- environment (M6) ---
    "ENV_POSTPROCESS_UNMAPPED": (
        INFO, "A post-process setting the UE artist explicitly overrode has no "
              "M6 mapping; it is carried in the manifest but not authored."),
    "ENV_VOLUME_BOUNDS_UNKNOWN": (
        WARN, "A bounded post-process volume's extents could not be read; the "
              "importer cannot size the equivalent volume."),

    # --- physics source data (M1 records, M3 consumes) ---
    "PHYS_NO_SIMPLE_COLLISION": (
        INFO, "Static mesh has no simple collision primitives; M3 must fall "
              "back to a mesh collider built from the render geometry."),
    "PHYS_DEGENERATE_SHAPE": (
        WARN, "Collision primitive has a zero or near-zero dimension. Most "
              "solvers reject or misbehave on degenerate shapes."),
    "PHYS_SHAPE_UNSUPPORTED": (
        WARN, "Collision primitive kind has no v1 mapping and was skipped."),
}


class Warnings:
    """Ordered, deduplicated collector for `manifest.warnings[]`."""

    def __init__(self):
        self._records = []
        self._seen = set()

    def add(self, code, subject, detail, severity=None):
        if code not in CODES:
            # A typo'd code is a bug in the exporter, not a data problem.
            raise KeyError("unknown warning code: " + repr(code))
        if severity is None:
            severity = CODES[code][0]
        if severity not in SEVERITIES:
            raise ValueError("bad severity: " + repr(severity))
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

    def records(self):
        """Deterministic order: by code, then subject, then detail.

        Sorted rather than chronological so that reordering the actor walk
        never shows up as a golden-file diff.
        """
        return sorted(self._records,
                      key=lambda r: (r["code"], r["subject"], r["detail"]))

    def count_by_severity(self, severity):
        return sum(1 for r in self._records if r["severity"] == severity)

    def has_errors(self):
        return self.count_by_severity(ERROR) > 0

    def __len__(self):
        return len(self._records)

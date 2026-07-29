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
import time

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
    "DECAL_MATERIAL_UNCONVERTED": (
        WARN, "A decal's material did not convert through the StandardPBR "
              "subset; the decal entity imports with its volume and sort key "
              "but no material assigned."),
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
    "PHYS_COLLIDER_NOT_BAKED": (
        ERROR, "A mesh collider reached the saved prefab with no baked geometry "
               "(no CookedData), so it collides with nothing. The bake runs on "
               "the component's tick and had not finished when the prefab was "
               "serialized. Re-import with a larger UEO3DE_SETTLE_FRAMES; the "
               "bake cannot be recovered afterwards, because the in-memory "
               "template is a snapshot and O3DE refuses to re-create a prefab "
               "in the same session (both measured -- see PERFORMANCE.md)."),
    "PHYS_MESH_NOT_COOKED": (
        WARN, "The mesh needs a cooked physics mesh (.pxmesh) on this backend, "
              "but the Asset Processor produced none -- either the staged "
              "sidecar predates cooked-mesh support (restage to fix) or the "
              "cook failed (check the AP log). The affected entities fall "
              "back to AABB boxes over each convex element, or to no collider "
              "where the fallback needed a triangle mesh."),
    "PHYS_MESH_ASSET_MISSING": (
        ERROR, "A PhysX mesh collider reached the saved prefab without a "
               "cooked physics mesh reference, so it collides with nothing. "
               "The reference was set through the editor without error, which "
               "makes this the asset-route sibling of PHYS_COLLIDER_NOT_BAKED: "
               "a write the editor accepted is not proof of what serialized, "
               "so the file is checked after the save."),

    # --- incremental re-import (M10) ---
    "REIMPORT_ENTITY_ADDED": (
        INFO, "The actor is new since the previous import of this prefab."),
    "REIMPORT_ENTITY_REMOVED": (
        INFO, "The actor existed in the previous import and is gone from the "
              "new manifest; its entity is not recreated."),
    "REIMPORT_ENTITY_CONFLICT": (
        WARN, "The entity's transform in O3DE differs from what the previous "
              "import authored, so someone edited it by hand. The manual edit "
              "is KEPT and the manifest's value is not applied -- re-importing "
              "must never silently discard someone's work."),
    "REIMPORT_LEDGER_MISSING": (
        INFO, "A re-import was requested but this prefab has no import ledger "
              "(never imported by this version, or the file was deleted). "
              "Treated as a first import: everything is authored fresh and "
              "no hand edits can be detected."),
    "REIMPORT_ENTITY_UNMATCHED": (
        WARN, "The previous import authored an entity of this name and the "
              "prefab no longer contains one -- it was renamed or deleted in "
              "O3DE. Entities are matched back to the prefab by name, so any "
              "hand edits on it cannot be found and are replaced by the "
              "manifest's values. Renaming an imported entity in O3DE breaks "
              "the link; rename the actor in UE instead."),
    "REIMPORT_CONFLICT_NOT_PRESERVED": (
        ERROR, "An entity was reported as hand-edited but its edit could not "
               "be restored into the rebuilt prefab, because no entity of the "
               "expected name was found there. The edit is lost. Being told "
               "an edit was kept when it was not is worse than either "
               "outcome on its own, so this is an error rather than a "
               "warning."),
    "REIMPORT_NAME_COLLISION": (
        WARN, "Two manifest entities share a name. Entities are matched back "
              "to the prefab by name, so hand-edit detection cannot tell "
              "these two apart and their conflicts are not reported."),
}


class _Phase:
    """Times a block and accumulates it under `name`. Re-entrant by name, so a
    phase entered once per entity sums into one figure."""

    def __init__(self, report, name):
        self._report = report
        self._name = name
        self._started = None

    def __enter__(self):
        self._started = time.perf_counter()
        return self

    def __exit__(self, *_exc):
        elapsed = time.perf_counter() - self._started
        self._report.timings[self._name] = \
            self._report.timings.get(self._name, 0.0) + elapsed
        return False


class Report:
    def __init__(self):
        self._records = []
        self._seen = set()
        self.counters = {}
        # Where the wall clock went, per phase. M11 recorded 809 s to import a
        # 2905-entity level and PERFORMANCE.md then ATTRIBUTED that cost to the
        # collider bakes -- a plausible guess with nothing behind it, in a
        # document that otherwise carries only measurements. Timings make the
        # attribution checkable, and give anyone optimising this a target
        # instead of an intuition.
        self.timings = {}
        # Breakdowns NESTED inside a phase, kept separate on purpose: they are
        # already counted by their parent, so folding them into `timings` would
        # double-count and break the "phases account for the whole import"
        # assertion that makes the top-level table trustworthy.
        self.subtimings = {}

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

    def phase(self, name):
        """`with report.phase("physics"):` — accumulates wall clock by name."""
        return _Phase(self, name)

    def timing_rows(self):
        """`[(name, seconds, percent)]`, slowest first.

        Slowest-first because the only question anyone asks of this table is
        "what do I fix", and that answer should be the first line.
        """
        total = sum(self.timings.values())
        rows = sorted(self.timings.items(), key=lambda kv: -kv[1])
        return [(name, seconds, (100.0 * seconds / total) if total else 0.0)
                for name, seconds in rows]

    def records(self):
        return sorted(self._records,
                      key=lambda r: (r["code"], r["subject"], r["detail"]))

    def has_errors(self):
        return any(r["severity"] == ERROR for r in self._records)

    def to_dict(self):
        return {
            "counters": dict(sorted(self.counters.items())),
            "timings_seconds": {name: round(seconds, 3)
                                for name, seconds in sorted(self.timings.items())},
            "subtimings_seconds": {name: round(seconds, 3)
                                   for name, seconds in sorted(self.subtimings.items())},
            "warnings": self.records(),
        }

    def write(self, path):
        with open(path, "w") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def to_text(self, title=None):
        """A plain-text report for the M10 dialog's "Save as .txt".

        Every warning carries its catalogue explanation, not just its code:
        the file is read by someone deciding whether their level imported
        correctly, away from this source tree, and a bare
        `PHYS_SHAPE_APPROXIMATED` tells them nothing about what to look at.
        """
        out = []
        if title:
            out.append(title)
            out.append("=" * len(title))
            out.append("")

        out.append("Counters")
        out.append("--------")
        if self.counters:
            width = max(len(name) for name in self.counters)
            for name in sorted(self.counters):
                out.append("  %-*s  %d" % (width, name, self.counters[name]))
        else:
            out.append("  (none)")
        out.append("")

        rows = self.timing_rows()
        if rows:
            total = sum(self.timings.values())
            out.append("Where the time went  (%.1f s total)" % total)
            out.append("-------------------")
            width = max(len(name) for name in self.timings)
            for name, seconds, percent in rows:
                out.append("  %-*s  %8.1f s  %5.1f%%" % (width, name, seconds, percent))
            if self.subtimings:
                out.append("")
                out.append("  within a phase (already counted above):")
                sub_width = max(len(name) for name in self.subtimings)
                for name, seconds in sorted(self.subtimings.items(),
                                            key=lambda kv: -kv[1]):
                    share = (100.0 * seconds / total) if total else 0.0
                    out.append("    %-*s  %8.1f s  %5.1f%%"
                               % (sub_width, name, seconds, share))
            out.append("")

        records = self.records()
        by_severity = {}
        for record in records:
            by_severity.setdefault(record["severity"], []).append(record)
        out.append("Warnings: %d (%s)"
                   % (len(records),
                      ", ".join("%d %s" % (len(by_severity[s]), s)
                                for s in (ERROR, WARN, INFO) if s in by_severity)
                      or "none"))
        out.append("-" * len(out[-1]))
        if not records:
            out.append("  (none)")
        for severity in (ERROR, WARN, INFO):
            group = by_severity.get(severity)
            if not group:
                continue
            by_code = {}
            for record in group:
                by_code.setdefault(record["code"], []).append(record)
            for code in sorted(by_code):
                items = by_code[code]
                out.append("")
                out.append("[%s] %s  (x%d)" % (severity.upper(), code, len(items)))
                explanation = CODES.get(code, (severity, ""))[1]
                if explanation:
                    out.append("  what it means: " + explanation)
                for record in items:
                    out.append("    - %s: %s" % (record["subject"], record["detail"]))
        out.append("")
        return "\n".join(out)

    def __len__(self):
        return len(self._records)

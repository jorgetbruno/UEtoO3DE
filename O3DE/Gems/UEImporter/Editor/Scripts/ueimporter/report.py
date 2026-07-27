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

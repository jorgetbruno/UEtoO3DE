"""
m10_dialog.py — the import UX, asserted without a display.

The plan states one dropdown rule precisely enough to test: the physics
backend is "pre-selected from detection, disabled when only one backend
resolves". That is a real safety rule, not decoration -- offering a backend
whose components do not resolve is how a user ends up with a level containing
no physics at all (constraint 5: available is not active).

The editor embeds a live QApplication even under -BatchMode (measured in
probe_m10_menu.py), so the widgets can be BUILT and interrogated here; only
`exec_()` needs a display. That is the difference between a UI that was
written and a UI that was checked.

Covered:
  * the pure rule, on all four cases (none / one / two+hint / two-no-hint);
  * the same rule as actually realised in the QComboBox;
  * the Import button disabled when nothing resolves;
  * this project's REAL detection agreeing with UEO3DE_EXPECT_BACKEND;
  * the summary dialog's warnings table matching the report;
  * report.to_text() carrying each code, its meaning, and its subject.

Run: Tests/o3de/run_o3de_python.bat Tests/m10/m10_dialog.py <result> <project>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'm10_dialog_result.txt')

EXPECT_BACKEND = os.environ.get("UEO3DE_EXPECT_BACKEND", "").strip().lower()

lines = []
failures = []


def log(msg=""):
    lines.append(str(msg))
    print(msg)


def fail(msg):
    failures.append(str(msg))
    log('FAIL: ' + str(msg))


def check(condition, msg):
    if not condition:
        fail(msg)
    return condition


def main():
    from ueimporter import dialog, report as report_module
    from ueimporter.adapters import detection

    log("=== 1. the dropdown rule, as pure logic ===")
    none_case = dialog.backend_choices([])
    log("  []                -> %r" % none_case)
    check(none_case["choices"] == [] and not none_case["enabled"],
          "with no backend resolving the dropdown must be empty and disabled")

    one_case = dialog.backend_choices(["jolt"])
    log("  ['jolt']          -> %r" % one_case)
    check(one_case["choices"] == ["jolt"], "one backend must offer exactly it")
    check(one_case["enabled"] is False,
          "THE rule: one resolving backend means a DISABLED dropdown")
    check(one_case["selected"] == 0, "the only backend must be pre-selected")

    two_hint = dialog.backend_choices(["jolt", "physx"], "physx")
    log("  both + hint physx -> %r" % two_hint)
    check(two_hint["enabled"] is True,
          "two resolving backends must leave the choice to the user")
    check(two_hint["choices"][two_hint["selected"]] == "physx",
          "the Settings Registry hint must drive the pre-selection")

    two_none = dialog.backend_choices(["jolt", "physx"], None)
    log("  both, no hint     -> %r" % two_none)
    check(two_none["enabled"] is True and two_none["selected"] == 0,
          "without a hint, both are offered with the first pre-selected")
    check("cannot be detected" in two_none["note"],
          "the ambiguous case must say why the user is being asked")

    log("")
    log("=== 2. the same rule, in the actual QComboBox ===")
    from PySide2 import QtWidgets
    application = QtWidgets.QApplication.instance()
    check(application is not None,
          "no QApplication in this editor; the dialog cannot be built at all")

    for available, hint, expect_enabled, expect_count, expect_current in (
            ([], None, False, 0, ""),
            (["jolt"], None, False, 1, "jolt"),
            # The hint is the SECOND entry, so a combo left on its default
            # index 0 shows 'jolt' and fails. Asserting `currentText() in
            # available` instead would be a tautology -- the combo is
            # populated FROM that list, so any in-range index passes it.
            (["jolt", "physx"], "physx", True, 2, "physx"),
            (["jolt", "physx"], None, True, 2, "jolt")):
        widget = dialog.make_import_dialog(available, hint, "", "")
        combo = widget.backend_combo
        log("  available=%-18r combo: count=%d enabled=%s current=%r import=%s"
            % (available, combo.count(), combo.isEnabled(),
               combo.currentText(), widget.ok_button.isEnabled()))
        check(combo.count() == expect_count,
              "combo should hold %d entries for %r, holds %d"
              % (expect_count, available, combo.count()))
        check(combo.isEnabled() is expect_enabled,
              "combo enabled=%s for %r, expected %s"
              % (combo.isEnabled(), available, expect_enabled))
        check(widget.ok_button.isEnabled() is bool(available),
              "the Import button must be disabled exactly when no backend "
              "resolves (available=%r, enabled=%s)"
              % (available, widget.ok_button.isEnabled()))
        check(combo.currentText() == expect_current,
              "combo pre-selected %r, expected %r for available=%r hint=%r "
              "-- the Settings Registry hint must drive the selection"
              % (combo.currentText(), expect_current, available, hint))
        widget.deleteLater()

    log("")
    log("=== 3. this project's real detection ===")
    resolved = detection.available(detection.editor_resolver)
    log("  detection.available() -> %r" % (resolved,))
    check(len(resolved) >= 1,
          "no physics backend resolves in this project at all")
    if EXPECT_BACKEND:
        check(EXPECT_BACKEND in resolved,
              "UEO3DE_EXPECT_BACKEND=%r but only %r resolve here"
              % (EXPECT_BACKEND, resolved))
    real = dialog.make_import_dialog(resolved, None, "", "")
    log("  real dialog: choices=%r enabled=%s note=%r"
        % ([real.backend_combo.itemText(i) for i in range(real.backend_combo.count())],
           real.backend_combo.isEnabled(), real.backend_decision["note"]))
    check(real.backend_combo.isEnabled() is (len(resolved) > 1),
          "the real dialog's dropdown must be enabled only when this project "
          "genuinely has a choice (resolved=%r)" % (resolved,))
    real.deleteLater()

    log("")
    log("=== 4. default prefab name comes from the manifest's level ===")
    manifest = os.path.join(REPO_ROOT, "Exports", "Fixture_01", "manifest.json")
    if os.path.isfile(manifest):
        name = dialog.default_prefab_name(manifest)
        log("  %s -> %r" % (manifest, name))
        check(name == "Fixture_01",
              "expected the level name from inside the manifest, got %r" % name)
    check(dialog.default_prefab_name("/nope/Missing_Level/manifest.json")
          == "Missing_Level",
          "an unreadable manifest must fall back to the folder name")

    log("")
    log("=== 5. the summary dialog reflects the report ===")
    report = report_module.Report()
    report.count("entities_created", 30)
    report.count("materials_assigned", 12)
    report.warn("PHYS_SHAPE_APPROXIMATED", "Fixture_Sphere",
                "sphere approximated on this backend")
    report.warn("REIMPORT_ENTITY_CONFLICT", "MovedByHand",
                "edited in O3DE since the last import")
    summary = dialog.make_summary_dialog(report, "C:/proj/Prefabs/X.prefab",
                                         reimported=True)
    rows = summary.warnings_table.rowCount()
    log("  warnings in report=%d, table rows=%d" % (len(report.records()), rows))
    check(rows == len(report.records()),
          "the table must show every warning: %d rows for %d records"
          % (rows, len(report.records())))
    codes = {summary.warnings_table.item(r, 1).text() for r in range(rows)}
    check(codes == {"PHYS_SHAPE_APPROXIMATED", "REIMPORT_ENTITY_CONFLICT"},
          "table codes wrong: %r" % (codes,))
    headline = "\n".join(dialog.summary_lines(report, "X.prefab", True))
    check("Entities created:     30" in headline,
          "the headline must carry the entity count: %r" % headline)
    summary.deleteLater()

    log("")
    log("=== 6. report.to_text() is readable away from this source tree ===")
    text = report.to_text("UE import report - X")
    for needle in ("PHYS_SHAPE_APPROXIMATED", "REIMPORT_ENTITY_CONFLICT",
                   "Fixture_Sphere", "MovedByHand", "entities_created",
                   "what it means"):
        check(needle in text, "report text is missing %r" % needle)
    check("[WARN]" in text, "severity must be visible in the text report")
    log("  --- first 12 lines of the exported report ---")
    for line in text.splitlines()[:12]:
        log("    " + line)


try:
    main()
except Exception:
    fail('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: ' + ('PASS' if not failures else 'FAIL (%d)' % len(failures)))
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
if not failures:
    _general.exit_no_prompt()
else:
    os._exit(1)

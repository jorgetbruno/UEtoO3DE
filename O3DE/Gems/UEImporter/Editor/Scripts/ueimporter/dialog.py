"""
dialog.py — the Tools menu's import dialog and its summary (plan M10).

O3DE's editor embeds PySide2 and a live QApplication, so a gem with no C++ can
put up a real dialog. Two of them:

  * `ImportDialog`  -- pick a manifest, name the prefab, choose the physics
    backend, decide whether to re-import incrementally;
  * `SummaryDialog` -- what the import did: counters, every warning with its
    catalogue meaning, and a "Save as .txt" that writes `report.to_text()`.

**The decisions are pure functions, the widgets are a thin shell.** The
backend dropdown has a rule the plan is specific about -- pre-selected from
detection, disabled when only one backend resolves -- and a rule stated that
precisely deserves a test. `backend_choices()` is that rule, and
`Tests/m10/m10_dialog.py` asserts it against both a one-backend and a
two-backend project without ever showing a window.

Nothing here may be imported at editor startup: `menu.py` imports this module
inside the click handler, so a broken PySide2 costs a menu item rather than
the whole editor.
"""

import os

# --------------------------------------------------------------------------
# pure decisions
# --------------------------------------------------------------------------

def backend_choices(available, settings_hint=None):
    """What the physics dropdown should show. No Qt, no editor.

    Returns `{choices, selected, enabled, note}`.

    The plan's rule -- "pre-selected from detection, disabled when only one
    backend resolves" -- exists because a dropdown offering a backend whose
    components do not resolve is an invitation to produce a level with no
    physics at all (constraint 5: available is not active). So the list is
    exactly what resolved, never the two names this project knows about.
    """
    choices = list(available or [])
    if not choices:
        return {"choices": [], "selected": -1, "enabled": False,
                "note": "No physics backend resolves in this project. "
                        "Enable the Jolt or PhysX gem before importing."}
    if len(choices) == 1:
        return {"choices": choices, "selected": 0, "enabled": False,
                "note": "%s is the only backend in this project."
                        % choices[0].capitalize()}
    selected = choices.index(settings_hint) if settings_hint in choices else 0
    return {"choices": choices, "selected": selected, "enabled": True,
            "note": "Both backends resolve here, so the choice cannot be "
                    "detected -- pick the one this project actually "
                    "simulates with." + ("" if settings_hint in choices else
                                         " (no Settings Registry hint found.)")}


def default_prefab_name(manifest_path):
    """`.../Exports/L_Showcase/manifest.json` -> `L_Showcase`.

    The level name inside the manifest is authoritative when it is readable;
    the folder is the fallback, because a dialog that opens with an empty
    required field for no reason is a dialog that annoys people.
    """
    try:
        import json
        with open(manifest_path, "r") as handle:
            document = json.load(handle)
        name = (document.get("level") or {}).get("name")
        if name:
            return str(name)
    except Exception:
        pass
    folder = os.path.basename(os.path.dirname(os.path.abspath(str(manifest_path))))
    return folder or "ImportedLevel"


def summary_lines(report, prefab_path, reimported=False):
    """The headline the summary dialog opens with."""
    counters = report.counters
    lines = [
        "Entities created:     %d" % counters.get("entities_created", 0),
        "Materials assigned:   %d" % counters.get("materials_assigned", 0),
        "Physics bodies:       %d" % counters.get("physics_bodies", 0),
        "Lights:               %d" % counters.get("lights_created", 0),
    ]
    if reimported:
        lines.append("Re-import:            %d added, %d removed, %d hand-edited"
                     % (counters.get("reimport_added", 0),
                        counters.get("reimport_removed", 0),
                        counters.get("reimport_conflicts", 0)))
    lines.append("Prefab:               " + str(prefab_path))
    return lines


# --------------------------------------------------------------------------
# the widgets
# --------------------------------------------------------------------------

def _qt():
    from PySide2 import QtCore, QtWidgets
    return QtCore, QtWidgets


def make_import_dialog(available, settings_hint=None, manifest_path="",
                       project_root="", parent=None):
    """Build (do not show) the import dialog. Returns the dialog instance.

    Constructing without showing is what makes this testable in -BatchMode:
    the editor already owns a QApplication, so the widgets exist and can be
    interrogated, and only `exec_()` needs a display.
    """
    QtCore, QtWidgets = _qt()

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Import UE Manifest")
    dialog.setMinimumWidth(560)
    layout = QtWidgets.QVBoxLayout(dialog)

    form = QtWidgets.QFormLayout()

    manifest_row = QtWidgets.QHBoxLayout()
    dialog.manifest_edit = QtWidgets.QLineEdit(manifest_path)
    dialog.manifest_edit.setPlaceholderText("path to manifest.json")
    browse = QtWidgets.QPushButton("Browse...")
    manifest_row.addWidget(dialog.manifest_edit)
    manifest_row.addWidget(browse)
    form.addRow("Manifest:", manifest_row)

    dialog.prefab_edit = QtWidgets.QLineEdit(
        default_prefab_name(manifest_path) if manifest_path else "")
    form.addRow("Prefab name:", dialog.prefab_edit)

    decision = backend_choices(available, settings_hint)
    dialog.backend_combo = QtWidgets.QComboBox()
    for choice in decision["choices"]:
        dialog.backend_combo.addItem(choice)
    if decision["selected"] >= 0:
        dialog.backend_combo.setCurrentIndex(decision["selected"])
    dialog.backend_combo.setEnabled(decision["enabled"])
    dialog.backend_decision = decision
    form.addRow("Physics backend:", dialog.backend_combo)

    note = QtWidgets.QLabel(decision["note"])
    note.setWordWrap(True)
    form.addRow("", note)

    dialog.reimport_check = QtWidgets.QCheckBox(
        "Incremental re-import (keep hand edits, report conflicts)")
    dialog.reimport_check.setChecked(True)
    form.addRow("", dialog.reimport_check)

    layout.addLayout(form)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Import")
    layout.addWidget(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    dialog.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
    # Nothing resolves -> nothing can be authored. Say so by disabling the
    # button rather than by failing halfway through an import.
    dialog.ok_button.setEnabled(bool(decision["choices"]))

    def pick_manifest():
        start = os.path.dirname(dialog.manifest_edit.text()) or project_root
        chosen, _filter = QtWidgets.QFileDialog.getOpenFileName(
            dialog, "Select a UEtoO3DE manifest", start,
            "UE manifest (manifest.json);;JSON (*.json)")
        if chosen:
            dialog.manifest_edit.setText(chosen)
            if not dialog.prefab_edit.text():
                dialog.prefab_edit.setText(default_prefab_name(chosen))

    browse.clicked.connect(pick_manifest)
    return dialog


def dialog_options(dialog):
    """Read the user's answers back out of an import dialog."""
    return {
        "manifest_path": dialog.manifest_edit.text().strip(),
        "prefab_name": dialog.prefab_edit.text().strip(),
        "backend": (dialog.backend_combo.currentText().strip() or None),
        "reimport": bool(dialog.reimport_check.isChecked()),
    }


def make_summary_dialog(report, prefab_path, reimported=False, parent=None):
    """The post-import summary, with the warnings list and its .txt export."""
    QtCore, QtWidgets = _qt()

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("UE Import Summary")
    dialog.setMinimumSize(760, 520)
    layout = QtWidgets.QVBoxLayout(dialog)

    headline = QtWidgets.QLabel("\n".join(summary_lines(report, prefab_path,
                                                        reimported)))
    headline.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    layout.addWidget(headline)

    records = report.records()
    layout.addWidget(QtWidgets.QLabel("Warnings: %d" % len(records)))

    table = QtWidgets.QTableWidget(len(records), 4)
    table.setHorizontalHeaderLabels(["Severity", "Code", "Subject", "Detail"])
    table.horizontalHeader().setStretchLastSection(True)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    for row, record in enumerate(records):
        for column, key in enumerate(("severity", "code", "subject", "detail")):
            table.setItem(row, column,
                          QtWidgets.QTableWidgetItem(str(record[key])))
    table.resizeColumnsToContents()
    dialog.warnings_table = table
    layout.addWidget(table)

    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
    save = QtWidgets.QPushButton("Save as .txt...")
    buttons.addButton(save, QtWidgets.QDialogButtonBox.ActionRole)
    layout.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)

    def save_report():
        start = os.path.splitext(str(prefab_path))[0] + "_import_report.txt"
        chosen, _filter = QtWidgets.QFileDialog.getSaveFileName(
            dialog, "Save import report", start, "Text (*.txt)")
        if not chosen:
            return
        with open(chosen, "w") as handle:
            handle.write(report.to_text(
                "UE import report - " + os.path.basename(str(prefab_path))))
        QtWidgets.QMessageBox.information(dialog, "Saved", "Wrote " + chosen)

    save.clicked.connect(save_report)
    dialog.save_button = save
    return dialog


# --------------------------------------------------------------------------
# the menu item's payload
# --------------------------------------------------------------------------

def run_import_dialog(parent=None):
    """Ask, import, report. Called from the Tools menu."""
    QtCore, QtWidgets = _qt()

    import azlmbr.legacy.general as general

    from . import importer
    from .adapters import detection

    project_root = general.get_game_folder().rstrip("/\\")
    try:
        resolved = detection.available(detection.editor_resolver)
    except Exception:
        resolved = []
    hint = detection.settings_hint()

    dialog = make_import_dialog(resolved, hint, "", project_root)
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return None

    options = dialog_options(dialog)
    if not options["manifest_path"]:
        QtWidgets.QMessageBox.warning(None, "Import UE Manifest",
                                      "No manifest selected.")
        return None

    export_root = os.path.dirname(os.path.abspath(options["manifest_path"]))
    prefab_path = "%s/Prefabs/%s.prefab" % (project_root,
                                            options["prefab_name"] or "ImportedLevel")

    # The import opens a scratch level to author in, and it does so with
    # `open_level_no_prompt` -- the variant that skips the save-changes modal.
    # From a batch script that is correct and deliberate. From a menu item it
    # is data loss: a user with unsaved work in the level they are standing in
    # loses it the moment they press Import, with no warning and no undo. So
    # the UI asks first. (The batch path is unchanged; it has no user to ask.)
    from . import importer as importer_module
    scratch_level = importer_module.scratch_level_name()
    proceed = QtWidgets.QMessageBox.warning(
        None, "Import UE Manifest",
        "Importing opens the '%s' level to build the prefab in.\n\n"
        "Your current level will be closed WITHOUT a save prompt, so save "
        "any work first.\n\nContinue?" % scratch_level,
        QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        QtWidgets.QMessageBox.Cancel)
    if proceed != QtWidgets.QMessageBox.Ok:
        return None

    progress = QtWidgets.QProgressDialog("Importing...", "", 0, 0, None)
    progress.setWindowTitle("Import UE Manifest")
    progress.setCancelButton(None)
    progress.setWindowModality(QtCore.Qt.ApplicationModal)
    progress.show()

    def log(message):
        progress.setLabelText(str(message)[:160])
        QtWidgets.QApplication.processEvents()
        print("[UEImporter] " + str(message))

    try:
        importer.stage_only(options["manifest_path"],
                            os.path.join(export_root, "Assets"),
                            os.path.join(project_root, "Assets"), log=log)
        report, saved = importer.import_level(
            manifest_path=options["manifest_path"],
            source_assets_root=os.path.join(export_root, "Assets"),
            project_assets_root=os.path.join(project_root, "Assets"),
            prefab_path=prefab_path,
            level_name=scratch_level,
            backend=options["backend"],
            reimport=options["reimport"],
            log=log)
    except Exception as exc:
        progress.close()
        import traceback
        traceback.print_exc()
        QtWidgets.QMessageBox.critical(
            None, "Import failed", "%s: %s" % (type(exc).__name__, exc))
        return None
    progress.close()

    summary = make_summary_dialog(report, saved, options["reimport"])
    summary.exec_()
    return report

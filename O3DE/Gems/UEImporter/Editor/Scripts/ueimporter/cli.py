"""
cli.py — the headless import entry point (plan M10, batch/CI mode).

The dialog and this file are the two ways in, and they share everything below
the surface: both call `importer.stage_only` then `importer.import_level`. What
CI needs that a dialog does not is an exit code and a written report.

Run (inside the editor, which is where the entity API lives):

    Editor.exe --project-path=<project> -BatchMode -autotest_mode \\
        --runpython <this file> \\
        --runpythonargs "--manifest=<...>/manifest.json --prefab=MyLevel \\
                         --backend=jolt --report=<...>/import_report.txt"

Arguments are `--key=value` because the editor tokenizes `--runpythonargs` on
spaces: a value with a space in it cannot survive, so paths must not contain
one (the same constraint `run_o3de_python.bat` documents).

Exit code is the contract (plan constraint 10): 0 only if the import
completed and the report holds no ERROR-severity records.
"""

import os
import sys
import traceback

DEFAULTS = {
    "manifest": "",
    "prefab": "",
    "backend": "",
    "report": "",
    "level": "DefaultLevel",
    "reimport": "1",
    "timeout": "180",
}


def parse_args(argv):
    options = dict(DEFAULTS)
    for token in argv:
        if not token.startswith("--"):
            continue
        key, _sep, value = token[2:].partition("=")
        if key in options:
            options[key] = value
    return options


def run(options, log=print):
    from . import importer
    from .report import ERROR

    manifest_path = options["manifest"]
    if not manifest_path or not os.path.isfile(manifest_path):
        # ValueError, not SystemExit: SystemExit derives from BaseException,
        # so `except Exception` in main() would let it past and the editor
        # would end on an unhandled exception instead of a reported failure.
        raise ValueError("--manifest is required and must exist: %r" % manifest_path)

    import azlmbr.legacy.general as general
    project_root = general.get_game_folder().rstrip("/\\")
    export_root = os.path.dirname(os.path.abspath(manifest_path))

    name = options["prefab"]
    if not name:
        # The level name inside the manifest, so a pipeline run needs one
        # fewer argument to get right.
        from . import manifest_io
        document = manifest_io.load(manifest_path)
        name = (document.get("level") or {}).get("name") or "ImportedLevel"
    prefab_path = "%s/Prefabs/%s.prefab" % (project_root, name)

    log("manifest: " + manifest_path)
    log("prefab:   " + prefab_path)
    log("backend:  " + (options["backend"] or "(detect)"))

    importer.stage_only(manifest_path,
                        os.path.join(export_root, "Assets"),
                        os.path.join(project_root, "Assets"), log=log)

    report, saved = importer.import_level(
        manifest_path=manifest_path,
        source_assets_root=os.path.join(export_root, "Assets"),
        project_assets_root=os.path.join(project_root, "Assets"),
        prefab_path=prefab_path,
        level_name=options["level"],
        asset_timeout=float(options["timeout"]),
        backend=(options["backend"] or None),
        reimport=options["reimport"] not in ("0", "false", "no"),
        log=log)

    report_path = options["report"]
    if report_path:
        directory = os.path.dirname(os.path.abspath(report_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(report_path, "w") as handle:
            handle.write(report.to_text("UE import - " + os.path.basename(saved)))
        report.write(os.path.splitext(report_path)[0] + ".json")
        log("wrote " + report_path)

    errors = [r for r in report.records() if r["severity"] == ERROR]
    log("")
    log("entities=%d  materials=%d  physics=%d  warnings=%d  errors=%d"
        % (report.counters.get("entities_created", 0),
           report.counters.get("materials_assigned", 0),
           report.counters.get("physics_bodies", 0),
           len(report.records()), len(errors)))
    return report, saved, errors


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    options = parse_args(argv)
    try:
        _report, _saved, errors = run(options)
    except Exception:
        print("IMPORT FAILED\n" + traceback.format_exc())
        return 1
    return 1 if errors else 0

# No `if __name__ == "__main__"` block here on purpose: this module uses
# relative imports, so running it directly as the editor's `--runpython`
# target fails before the first line of it executes. `Tools/o3de_import.py`
# is the entry point, and it exists for exactly that reason.

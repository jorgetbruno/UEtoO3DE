"""
ue_export.py — headless export driver (plan M10, batch/CI mode).

The UE half of the pipeline, driven by arguments instead of by constants so CI
can point it at any level:

    UnrealEditor.exe <project>.uproject \\
        -ExecutePythonScript="Tools/ue_export.py --map=/Game/Maps/X --out=D:/Exports/X" \\
        -unattended -nop4 -nosplash

A FULL editor, not a commandlet: the skeletal exporter walks render objects
that do not exist under -nullrhi (measured in M8 -- "Assertion failed:
MeshObject"). The editor's process exit code is meaningless once
`quit_editor` is called, so this writes a RESULT line and the .bat asserts on
that (plan constraint 10).

The export sequence itself is `ueo3de.export_api`, the same call the "Export
Level to O3DE..." menu item makes.
"""

import os
import sys
import traceback

import unreal

DEFAULT_PACKAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "UE", "UEtoO3DEFixture", "Plugins", "UEO3DEExporter", "Content", "Python")


def _write_failure(message):
    """Emit a RESULT: FAIL verdict when nothing else can be, then quit."""
    for candidate in (os.path.join(os.getcwd(), "export_result.txt"),):
        try:
            with open(candidate, "w") as handle:
                handle.write(message + "\nRESULT: FAIL\n")
        except Exception:
            pass
    print("RESULT: FAIL")
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception:
        pass


def parse_args(argv):
    """`--key=value` -> options. Unknown tokens are reported, never dropped:
    a typo'd flag that silently falls back to a default is how a run exports
    the wrong thing and still says PASS."""
    options = {"map": "", "out": "", "result": "", "package-root": ""}
    unknown = []
    for token in argv:
        if not token.startswith("--"):
            unknown.append(token)
            continue
        key, sep, value = token[2:].partition("=")
        if key not in options or not sep:
            unknown.append(token)
        else:
            options[key] = value
    if unknown:
        raise ValueError("unrecognized argument(s): %s (expected --key=value "
                         "from %s)" % (", ".join(repr(u) for u in unknown),
                                       sorted(options)))
    return options


def main():
    try:
        options = parse_args(sys.argv[1:])
    except ValueError as exc:
        # Still write a verdict: a caller that finds no result file cannot
        # tell "bad arguments" from "the editor never started".
        options = {"map": "", "out": "", "result": "", "package-root": ""}
        unreal.log_error("[ue_export] " + str(exc))
        _write_failure(str(exc))
        return 1
    package_root = options["package-root"] or DEFAULT_PACKAGE_ROOT
    if package_root not in sys.path:
        sys.path.insert(0, package_root)

    lines = []

    def log(message):
        lines.append(str(message))
        unreal.log("[ue_export] " + str(message))

    status = "PASS"
    try:
        if not options["map"] or not options["out"]:
            # ValueError, not SystemExit: SystemExit derives from
            # BaseException and would sail past the `except Exception` below,
            # so a typo'd flag wrote NO result file -- leaving an earlier
            # run's `RESULT: PASS` on disk for any caller that does not delete
            # it first, and never calling quit_editor.
            raise ValueError("--map and --out are both required (got map=%r "
                             "out=%r)" % (options["map"], options["out"]))
        from ueo3de import export_api

        log("exporting %s -> %s" % (options["map"], options["out"]))
        result = export_api.export_level(
            options["map"], options["out"], log=log,
            progress=lambda index, total, label:
                log("  [%d/%d] %s" % (index + 1, total, label)))
        log("")
        log(export_api.summary_text(result))
    except Exception:
        log("EXPORT FAILED")
        log(traceback.format_exc())
        unreal.log_error("[ue_export] " + traceback.format_exc())
        status = "FAIL"

    lines.append("RESULT: " + status)
    result_path = options["result"] or os.path.join(
        options["out"] or ".", "export_result.txt")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(result_path)), exist_ok=True)
        with open(result_path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except Exception:
        unreal.log_error("[ue_export] could not write " + str(result_path))

    print("RESULT: " + status)
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception:
        pass
    return 0 if status == "PASS" else 1


raise SystemExit(main())

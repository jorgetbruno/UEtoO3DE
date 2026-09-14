"""
verify_export.py — run the export's bounds check after the editor has exited.

Usage: python Tests/ue/verify_export.py <export_records.json> <assets root> <result file>

Appends the check's lines and the final `RESULT: PASS|FAIL` to the result file
the editor stages wrote (which end with `EDITOR: PASS`). Exit code is the
verdict. See Tests/lib/export_verify.py for why this left the editor.
"""

import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_ROOT = os.path.join(REPO_ROOT, "Tests", "lib")
if LIB_ROOT not in sys.path:
    sys.path.insert(0, LIB_ROOT)

import export_verify  # noqa: E402


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    records_path, assets_root, result_path = argv[1], argv[2], argv[3]
    lines = ["", "== FBX intermediate check: bake and export negations cancel =="]
    started = time.time()
    try:
        with open(records_path, "r") as handle:
            records = json.load(handle)
        checked, failures = export_verify.verify_records(records, assets_root)
    except Exception as error:
        checked, failures = 0, ["the check itself failed: %r" % (error,)]
    elapsed = time.time() - started
    if failures:
        lines.append("  %d of %d files FAILED (%.1fs):" % (len(failures), checked, elapsed))
        lines.extend("  " + message for message in failures[:20])
        if len(failures) > 20:
            lines.append("  ... and %d more" % (len(failures) - 20))
    elif checked == 0:
        failures = ["no records to check: an export that checks nothing proves nothing"]
        lines.append("  " + failures[0])
    else:
        lines.append("  ok: all %d files match their expected intermediate bounds "
                     "(mirror-X for normal entries, verbatim for #mx variants) in %.1fs"
                     % (checked, elapsed))
    lines.append("")
    lines.append("RESULT: " + ("FAIL" if failures else "PASS"))
    with open(result_path, "a") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

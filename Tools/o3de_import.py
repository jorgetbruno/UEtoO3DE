"""
o3de_import.py — headless import driver (plan M10, batch/CI mode).

A thin shim, and deliberately so: `ueimporter.cli` lives inside the package
and uses relative imports, which break the moment the editor runs a file as
`__main__`. This sits outside the package, puts the gem's Scripts folder on
sys.path, and hands over.

    Editor.exe --project-path=<project> -BatchMode -autotest_mode \\
        --runpython Tools/o3de_import.py \\
        --runpythonargs "--manifest=<...>/manifest.json --prefab=MyLevel"

See `Tools/run_pipeline.bat` for the whole export-to-import run.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_GEM_SCRIPTS = os.path.join(os.path.dirname(_HERE), "O3DE", "Gems", "UEImporter",
                            "Editor", "Scripts")
if _GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, _GEM_SCRIPTS)

from ueimporter import cli  # noqa: E402

_code = cli.main(sys.argv[1:])
print("RESULT: " + ("PASS" if _code == 0 else "FAIL"))

try:
    import azlmbr.legacy.general as _general
    if _code == 0:
        _general.exit_no_prompt()
except Exception:
    pass
if _code:
    os._exit(_code)

"""
bootstrap.py — the gem's editor entry point (plan M10).

EditorPythonBindings executes `<gem>/Editor/Scripts/bootstrap.py` for every
enabled gem during editor startup, which is the only hook a gem with no C++ at
all gets. The plan flagged this as verify-early; it works, but the gem has to
be *registered and enabled* first (`o3de register -gp <gem> -espp <project>`
then `o3de enable-gem -gn UEImporter -pp <project>`) -- until then this file is
simply never read, which looks exactly like a bug in the file.

This runs inside every editor launch in this repo, including the ten headless
acceptance suites. So it is written to be boring:

  * nothing heavy at import time -- no PySide2, no manifest parsing, no
    adapters; just the menu registration, whose payload is imported lazily
    when the user actually clicks;
  * it cannot raise. An exception here happens before any test's own code and
    would present as an unrelated, unexplained editor failure.

The env marker is a test hook: `Tests/m10/m10_menu.py` asserts on it to tell
"the bootstrap ran and installed the menu" from "the bootstrap never ran",
which are otherwise the same observation from outside.
"""

import os
import sys
import traceback

os.environ["UEO3DE_BOOTSTRAP_RAN"] = "1"

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from ueimporter import menu

    # menu.install() owns UEO3DE_BOOTSTRAP_MENU, because at this point in
    # startup the Action Manager is not up yet: install() falls back to the
    # registration hooks and only finishes several milliseconds later. Writing
    # the marker from here recorded "hook" permanently, on runs where the menu
    # entry did in fact appear.
    menu.install()
except Exception:
    os.environ["UEO3DE_BOOTSTRAP_MENU"] = "exception"
    os.environ["UEO3DE_BOOTSTRAP_ERROR"] = traceback.format_exc()
    print("[UEImporter] bootstrap failed (editor startup continues):\n"
          + traceback.format_exc())

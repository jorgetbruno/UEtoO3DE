"""
test_gem_binaries_fresh.py — the gem test DLLs must be newer than the gem.

Pure: no editor. Run: python Tests/m3/test_gem_binaries_fresh.py <bin-dir>

WHY THIS EXISTS. M3 step 4 runs AzTestRunner over the JoltPhysics gem's two
test DLLs and asserts a zero exit code. A passing run therefore means "the
binaries on disk passed" — which is NOT the same as "the gem passes", and the
difference is invisible: on 2026-07-30 `JoltPhysics.Tests.dll` was three days
old because the gem's test sources had stopped compiling, and M3 reported PASS
for three days while the new convex-decomposition, mesh-asset, per-face
material and primitive-fit tests never ran once. A green suite testing a stale
binary is worse than a red one, because nobody looks.

So: every test DLL must be newer than the newest source file in the gem. That
is a coarse check and deliberately so — it cannot tell a correct build from an
incorrect one, only a CURRENT one from a STALE one, which is the failure that
actually happened.

The gem's source directory comes from the O3DE manifest's registered external
subdirectories (`~/.o3de/o3de_manifest.json`), so no new machine-specific path
lands in Tests/paths.config. `UEO3DE_GEM_SOURCE` overrides it. If neither
resolves, this FAILS rather than skipping: a freshness guard that silently
does nothing is the exact thing it was written to prevent.
"""

import json
import os
import sys

BIN_DIR = sys.argv[1] if len(sys.argv) > 1 else ""
DLL_NAMES = sys.argv[2:] or ["JoltPhysics.Tests.dll", "JoltPhysics.Editor.Tests.dll"]
GEM_NAME = "JoltPhysics"
SOURCE_SUFFIXES = (".cpp", ".h", ".hpp", ".inl", ".cmake", ".txt")

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def gem_source_dir():
    override = os.environ.get("UEO3DE_GEM_SOURCE", "").strip()
    if override:
        return override, "UEO3DE_GEM_SOURCE"
    manifest = os.path.expanduser(os.path.join("~", ".o3de", "o3de_manifest.json"))
    if not os.path.isfile(manifest):
        return None, "no %s" % manifest
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            entries = json.load(handle).get("external_subdirectories") or []
    except (ValueError, OSError) as error:
        return None, "%s is unreadable (%s)" % (manifest, error)
    for entry in entries:
        if os.path.basename(str(entry).rstrip("/\\")) == GEM_NAME:
            return str(entry), "o3de_manifest.json"
    return None, "%s registers no %s" % (manifest, GEM_NAME)


def newest_source(root):
    """(path, mtime) of the newest source file under `root`, or (None, 0)."""
    newest_path, newest_time = None, 0.0
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in ("build", ".git", "__pycache__")]
        for name in files:
            if not name.endswith(SOURCE_SUFFIXES):
                continue
            path = os.path.join(directory, name)
            try:
                stamp = os.path.getmtime(path)
            except OSError:
                continue
            if stamp > newest_time:
                newest_path, newest_time = path, stamp
    return newest_path, newest_time


source_dir, how = gem_source_dir()
if not check(source_dir and os.path.isdir(source_dir),
             "cannot locate the %s gem sources (%s); set UEO3DE_GEM_SOURCE. "
             "Refusing to pass: this guard exists because a stale binary looks "
             "exactly like a passing one" % (GEM_NAME, how)):
    print("")
    print("RESULT: FAIL (%d)" % len(failures))
    sys.exit(1)

code_dir = os.path.join(source_dir, "Code")
code_dir = code_dir if os.path.isdir(code_dir) else source_dir
source_path, source_time = newest_source(code_dir)
check(source_path is not None,
      "no source files under %s -- the gem path resolved to something that is "
      "not a gem" % code_dir)
print("gem sources: %s (via %s)" % (code_dir, how))
if source_path:
    print("  newest source: %s" % os.path.basename(source_path))

if not check(BIN_DIR and os.path.isdir(BIN_DIR),
             "binary directory %r does not exist" % BIN_DIR):
    print("")
    print("RESULT: FAIL (%d)" % len(failures))
    sys.exit(1)

for dll in DLL_NAMES:
    path = os.path.join(BIN_DIR, dll)
    if not check(os.path.isfile(path), "%s is missing from %s" % (dll, BIN_DIR)):
        continue
    stamp = os.path.getmtime(path)
    age_hours = (source_time - stamp) / 3600.0
    if check(stamp >= source_time,
             "%s is OLDER than the gem sources by %.1f h (%s changed after it "
             "was built). AzTestRunner would pass this binary while the gem's "
             "current tests never run -- rebuild the test targets"
             % (dll, age_hours, os.path.basename(source_path or "?"))):
        print("  %-32s current" % dll)

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

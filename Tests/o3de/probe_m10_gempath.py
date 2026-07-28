"""
probe_m10_gempath.py -- why did the gem bootstrap not run?

The gem is registered (`o3de register -gp ... -espp <project>`) and enabled
(`o3de enable-gem`), yet `UEO3DE_BOOTSTRAP_RAN` is unset, so EditorPythonBindings
never read `Editor/Scripts/bootstrap.py`. Three candidate causes, and they need
different fixes, so guessing is expensive:

  A. the editor never learned the gem's PATH -- likely, because these suites
     run the prebuilt SDK editor (C:\\O3DE\\26.05\\bin) whose gem list comes
     from cmake_dependencies.editor.setreg, generated at ENGINE build time and
     therefore ignorant of a gem registered afterwards;
  B. it learned the path but does not scan Editor/Scripts for gems with no
     compiled module;
  C. bootstrap.py ran and threw before setting the marker (excluded already --
     it sets the marker on line 1 and catches everything).

So: dump what the editor actually believes. Which gems it knows, whether
UEImporter is among them, and which script directories ended up on sys.path.
Whatever the answer, it decides where the bootstrap has to live.
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m10_gempath_result.txt')

lines = []


def log(msg=""):
    lines.append(str(msg))
    print(msg)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
        with open(RESULT_PATH, 'w') as handle:
            handle.write('\n'.join(lines))
    except Exception:
        pass


def section(title):
    log("")
    log("=== %s ===" % title)


def main():
    section("1. sys.path -- which gem script dirs did the editor add?")
    for entry in sys.path:
        marker = ""
        if 'Editor' in entry and 'Scripts' in entry:
            marker = "   <-- gem script dir"
        if 'UEImporter' in entry:
            marker = "   <-- OURS"
        log("  %s%s" % (entry, marker))

    section("2. azlmbr.paths")
    try:
        import azlmbr.paths as paths
        for name in sorted(n for n in dir(paths) if not n.startswith('_')):
            try:
                log("  %-16s %r" % (name, getattr(paths, name)))
            except Exception as exc:
                log("  %-16s <%s>" % (name, exc))
    except Exception as exc:
        log("  azlmbr.paths unavailable: %s" % exc)

    section("3. does the editor know a path for UEImporter?")
    # The settings registry is the mechanism: /O3DE/Gems/<name>/SourcePaths.
    try:
        import azlmbr.settingsregistry as sr
        registry = getattr(sr, 'SettingsRegistry', None)
        log("  SettingsRegistry binding: %r" % registry)
        if registry is not None:
            log("  methods: %s" % [n for n in dir(registry) if not n.startswith('_')])
            for key in ("/O3DE/Gems/UEImporter/Path",
                        "/O3DE/Gems/UEImporter/SourcePaths/0",
                        "/O3DE/Gems/QtForPython/Path",
                        "/Amazon/AzCore/Bootstrap/project_path"):
                for method in ('GetString', 'get_string'):
                    fn = getattr(registry, method, None)
                    if fn is None:
                        continue
                    try:
                        log("  %-42s -> %r" % (key, fn(key)))
                    except Exception as exc:
                        log("  %-42s raised %s" % (key, str(exc)[:80]))
                    break
    except Exception as exc:
        log("  settingsregistry unavailable: %s" % exc)

    section("4. which gems did the editor load modules for?")
    try:
        import azlmbr.bus as bus
        import azlmbr.editor as editor
        names = editor.EditorComponentAPIBus(bus.Broadcast,
                                             'BuildComponentTypeNameList')
        log("  component type names known: %d" % (len(names) if names else 0))
    except Exception as exc:
        log("  could not enumerate: %s" % str(exc)[:160])

    section("5. the project's own gem -- is IT scanned?")
    # The project is itself a gem and is unquestionably loaded. If its
    # Editor/Scripts is scanned, a shim there is a viable fallback route.
    import azlmbr.legacy.general as general
    project = general.get_game_folder()
    log("  project folder: %r" % project)
    for candidate in ("Gem/Editor/Scripts", "Editor/Scripts"):
        full = os.path.join(project, candidate.replace("/", os.sep))
        log("  %-24s exists=%s  on sys.path=%s"
            % (candidate, os.path.isdir(full),
               any(os.path.normcase(os.path.normpath(p))
                   == os.path.normcase(os.path.normpath(full)) for p in sys.path)))

    section("6. engine gems that DO ship a bootstrap.py -- are they on sys.path?")
    for gem in ("QtForPython", "PythonAssetBuilder"):
        hits = [p for p in sys.path if gem in p]
        log("  %-20s %s" % (gem, hits or '(not on sys.path)'))


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')

import azlmbr.legacy.general as _general
_general.exit_no_prompt()

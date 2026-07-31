"""probe_bounds_api.py -- which bus reports a mesh's bounds in THIS build?

`BoundsRequestBus` is None in azlmbr.components, azlmbr.entity and
azlmbr.framework here, so probe_gltf_basis.py cannot read local bounds the way
lane_b_measure.py once did. Rather than guess a fourth module, scan.

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_bounds_api.py <result> <project>
"""

import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
RESULT_PATH = (sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip()
               and not sys.argv[1].startswith('-')
               else os.path.join(SCRIPT_DIR, 'results', 'probe_bounds_api_result.txt'))

lines = []


def log(message=""):
    lines.append(str(message))
    print(message)


def main():
    import azlmbr

    log("=== every azlmbr.* attribute whose name mentions Bounds or Mesh ===")
    for mod_name in sorted(dir(azlmbr)):
        if mod_name.startswith('_'):
            continue
        try:
            mod = getattr(azlmbr, mod_name)
            names = sorted(dir(mod))
        except Exception:  # noqa: BLE001
            continue
        for attr in names:
            if 'Bounds' in attr or ('Mesh' in attr and 'Bus' in attr):
                try:
                    value = getattr(mod, attr)
                except Exception:  # noqa: BLE001
                    value = '<unreadable>'
                log("  azlmbr.%-22s %-38s %s"
                    % (mod_name, attr, 'None' if value is None else type(value).__name__))

    log("")
    log("=== submodules that must be imported before they appear ===")
    for candidate in ('azlmbr.render', 'azlmbr.atom', 'azlmbr.bounds',
                      'azlmbr.components', 'azlmbr.entity', 'azlmbr.framework',
                      'azlmbr.editor', 'azlmbr.debug'):
        try:
            module = __import__(candidate, fromlist=['*'])
        except Exception as error:  # noqa: BLE001
            log("  %-22s import failed: %s" % (candidate, error))
            continue
        hits = [a for a in sorted(dir(module))
                if 'Bounds' in a or ('Mesh' in a and 'Bus' in a)]
        log("  %-22s %s" % (candidate, hits or '(no Bounds/Mesh bus names)'))


try:
    main()
except Exception:
    log('EXCEPTION: ' + traceback.format_exc())

log('')
log('RESULT: PASS')
os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
with open(RESULT_PATH, 'w') as handle:
    handle.write('\n'.join(lines))

import azlmbr.legacy.general as _general
_general.exit_no_prompt()

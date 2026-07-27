"""
test_backend_detection.py — the never-guess rules, unit-tested offline (M3).

The detection core takes its I/O injected, so the ambiguity rules run in a
plain interpreter. The case that matters most is both-resolve-without-explicit
RAISING: available != active, and picking a backend whose system component is
not simulating produces a level with no physics at all (constraint 5).

Run:  python Tests/m3/test_backend_detection.py
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts"))

from ueimporter.adapters import detection  # noqa: E402
from ueimporter.adapters.detection import (  # noqa: E402
    BackendAmbiguityError, BackendDetectionError, detect,
)

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def resolver_for(*backends):
    """A resolver that answers True only for the given backends' names."""
    known = set()
    for backend in backends:
        known.update(detection.PROBE_NAMES[backend])
    return lambda names: [name in known for name in names]


def expect_raise(exception_type, label, **kwargs):
    try:
        result = detect(**kwargs)
    except exception_type:
        return True
    except Exception as exc:
        check(False, "%s: raised %r, expected %s"
              % (label, exc, exception_type.__name__))
        return False
    check(False, "%s: returned %r, expected %s"
          % (label, result, exception_type.__name__))
    return False


def main():
    # 1. Jolt-only project resolves to jolt (the fixture project's shape).
    result = detect(resolver_for("jolt"))
    check(result["backend"] == "jolt", "jolt-only should detect jolt")
    check(result["source"] == "type_ids", "jolt-only source should be type_ids")

    # 2. PhysX-only resolves to physx (M3b's project, same logic today).
    result = detect(resolver_for("physx"))
    check(result["backend"] == "physx", "physx-only should detect physx")

    # 3. THE case: both resolve, no explicit choice -> raise, never guess.
    expect_raise(BackendAmbiguityError, "both-resolve without explicit",
                 resolver=resolver_for("jolt", "physx"))

    # 4. Both resolve WITH an explicit choice -> honoured.
    result = detect(resolver_for("jolt", "physx"), explicit="physx")
    check(result["backend"] == "physx", "explicit choice must win on ambiguity")
    check(result["source"] == "explicit", "source must say explicit")

    # 5. A settings hint must NOT break the ambiguity (it is a hint for the
    #    M10 dialog's default, not a decider -- stale registry state is real).
    expect_raise(BackendAmbiguityError, "hint does not resolve ambiguity",
                 resolver=resolver_for("jolt", "physx"),
                 settings_reader=lambda: "JoltPhysics")

    # 6. Explicit backend whose components do not resolve -> error, not a
    #    silent no-physics import.
    expect_raise(BackendDetectionError, "explicit but unresolvable",
                 resolver=resolver_for("jolt"), explicit="physx")

    # 7. Nothing resolves -> error.
    expect_raise(BackendDetectionError, "no backend at all",
                 resolver=resolver_for())

    # 8. A stale hint disagreeing with the only resolvable backend: type IDs
    #    win (they test the actual capability) and the override is recorded.
    result = detect(resolver_for("jolt"), settings_reader=lambda: "PhysX")
    check(result["backend"] == "jolt", "type IDs beat a stale settings hint")
    check("ignored" in result["source"], "the ignored hint must be visible")

    # 9. A settings reader that throws must not take detection down.
    def broken_reader():
        raise RuntimeError("registry unavailable")
    result = detect(resolver_for("jolt"), settings_reader=broken_reader)
    check(result["backend"] == "jolt", "a broken settings reader is non-fatal")

    # 10. Unknown explicit name -> error naming the valid choices.
    expect_raise(BackendDetectionError, "unknown explicit backend",
                 resolver=resolver_for("jolt"), explicit="havok")

    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS (10 cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

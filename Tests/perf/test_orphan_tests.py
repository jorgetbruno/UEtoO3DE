"""test_orphan_tests.py -- every test must be wired to a runner, or it rots.

Pure: no editor. Run: python Tests/perf/test_orphan_tests.py

THE ROT THIS PINS HAS RECURRED THREE TIMES, and the third instance is the one
that makes it worth a meta-test: `m6_level_renders.py` -- the check written
after a level imported PURE WHITE while every structural assertion passed --
sat referenced by zero runners, guarding nothing, until an external review
noticed. `test_chunk.py` and `test_png.py` likewise. A test that no runner
executes is worse than no test: it radiates false confidence exactly
proportional to how important it looks.

The rule: every `Tests/**/test_*.py` and every `Tests/m*/m*_*.py` acceptance
script must be named by at least one `.bat` under Tests/. Probes
(`probe_*.py`), libraries (`Tests/lib`), and one-off measurement scripts are
exempt -- they are instruments, not assertions, and wiring them would turn
every exploratory measurement into CI load.

When this fails: either wire the test into the suite's runner, or -- if it is
genuinely a probe -- rename it `probe_*` so the exemption is explicit instead
of accidental.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(REPO_ROOT, "Tests")

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# Every .bat under Tests/, concatenated: "is this filename mentioned anywhere"
# is a substring question, and filenames are unique enough for that.
bat_text = ""
bat_count = 0
for dirpath, _dirs, files in os.walk(TESTS):
    for name in files:
        if name.lower().endswith(".bat"):
            bat_count += 1
            with open(os.path.join(dirpath, name), "r",
                      encoding="utf-8", errors="replace") as handle:
                bat_text += handle.read().lower()

check(bat_count >= 10,
      "only %d .bat files found under Tests -- the scan itself is broken"
      % bat_count)

orphans = []
scanned = 0
for dirpath, dirs, files in os.walk(TESTS):
    dirs[:] = [d for d in dirs if d not in ("results", "__pycache__", "lib",
                                            "data")]
    for name in files:
        if not name.endswith(".py"):
            continue
        if not name.startswith("test_"):
            continue
        scanned += 1
        if name.lower() not in bat_text:
            orphans.append(os.path.relpath(os.path.join(dirpath, name),
                                           REPO_ROOT))

check(scanned >= 15,
      "only %d test files scanned -- the walk missed the suites" % scanned)
for orphan in orphans:
    check(False,
          "%s is referenced by NO runner .bat: it executes never, and every "
          "assertion in it is decoration. Wire it into its suite's runner, or "
          "rename it probe_* if it is genuinely an instrument." % orphan)

# The named recidivist gets its own assertion, because it is the single most
# expensive check in the repo to lose: it is the only one that can see a
# level that imports broken-LOOKING while structurally perfect.
check("m6_level_renders.py" in bat_text,
      "m6_level_renders.py (the white-level guard) is not wired into any "
      "runner -- this exact omission is what let a pure-white import pass "
      "the full suite")

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

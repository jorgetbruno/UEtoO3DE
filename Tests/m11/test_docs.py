"""
test_docs.py — the documentation contract, enforced (plan M11).

M11 asks for "`MAPPING.md` ... including every `warnings[]` code with its
meaning and severity". A prose promise like that decays silently: a milestone
adds four codes, the table keeps the old thirteen, and nothing anywhere
notices. This repo has already paid for that class of bug twice in code; there
is no reason to accept it in the docs, which are the only artefact a user has
when the tool tells them `PHYS_SHAPE_APPROXIMATED` and they need to know
whether their level is broken.

So the tables in MAPPING.md are checked against the two catalogues in both
directions:

  * every code in `ueo3de.warnings.CODES` and `ueimporter.report.CODES`
    appears in MAPPING.md, with the SAME severity;
  * every code documented in MAPPING.md still exists in a catalogue -- a
    renamed or deleted code leaving a phantom row is just as misleading;
  * the two catalogues do not disagree with each other about a shared code.

Pure and fast. Run: python Tests/m11/test_docs.py
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))
sys.path.insert(0, os.path.join(REPO_ROOT, "UE", "UEtoO3DEFixture", "Plugins",
                                "UEO3DEExporter", "Content", "Python"))

from ueimporter import report as importer_report  # noqa: E402
from ueo3de import warnings as exporter_warnings  # noqa: E402

MAPPING = os.path.join(REPO_ROOT, "MAPPING.md")
DIVERGENCES = os.path.join(REPO_ROOT, "DIVERGENCES.md")
LANE_B = os.path.join(REPO_ROOT, "LANE_B.md")

# `| `CODE` | severity | meaning |`
ROW = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|\s*([a-z]+)\s*\|", re.M)

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def documented_codes():
    return {code: severity for code, severity in ROW.findall(read(MAPPING))}


def test_every_code_is_documented():
    documented = documented_codes()
    for label, catalogue in (("exporter", exporter_warnings.CODES),
                             ("importer", importer_report.CODES)):
        missing = sorted(code for code in catalogue if code not in documented)
        check(not missing,
              "%d %s warning code(s) missing from MAPPING.md: %s"
              % (len(missing), label, ", ".join(missing)))


def test_documented_severities_match_the_catalogue():
    documented = documented_codes()
    catalogue = {}
    catalogue.update({c: v[0] for c, v in exporter_warnings.CODES.items()})
    catalogue.update({c: v[0] for c, v in importer_report.CODES.items()})
    wrong = []
    for code, severity in sorted(documented.items()):
        actual = catalogue.get(code)
        if actual is not None and actual != severity:
            wrong.append("%s documented as %r, catalogue says %r"
                         % (code, severity, actual))
    check(not wrong,
          "MAPPING.md disagrees with the catalogue on severity: " + "; ".join(wrong))


def test_no_phantom_codes():
    """A row for a code that no longer exists is worse than no row: it tells
    the reader to look for something the tool will never emit."""
    documented = documented_codes()
    known = set(exporter_warnings.CODES) | set(importer_report.CODES)
    phantom = sorted(code for code in documented if code not in known)
    check(not phantom,
          "%d code(s) documented in MAPPING.md exist in no catalogue: %s"
          % (len(phantom), ", ".join(phantom)))


def test_catalogues_agree_on_shared_codes():
    shared = set(exporter_warnings.CODES) & set(importer_report.CODES)
    disagree = [code for code in sorted(shared)
                if exporter_warnings.CODES[code][0] != importer_report.CODES[code][0]]
    check(not disagree,
          "the two catalogues give different severities for: %s"
          % ", ".join(disagree))


def test_the_three_documents_exist_and_are_not_stubs():
    for path in (MAPPING, DIVERGENCES, LANE_B):
        check(os.path.isfile(path), "missing " + os.path.basename(path))
        if os.path.isfile(path):
            check(len(read(path).splitlines()) > 40,
                  "%s is a stub (%d lines)"
                  % (os.path.basename(path), len(read(path).splitlines())))


def test_divergences_covers_both_backends():
    """The plan asks for DIVERGENCES 'per-backend where they differ'. The
    PhysX column carried (M3b) IOUs for four milestones; this stops that
    happening again silently."""
    text = read(DIVERGENCES)
    for needle in ("Jolt", "PhysX"):
        check(needle in text, "DIVERGENCES.md never mentions %s" % needle)
    check("(M3b)" not in text,
          "DIVERGENCES.md still carries unfilled '(M3b)' placeholders -- those "
          "were IOUs for measurements that have since been taken")


def test_the_canary_can_fail():
    """The row regex is the single point of failure for every check above: if
    it matched nothing, all of them would pass vacuously."""
    documented = documented_codes()
    check(len(documented) >= 40,
          "only %d codes parsed out of MAPPING.md -- the table format probably "
          "changed and every check above is passing on an empty set"
          % len(documented))
    check("LEVEL_WORLD_PARTITION" in documented,
          "a known code did not parse out of MAPPING.md; the row regex is wrong")


def main():
    for test in (test_every_code_is_documented,
                 test_documented_severities_match_the_catalogue,
                 test_no_phantom_codes,
                 test_catalogues_agree_on_shared_codes,
                 test_the_three_documents_exist_and_are_not_stubs,
                 test_divergences_covers_both_backends,
                 test_the_canary_can_fail):
        print("- " + test.__name__)
        test()
    total = len(set(exporter_warnings.CODES) | set(importer_report.CODES))
    if failures:
        print("RESULT: FAIL (%d)" % len(failures))
        return 1
    print("RESULT: PASS (M11 docs: %d warning codes documented)" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

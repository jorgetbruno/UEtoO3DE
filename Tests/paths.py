"""
paths.py — where this machine keeps the engines and projects (Python half).

The same `Tests/paths.config` that `paths.cmd` loads for the .bat runners, so
there is ONE place to edit and no chance of the two halves drifting apart.

    from paths import PATHS
    PATHS["O3DE_PROJECT_JOLT"]
    PATHS.repo_root                 # derived from this file, never configured

Precedence matches paths.cmd: an environment variable of the same name wins
over the file, so CI overrides one value without editing anything.

`repo_root` is deliberately NOT a config key. It is derivable from this file's
own location, and every derivable value that gets configured instead is a value
that can be configured WRONG -- which is most of what the 40 hardcoded
`REPO_ROOT = "D:/Gamedev/UEtoO3DE"` lines were.
"""

import os

CONFIG_NAME = "paths.config"
TEMPLATE_NAME = "paths.config.template"

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

REQUIRED = ("UE_EDITOR", "UE_PROJECT", "O3DE_BIN", "O3DE_PROJECT_JOLT")


class MissingConfig(RuntimeError):
    pass


def _parse(path):
    values = {}
    with open(path, "r") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep:
                continue
            values[key.strip()] = value.strip()
    return values


class _Paths(dict):
    """dict of config values, with `.repo_root` and a helpful KeyError."""

    repo_root = REPO_ROOT

    def __missing__(self, key):
        raise MissingConfig(
            "%r is not set. Add it to %s (see %s), or set it as an "
            "environment variable."
            % (key, os.path.join(_HERE, CONFIG_NAME),
               os.path.join(_HERE, TEMPLATE_NAME)))

    def require(self, *keys):
        """Return the values, raising one clear error naming everything absent
        rather than failing on whichever happened to be read first."""
        absent = [key for key in keys if not self.get(key)]
        if absent:
            raise MissingConfig(
                "missing config value(s): %s. Add them to %s (see %s) or set "
                "them in the environment."
                % (", ".join(absent), os.path.join(_HERE, CONFIG_NAME),
                   os.path.join(_HERE, TEMPLATE_NAME)))
        return [self[key] for key in keys]

    def project(self, backend="jolt"):
        key = "O3DE_PROJECT_" + str(backend).upper()
        return self[key]

    def o3de(self, executable):
        """`paths.o3de("Editor.exe")` -> full path."""
        return os.path.join(self["O3DE_BIN"], executable)


def load(strict=False):
    """Read the config. `strict=True` raises when required keys are absent."""
    values = _Paths()
    config_path = os.path.join(_HERE, CONFIG_NAME)
    if os.path.isfile(config_path):
        values.update(_parse(config_path))
    elif strict:
        raise MissingConfig(
            "missing %s -- copy %s and edit it to point at your UE install, "
            "O3DE install and test projects."
            % (config_path, os.path.join(_HERE, TEMPLATE_NAME)))
    # The environment wins, matching paths.cmd.
    for key in list(values) + list(REQUIRED):
        if os.environ.get(key):
            values[key] = os.environ[key]
    if strict:
        values.require(*REQUIRED)
    return values


PATHS = load()


if __name__ == "__main__":
    import sys
    try:
        resolved = load(strict=True)
    except MissingConfig as exc:
        print("FAIL: %s" % exc)
        raise SystemExit(1)
    print("repo_root = %s" % resolved.repo_root)
    problems = 0
    for key in sorted(resolved):
        value = resolved[key]
        # UE_PROJECT and the executables are files; the rest are folders.
        exists = os.path.exists(value)
        print("  %-22s %-70s %s" % (key, value, "ok" if exists else "MISSING"))
        if not exists:
            problems += 1
    print("RESULT: " + ("PASS" if not problems
                        else "FAIL (%d path(s) do not exist)" % problems))
    sys.exit(1 if problems else 0)

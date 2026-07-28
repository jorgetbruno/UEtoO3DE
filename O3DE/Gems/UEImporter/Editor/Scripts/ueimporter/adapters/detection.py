"""
detection.py — which physics backend is this project running? (constraint 5)

Detection order, decreasing reliability, exactly as the plan specifies:

  1. **Component name -> type ID resolution.** Tests the capability actually
     needed (can the adapter's components be added?) rather than a proxy.
  2. **Settings Registry `/O3DE/Physics/DefaultBackend`.** The Jolt gem ships
     this key; the PhysX gem does not set it, so absence is only a weak
     "probably PhysX". Used as a tie-break hint and for the M10 dialog's
     default, never as the decider.
  3. Enabled-gem list -- brittle, not implemented until something needs it.

**Available != active** (plan, Known Hard Spot 4): both gems' *editor*
components can resolve while only one system component simulates, and creating
components for the inactive backend yields a level with no physics at all --
indistinguishable from a broken importer. Therefore when both resolve this
module REFUSES to guess: an explicit choice (`--backend`, or the M10 dropdown)
is required, and ambiguity without one is an error.

The core logic is pure (resolvers injected) so the ambiguity rules are
unit-testable without an editor: `Tests/m3/test_backend_detection.py`.
"""

# Editor component names per backend. These strings live HERE, inside
# adapters/, on purpose -- the seam guard greps everything outside this
# package for them. Detection needs only bodies: a project with a backend's
# rigid bodies has that backend's gem.
PROBE_NAMES = {
    "jolt": ["Jolt Rigid Body", "Jolt Static Rigid Body"],
    "physx": ["PhysX Dynamic Rigid Body", "PhysX Static Rigid Body"],
}

# Settings Registry hints (step 2). Key shipped by the Jolt gem's setreg.
DEFAULT_BACKEND_KEY = "/O3DE/Physics/DefaultBackend"
_SETREG_VALUE_TO_BACKEND = {"joltphysics": "jolt", "physx": "physx"}


class BackendDetectionError(Exception):
    pass


class BackendAmbiguityError(BackendDetectionError):
    """Both backends resolve and no explicit choice was given. Never guess."""


def settings_hint(settings_reader=None):
    """The Settings Registry's backend name, or None. Never raises.

    Exists so callers do not reach into `_SETREG_VALUE_TO_BACKEND` to do this
    themselves -- the M10 dialog did, and a private mapping with an outside
    caller is a rename away from breaking the one place a user sees it.
    """
    reader = settings_reader or editor_settings_reader
    try:
        raw = reader()
    except Exception:
        return None
    if not raw:
        return None
    return _SETREG_VALUE_TO_BACKEND.get(str(raw).lower())


def available(resolver):
    """Which backends' components resolve here. Pure; never raises.

    `detect` deliberately raises on ambiguity, which is right for an import
    that must not guess -- but the M10 dialog needs the *list* precisely in
    the ambiguous case, to offer it. Asking that question through an exception
    would mean parsing an error message for data.
    """
    return sorted(backend for backend, names in PROBE_NAMES.items()
                  if all(resolver(names)))


def detect(resolver, settings_reader=None, explicit=None):
    """Decide the backend. Pure logic; I/O is injected.

    `resolver(names) -> list[bool]`   whether each component name resolves
    `settings_reader() -> str|None`   DefaultBackend value, best effort
    `explicit`                        'jolt'/'physx' from --backend, or None

    Returns {"backend", "resolved", "settings_hint", "source"}.
    """
    resolved = {backend: all(resolver(names))
                for backend, names in PROBE_NAMES.items()}
    available = sorted(b for b, present in resolved.items() if present)

    hint = None
    if settings_reader is not None:
        try:
            raw = settings_reader()
        except Exception:
            raw = None
        if raw:
            hint = _SETREG_VALUE_TO_BACKEND.get(str(raw).lower())

    if explicit is not None:
        if explicit not in PROBE_NAMES:
            raise BackendDetectionError(
                "unknown backend %r (choose one of %s)"
                % (explicit, sorted(PROBE_NAMES)))
        if not resolved[explicit]:
            raise BackendDetectionError(
                "backend %r was requested but its components do not resolve "
                "in this project" % explicit)
        return {"backend": explicit, "resolved": resolved,
                "settings_hint": hint, "source": "explicit"}

    if len(available) == 1:
        backend = available[0]
        if hint is not None and hint != backend:
            # The registry claims one backend while only the other's
            # components resolve. Type IDs test the real capability; the hint
            # is stale registry state. Proceed, but say so.
            source = "type_ids (settings hint %r ignored)" % hint
        else:
            source = "type_ids"
        return {"backend": backend, "resolved": resolved,
                "settings_hint": hint, "source": source}

    if len(available) == 0:
        raise BackendDetectionError(
            "no physics backend resolves: neither Jolt nor PhysX editor "
            "components are available. Is a physics gem enabled?")

    raise BackendAmbiguityError(
        "both Jolt and PhysX components resolve in this project. Available "
        "is not active -- authoring for the inactive backend produces a level "
        "with no physics. Pass an explicit backend (--backend jolt|physx); "
        "the Settings Registry hint is %r." % hint)


# ---------------------------------------------------------------------------
# editor-bound I/O for the pure core
# ---------------------------------------------------------------------------

def editor_resolver(names):
    """Type-ID resolution against the live editor."""
    import azlmbr.bus as bus
    import azlmbr.editor as editor
    from azlmbr.entity import EntityType

    instance = EntityType()
    game_type = instance.Game() if callable(instance.Game) else instance.Game
    type_ids = editor.EditorComponentAPIBus(
        bus.Broadcast, 'FindComponentTypeIdsByEntityType', list(names), game_type)
    if not type_ids or len(type_ids) != len(names):
        return [False] * len(names)
    return [t is not None and not t.IsNull() for t in type_ids]


def editor_settings_reader():
    """Best-effort read of DefaultBackend from the live Settings Registry.

    The binding surface in 26.05 is thin; every route is tried and failure
    returns None -- detection never depends on this succeeding.
    """
    try:
        import azlmbr.settingsregistry as settingsregistry
        registry = getattr(settingsregistry, 'SettingsRegistry', None)
        if registry is None:
            return None
        for method in ('GetString', 'get_string'):
            fn = getattr(registry, method, None)
            if fn is None:
                continue
            try:
                value = fn(DEFAULT_BACKEND_KEY)
                if value:
                    # Some bindings return an outcome-like object.
                    if hasattr(value, 'IsSuccess'):
                        return value.GetValue() if value.IsSuccess() else None
                    return value
            except Exception:
                continue
    except Exception:
        pass
    return None


def detect_in_editor(explicit=None):
    return detect(editor_resolver, editor_settings_reader, explicit)

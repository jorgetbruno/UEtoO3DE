"""
adapters — the physics backend seam (plan M3, global constraint 5).

The ONLY package allowed to contain physics component names. Everything else
authors through `PhysicsBackendAdapter`, and `Tests/m3/test_seam_guard.py`
greps the rest of the importer (and the UE exporter) for "Jolt "/"PhysX "
literals to keep it that way.

  base       the interface + capability names
  detection  which backend is active (type IDs first; never guess on ambiguity)
  jolt       the JoltPhysics implementation (M3)
  physx      arrives in M3b

`make_adapter(name)` is the factory the importer calls after detection.
"""

from .base import AdapterError, PhysicsBackendAdapter  # noqa: F401
from .detection import (  # noqa: F401
    BackendAmbiguityError, BackendDetectionError, detect, detect_in_editor,
)


def make_adapter(backend_name):
    if backend_name == "jolt":
        from .jolt import JoltBackendAdapter
        return JoltBackendAdapter()
    if backend_name == "physx":
        raise AdapterError("the PhysX adapter ships in M3b")
    raise AdapterError("unknown backend %r" % (backend_name,))

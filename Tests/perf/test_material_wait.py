"""test_material_wait.py -- the model-rows stall budget and dedup-suffix slots.

Pure: no editor. Run: python Tests/perf/test_material_wait.py

Pins the two halves of the RetroCars near-distance material regression
("only the far away works"):

  * `wait_for_model_rows` treated MODEL_READY_WAIT_FRAMES as a TOTAL budget.
    Tuned when every mesh was one flattened azmodel, it expired mid-stream
    once the LOD chains quintupled the product count: 41 of 64 entities were
    silently degraded to one material on the default slot. The budget is now
    a STALL budget -- it resets whenever any entity comes ready, and only a
    genuinely quiet stretch gives up on the stragglers.

  * a UE mesh that fills two slots with the SAME material exports FBX
    material nodes MI_X and MI_X_1; no manifest assignment carries the
    suffixed label, so that slot silently kept the model default (measured:
    sm_van_02e, 2969 of 5602 triangles). `finish_material_slots` now probes
    numeric-suffix variants of every assigned label and gives those rows the
    same material.

The azlmbr modules are faked at import time; the functions under test import
them lazily, so the fakes are what they see.
"""

import os
import sys
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter",
                                "Editor", "Scripts"))

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


# --- the azlmbr fakes ---------------------------------------------------------
class Clock(object):
    frames = 0


def _module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


azlmbr = _module("azlmbr")
azlmbr_bus = _module("azlmbr.bus")
azlmbr_bus.Broadcast = "Broadcast"
azlmbr_bus.Event = "Event"
azlmbr_editor = _module("azlmbr.editor")
azlmbr_render = _module("azlmbr.render")
azlmbr_legacy = _module("azlmbr.legacy")
azlmbr_general = _module("azlmbr.legacy.general")
azlmbr.bus = azlmbr_bus
azlmbr.editor = azlmbr_editor
azlmbr.render = azlmbr_render
azlmbr.legacy = azlmbr_legacy
azlmbr_legacy.general = azlmbr_general


def idle_wait_frames(count):
    Clock.frames += count


azlmbr_general.idle_wait_frames = idle_wait_frames

from ueimporter import prefab_build  # noqa: E402


# --- 1. the stall budget ------------------------------------------------------
# Readiness schedule in frames. Old TOTAL-budget behaviour: pair 2 (ready at
# 800 > 600) and pair 3 would both be dropped. Stall behaviour: progress at
# 400 and at 800 resets the quiet clock, so only the never-ready pair drops.
ready_at = {0: 0, 1: 400, 2: 800, 3: 10 ** 9}


def fake_get_property(pair, _prop):
    return (Clock.frames >= ready_at[pair], 0)


prefab_build._get_property = fake_get_property

Clock.frames = 0
stragglers = prefab_build.wait_for_model_rows([0, 1, 2, 3])
check(stragglers == {3},
      "slow-but-steady streaming must be waited out and only the never-ready "
      "entity dropped; got %r" % (stragglers,))
check(Clock.frames <= 800 + prefab_build.MODEL_READY_WAIT_FRAMES + 2 * prefab_build.MODEL_READY_POLL_FRAMES,
      "the wait must end one stall budget after the last progress, not run "
      "unbounded; waited %d frames" % Clock.frames)

Clock.frames = 0
check(prefab_build.wait_for_model_rows([0]) == set(),
      "an immediately-ready entity must not wait at all")
check(Clock.frames == 0,
      "no idling when everything is ready on the first probe; waited %d"
      % Clock.frames)


# --- 2. dedup-suffix slots ----------------------------------------------------
SLOT_IDS = {"MI_A": 101, "MI_B": 102, "MI_B_1": 103}
ROW_STABLE_IDS = [101, 102, 103]
set_calls = []
warned = []


class Outcome(object):
    def IsSuccess(self):
        return True


def rows_get_property(_pair, prop):
    # "Model Materials|[N]|Material Slot Stable Id"
    row = int(prop.split("[")[1].split("]")[0])
    if row < len(ROW_STABLE_IDS):
        return (True, ROW_STABLE_IDS[row])
    return (False, None)


def find_assignment_id(_bus_kind, _name, _entity, _lod, label):
    result = types.SimpleNamespace()
    if label in SLOT_IDS:
        result.materialSlotStableId = SLOT_IDS[label]
    return result


def component_api(_bus_kind, name, _pair, prop, asset):
    if name == "SetComponentProperty":
        set_calls.append((int(prop.split("[")[1].split("]")[0]), asset))
    return Outcome()


class Report(object):
    def warn(self, code, _subject, _detail, severity=None):
        warned.append(code)


azlmbr_render.MaterialComponentRequestBus = find_assignment_id
azlmbr_editor.EditorComponentAPIBus = component_api
prefab_build._get_property = rows_get_property

assigned = prefab_build.finish_material_slots(
    "pair", "entity", [("MI_A", "asset_a"), ("MI_B", "asset_b")],
    "SM_Van_02e", Report())
check(assigned == 3,
      "two labels + one dedup suffix = three rows assigned; got %d" % assigned)
check((2, "asset_b") in set_calls,
      "the MI_B_1 row must receive MI_B's material; set calls: %r" % set_calls)
check((0, "asset_a") in set_calls and (1, "asset_b") in set_calls,
      "exact-label rows keep their own materials; set calls: %r" % set_calls)
check(warned == ["MAT_SLOT_DEDUP_SUFFIX"],
      "the suffix assignment must be reported, and nothing else; got %r"
      % warned)
check(len(set_calls) == 3,
      "no row may be set twice; set calls: %r" % set_calls)

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

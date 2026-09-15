"""
test_import_chunks.py — parallel chunk imports never share a scratch level and stop on failure.

Pure: no editor (the runner is injected). Run: python Tests/perf/test_import_chunks.py
"""

import os
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "Tools"))

import import_chunks as ic  # noqa: E402

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print("FAIL: " + message)
    return condition


def quiet(_message):
    pass


# --- chunk selection -----------------------------------------------------------
check(ic.parse_only("", 5) == [1, 2, 3, 4, 5], "no subset means every chunk")
check(ic.parse_only("1, 4,7-9", 15) == [1, 4, 7, 8, 9], "lists and ranges parse")
try:
    ic.parse_only("0,16", 15)
    check(False, "chunks outside 1..n must raise")
except ValueError:
    pass

# --- scratch levels ------------------------------------------------------------
check(ic.scratch_level(1, 1) == "UEO3DE_Scratch", "a serial import keeps the shared scratch level")
check(ic.scratch_level(2, 3) == "UEO3DE_Scratch_2", "a parallel slot gets its own scratch level")
env = ic.chunk_environment({"UEO3DE_CHUNK_ORDER": "spatial"}, 4, 15, "X:/exp", 2, 3)
check(env["UEO3DE_CHUNK"] == "4/15" and env["UEO3DE_EXPORT"] == "X:/exp"
      and env["UEO3DE_SCRATCH_LEVEL"] == "UEO3DE_Scratch_2" and env["UEO3DE_CHUNK_ORDER"] == "spatial",
      "the chunk environment sets chunk, export and slot scratch level, and keeps the caller's knobs")

# --- no two RUNNING chunks ever share a slot -------------------------------------
running = {}
overlap = []
lock = threading.Lock()


def slow_runner(chunk, slot):
    with lock:
        if slot in running.values():
            overlap.append((chunk, slot))
        running[chunk] = slot
    time.sleep(0.02)
    with lock:
        del running[chunk]
    return True


results = ic.run_all(list(range(1, 16)), 3, slow_runner, quiet)
check(all(results.values()) and len(results) == 15, "all 15 chunks run and pass")
check(not overlap, "two running chunks shared a slot (and so a scratch level): %r" % overlap)

# --- after a failure, nothing new starts ----------------------------------------
started = []


def failing_runner(chunk, slot):
    started.append(chunk)
    time.sleep(0.01)
    return chunk != 2


results = ic.run_all(list(range(1, 11)), 1, failing_runner, quiet)
check(results[2] is False, "the failing chunk is reported as failed")
check(started == [1, 2], "serially, no chunk may start after a failure; started %r" % started)
check(all(results[c] is None for c in range(3, 11)), "chunks that never started are reported as skipped")


def raising_runner(chunk, slot):
    raise RuntimeError("editor crashed")


check(ic.run_all([1], 2, raising_runner, quiet) == {1: False},
      "a runner that raises is a failed chunk, not a crash of the driver")

# --- the shared Asset Processor is waited for, not raced ---------------------------
class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


clock = Clock()
check(ic.wait_until(lambda: clock.now >= 3.0, lambda: True, 60, clock.time, clock.sleep) and clock.now >= 3.0,
      "editors launch only once the AP port accepts connections")
clock = Clock()
check(not ic.wait_until(lambda: False, lambda: clock.now < 2.0, 60, clock.time, clock.sleep) and clock.now < 60,
      "an AP that exits (e.g. port already bound) fails fast instead of waiting out the timeout")
clock = Clock()
check(not ic.wait_until(lambda: False, lambda: True, 10, clock.time, clock.sleep),
      "an AP that never opens its port fails after the timeout")
free = ic.socket.socket()
free.bind(("127.0.0.1", 0))
port = free.getsockname()[1]
check(not ic.port_open(port), "a bound but not listening port is not an Asset Processor")
free.listen(1)
check(ic.port_open(port), "a listening port is detected")
free.close()

print("")
print("RESULT: " + ("PASS" if not failures else "FAIL (%d)" % len(failures)))
sys.exit(1 if failures else 0)

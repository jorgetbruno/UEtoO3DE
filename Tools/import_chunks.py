"""
import_chunks.py — import a chunked level, several chunks at a time.

    python Tools/import_chunks.py --project D:/O3DE/Projects/Phoenix
                                  --export D:/Gamedev/UEtoO3DE/Exports/NYC_Level_WC
                                  --chunks 15 --parallel 3 [--only 1,4] [--results <dir>]

Every chunk is one headless editor running Tests/m2/m2_import.py with
UEO3DE_CHUNK=i/n; import knobs set in the environment (UEO3DE_CHUNK_ORDER,
UEO3DE_SKIP_CAMERAS, ...) pass through unchanged. Exit code 0 only if every
requested chunk passed.

WHY. Importing NYC1950 as 15 chunks took ~130 s a chunk, one after another:
~95 s of import stages plus ~35 s of editor start-up and shutdown that no stage
records. The chunks are independent -- each writes its own prefab and ledger --
so they can run side by side, as the export's worker editors do. Each parallel
SLOT gets its own scratch level (UEO3DE_Scratch_<slot>): the importer rewrites
its scratch level's file when a stale instance of the prefab it is importing is
found there, and two editors must never share that file.

ONE ASSET PROCESSOR. A batch editor that finds no Asset Processor listening
launches its own. Three editors started together all find none, all launch one,
and every AP after the first fails to bind the port (45643) and puts up a modal
"Cannot start Asset Processor server" dialog that blocks the run. So a parallel
run starts a single AP first and launches editors only once its port accepts
connections; an AP already running is reused, and one we started is stopped at
the end.
"""

import argparse
import os
import queue
import socket
import subprocess
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO_ROOT, "Tests", "o3de", "run_o3de_python.bat")
IMPORT_SCRIPT = os.path.join(REPO_ROOT, "Tests", "m2", "m2_import.py")
SCRATCH = "UEO3DE_Scratch"
AP_PORT = 45643                     # /Amazon/AzCore/Bootstrap/remote_port (bootstrap.setreg)
AP_READY_TIMEOUT = 300.0


def parse_only(text, total):
    """'1,4,7-9' -> [1, 4, 7, 8, 9]; None -> every chunk. Out of range raises."""
    if not text:
        return list(range(1, total + 1))
    chunks = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            low, high = (int(x) for x in part.split("-", 1))
            chunks.extend(range(low, high + 1))
        elif part:
            chunks.append(int(part))
    bad = [c for c in chunks if not 1 <= c <= total]
    if bad:
        raise ValueError("chunks %r are outside 1..%d" % (bad, total))
    return sorted(set(chunks))


def scratch_level(slot, parallel):
    """The scratch level a slot imports in: the shared one when serial."""
    return SCRATCH if parallel <= 1 else "%s_%d" % (SCRATCH, slot)


def chunk_environment(base, chunk, total, export, slot, parallel):
    env = dict(base)
    env["UEO3DE_EXPORT"] = export
    env["UEO3DE_CHUNK"] = "%d/%d" % (chunk, total)
    env["UEO3DE_SCRATCH_LEVEL"] = scratch_level(slot, parallel)
    return env


def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def wait_until(ready, alive, timeout, clock=time.time, sleep=time.sleep):
    """Poll `ready()` until it is true. False if `alive()` goes false or time runs out."""
    deadline = clock() + timeout
    while clock() < deadline:
        if ready():
            return True
        if not alive():
            return False
        sleep(0.5)
    return ready()


def start_asset_processor(o3de_bin, engine, project, log, port=AP_PORT):
    """(process or None, error or None). None, None means an AP was already listening."""
    if port_open(port):
        log("an Asset Processor is already listening on %d; the editors will share it" % port)
        return None, None
    exe = os.path.join(o3de_bin, "AssetProcessor.exe")
    proc = subprocess.Popen([exe, "--start-hidden", "--engine-path=" + engine, "--project-path=" + project],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    if wait_until(lambda: port_open(port), lambda: proc.poll() is None, AP_READY_TIMEOUT):
        log("shared Asset Processor (pid %d) listening on %d after %.0fs" % (proc.pid, port, time.time() - t0))
        return proc, None
    stop_asset_processor(proc)
    return None, "the Asset Processor did not open port %d within %.0fs" % (port, AP_READY_TIMEOUT)


def stop_asset_processor(proc):
    if proc is None or proc.poll() is not None:
        return
    # /T: the AP starts its AssetBuilder children, which must not outlive it.
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_all(chunks, parallel, run_one, log=print):
    """Run `run_one(chunk, slot) -> bool` over `chunks`, `parallel` at a time.

    Slots are handed out from a pool, so no two running chunks ever share one.
    After the first failure no NEW chunk starts; the running ones finish, and
    every chunk that never started is reported as skipped. Returns
    {chunk: True | False | None(skipped)}.
    """
    parallel = max(1, min(parallel, len(chunks) or 1))
    slots = queue.Queue()
    for slot in range(1, parallel + 1):
        slots.put(slot)
    results = {chunk: None for chunk in chunks}
    failed = threading.Event()
    lock = threading.Lock()
    threads = []

    def worker(chunk, slot):
        try:
            ok = bool(run_one(chunk, slot))
        except Exception as error:                     # a crashed runner is a failed chunk
            log("chunk %d: runner raised %r" % (chunk, error))
            ok = False
        with lock:
            results[chunk] = ok
        if not ok:
            failed.set()
        slots.put(slot)

    for chunk in chunks:
        slot = slots.get()
        if failed.is_set():
            slots.put(slot)
            break
        thread = threading.Thread(target=worker, args=(chunk, slot))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--export", required=True, help="export directory holding manifest.json")
    parser.add_argument("--chunks", type=int, required=True, help="n in UEO3DE_CHUNK=i/n")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--only", default="", help="subset, e.g. 1,4,7-9")
    parser.add_argument("--results", default=os.path.join(REPO_ROOT, "Tests", "m2", "results", "chunks"))
    args = parser.parse_args(argv)

    chunks = parse_only(args.only, args.chunks)
    os.makedirs(args.results, exist_ok=True)
    started = time.time()
    print_lock = threading.Lock()

    def log(message):
        with print_lock:
            print("[t+%5.0fs] %s" % (time.time() - started, message), flush=True)

    def run_one(chunk, slot):
        result = os.path.join(args.results, "chunk_%d_of_%d.txt" % (chunk, args.chunks))
        env = chunk_environment(os.environ, chunk, args.chunks, args.export, slot, args.parallel)
        log("chunk %d/%d started in slot %d (%s)" % (chunk, args.chunks, slot, env["UEO3DE_SCRATCH_LEVEL"]))
        t0 = time.time()
        proc = subprocess.run(["cmd", "/c", RUNNER, IMPORT_SCRIPT, result, args.project],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        verdict = "NO RESULT FILE"
        if os.path.exists(result):
            with open(result, "r", encoding="utf-8", errors="replace") as handle:
                lines = [line.strip() for line in handle if line.strip()]
            verdict = lines[-1] if lines else "EMPTY RESULT FILE"
        ok = proc.returncode == 0 and verdict.startswith("RESULT: PASS")
        log("chunk %d/%d %s in %.0fs (exit %d): %s"
            % (chunk, args.chunks, "PASS" if ok else "FAIL", time.time() - t0, proc.returncode, verdict))
        return ok

    ap = None
    if args.parallel > 1 and len(chunks) > 1:
        sys.path.insert(0, os.path.join(REPO_ROOT, "Tests"))
        from paths import PATHS
        o3de_bin = PATHS["O3DE_BIN"]
        engine = os.path.normpath(os.path.join(o3de_bin, "..", "..", "..", ".."))
        ap, error = start_asset_processor(o3de_bin, engine, args.project, log)
        if error:
            log("FAIL: " + error)
            return 1
    try:
        results = run_all(chunks, args.parallel, run_one, log)
    finally:
        stop_asset_processor(ap)
    passed = sorted(c for c, ok in results.items() if ok)
    failed = sorted(c for c, ok in results.items() if ok is False)
    skipped = sorted(c for c, ok in results.items() if ok is None)
    log("done: %d passed, %d failed %s, %d not started %s, %.1f min"
        % (len(passed), len(failed), failed or "", len(skipped), skipped or "",
           (time.time() - started) / 60.0))
    return 0 if not failed and not skipped else 1


if __name__ == "__main__":
    sys.exit(main())

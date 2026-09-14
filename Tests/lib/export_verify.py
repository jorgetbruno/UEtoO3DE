"""
export_verify.py — the export's intermediate bounds check, outside the editor.

The check is unchanged: every written mesh must match the expected bounds its
export recorded (mirror-X for normal entries, verbatim for #mx variants), or a
negation went missing or got doubled and the product will be mirrored.

What changed is WHERE it runs. It used to run inside the UE session, one file
at a time on the editor's single Python thread, re-parsing every FBX of the
level with a pure-Python reader -- NYC_Level_WC's 2,272 files and 580 MB kept
the editor alive for the last stretch of a three-hour export doing nothing
but reading. The records now land on disk (`export_records.json`) and this
module checks them after the editor has exited, across every core.
"""

import multiprocessing
import os

import fbx_reader
import gltf_reader

BOUNDS_TOLERANCE_CM = 1e-3


def check_record(record, assets_root, readers=None):
    """None when the file matches its record, else a failure message.

    `readers` is `(is_gltf, gltf_stats, fbx_stats)`, injectable for tests.
    """
    is_gltf, gltf_stats, fbx_stats = readers or (
        gltf_reader.gltf_source.is_gltf_source, gltf_reader.vertex_stats,
        fbx_reader.vertex_stats)
    expected_min = list(record["ue_bounds_min"])
    expected_max = list(record["ue_bounds_max"])
    path = os.path.join(assets_root, record["relative_path"]).replace("\\", "/")
    tolerance = record.get("tolerance_cm", BOUNDS_TOLERANCE_CM)

    # ONE recorded expectation, converted per container. The record holds the
    # FBX-file expectation; a glTF is Y-up and in METRES, so both the numbers
    # and the tolerance have to be converted or the check is meaningless.
    if is_gltf(path):
        label = "glTF"
        expected_min, expected_max = gltf_reader.expected_from_fbx_bounds(
            expected_min, expected_max)
        tolerance = tolerance / 100.0
        reader = gltf_stats
    else:
        label = "FBX"
        reader = fbx_stats
    try:
        stats = reader(path)
    except Exception as error:                      # unreadable = failed, never skipped
        return "%s: %s could not be read (%s)" % (record["relative_path"], label, error)

    deltas = [max(abs(stats["min"][i] - expected_min[i]),
                  abs(stats["max"][i] - expected_max[i])) for i in range(3)]
    # The bake goes through float32 geometry: at 392 m from the origin one ulp
    # is ~0.004 cm. A mirror error moves bounds by the coordinate itself, so a
    # tolerance that grows with magnitude (~4e-6 relative) cannot hide one.
    magnitude = max(abs(v) for v in list(expected_min) + list(expected_max)) or 0.0
    tolerance = max(tolerance, magnitude * 4e-6)
    if max(deltas) <= tolerance:
        return None
    return ("%s: %s does not match its expected intermediate bounds.\n"
            "  file bounds %s .. %s\n"
            "  expected    %s .. %s\n"
            "The bake stage and the writer's negation should cancel here; one "
            "is missing or doubled, and the product will be mirrored."
            % (record["relative_path"], label,
               [round(v, 4) for v in stats["min"]], [round(v, 4) for v in stats["max"]],
               [round(v, 4) for v in expected_min], [round(v, 4) for v in expected_max]))


def _check_one(job):
    record, assets_root = job
    return check_record(record, assets_root)


def verify_records(records, assets_root, processes=None):
    """Check every record; returns (checked, [failure messages]).

    Records are checked in parallel -- each is an independent file read -- and
    the failures come back in record order, so a run reports the same first
    failure however the work was scheduled.
    """
    records = list(records)
    if not records:
        return 0, []
    processes = processes or max(1, min(len(records), (os.cpu_count() or 2) - 1))
    jobs = [(record, assets_root) for record in records]
    if processes == 1:
        results = [_check_one(job) for job in jobs]
    else:
        with multiprocessing.Pool(processes) as pool:
            results = pool.map(_check_one, jobs, chunksize=max(1, len(jobs) // (processes * 8)))
    return len(records), [message for message in results if message]

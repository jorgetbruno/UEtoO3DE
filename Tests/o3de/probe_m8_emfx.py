"""
probe_m8_emfx.py -- M8: EMotionFX authoring + playback APIs, measured.

Prereq: Assets/uetoo3de/__m8probe/probe_character.fbx + probe_anim.fbx staged
and AP-processed (products: probe_character.actor, probe_anim.motion --
measured: the DEFAULT scene rules make both, no .assetinfo needed).

Findings from rounds 1-6 (this file is the round-7 shape):
  * component names: 'Actor', 'Simple Motion', 'Anim Graph';
  * Actor asset property: 'Actor asset'; Simple Motion: 'Configuration|Motion'
    + 'Configuration|Play on active' + 'Configuration|Loop motion';
  * NO EMotionFX bus is reflected to EditorPythonBindings in 26.05 -- azlmbr
    has no animation/motion module, azlmbr.default carries only ScriptCanvas
    nodeables, no bounds bus exists anywhere, and the Actor attachment
    feature pins the attached entity to the TARGET ENTITY origin, not to a
    joint. Joint transforms are therefore unreachable from Python;
  * FrameCaptureRequestBus (azlmbr.atom) DOES write real screenshots in this
    headless editor (BatchMode still renders).

This round measures the only playback observable left standing: PIXEL deltas
between game-mode frames (animated actor) vs edit-mode frames (noise floor).

Run: Tests/o3de/run_o3de_python.bat Tests/o3de/probe_m8_emfx.py
"""

import os
import struct
import sys
import traceback
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEM_SCRIPTS = os.path.join(REPO_ROOT, "O3DE", "Gems", "UEImporter", "Editor", "Scripts")
if GEM_SCRIPTS not in sys.path:
    sys.path.insert(0, GEM_SCRIPTS)

if len(sys.argv) > 1 and sys.argv[1].strip() and not sys.argv[1].startswith('-'):
    RESULT_PATH = sys.argv[1]
else:
    RESULT_PATH = os.path.join(SCRIPT_DIR, 'results', 'probe_m8_emfx_result.txt')

os.makedirs(os.path.dirname(os.path.abspath(RESULT_PATH)), exist_ok=True)
_handle = open(RESULT_PATH, 'w')
_failures = []

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def log(msg=""):
    _handle.write(str(msg) + "\n")
    _handle.flush()
    print(msg)


def decode_png(path):
    """(width, height, channels, raw bytes) for an 8-bit PNG. Stdlib only."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if blob[:8] != PNG_MAGIC:
        raise ValueError("not a PNG: " + path)
    pos, width, height, bit_depth, color_type = 8, 0, 0, 0, 0
    idat = b""
    while pos < len(blob):
        length, ctype = struct.unpack_from(">I4s", blob, pos)
        data = blob[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack_from(">IIBB", data)
        elif ctype == b"IDAT":
            idat += data
        pos += 12 + length
    if bit_depth != 8:
        raise ValueError("bit depth %d unsupported" % bit_depth)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _y in range(height):
        filt = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filt == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif filt == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif filt == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev[i]
                ul = prev[i - channels] if i >= channels else 0
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                line[i] = (line[i] + pred) & 255
        out += line
        prev = line
    return width, height, channels, bytes(out)


def mean_abs_delta(path_a, path_b):
    wa, ha, ca, a = decode_png(path_a)
    wb, hb, cb, b = decode_png(path_b)
    if (wa, ha, ca) != (wb, hb, cb):
        return None, "dims differ %s vs %s" % ((wa, ha, ca), (wb, hb, cb))
    total = sum(abs(x - y) for x, y in zip(a, b))
    changed = sum(1 for i in range(0, len(a), ca)
                  if any(abs(a[i + c] - b[i + c]) > 8 for c in range(ca)))
    return (total / float(len(a)), changed / float(wa * ha)), "%dx%dx%d" % (wa, ha, ca)


def main():
    import azlmbr.atom as atom
    import azlmbr.bus as bus
    import azlmbr.components as components
    import azlmbr.editor as editor
    import azlmbr.entity as entity_module
    import azlmbr.legacy.general as general
    import azlmbr.math as azmath

    from ueimporter import asset_wait, prefab_build

    log("=== setup: entity with Actor + Simple Motion (known-good recipe) ===")
    actor_id = asset_wait.resolve("assets/uetoo3de/__m8probe/probe_character.actor")
    motion_id = asset_wait.resolve("assets/uetoo3de/__m8probe/probe_anim.motion")
    log("actor %s motion %s" % (actor_id is not None, motion_id is not None))
    if actor_id is None or motion_id is None:
        raise RuntimeError("probe products missing; run AssetProcessorBatch first")

    general.idle_enable(True)
    general.open_level_no_prompt("DefaultLevel")
    general.idle_wait_frames(30)
    entity_id = editor.ToolsApplicationRequestBus(
        bus.Broadcast, 'CreateNewEntity', entity_module.EntityId())
    editor.EditorEntityAPIBus(bus.Event, 'SetName', entity_id, 'M8_Probe')
    components.TransformBus(bus.Event, 'SetWorldTranslation', entity_id,
                            azmath.Vector3(0.0, 0.0, 1.0))

    actor_type = prefab_build.resolve_component_type("Actor")
    editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType',
                                 entity_id, [actor_type])
    pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, actor_type).GetValue()
    editor.EditorComponentAPIBus(bus.Broadcast, 'SetComponentProperty',
                                 pair, 'Actor asset', actor_id)

    sm_type = prefab_build.resolve_component_type("Simple Motion")
    editor.EditorComponentAPIBus(bus.Broadcast, 'AddComponentsOfType',
                                 entity_id, [sm_type])
    sm_pair = editor.EditorComponentAPIBus(
        bus.Broadcast, 'GetComponentOfType', entity_id, sm_type).GetValue()
    for path, value in (("Configuration|Motion", motion_id),
                        ("Configuration|Play on active", True),
                        ("Configuration|Loop motion", True)):
        result = editor.EditorComponentAPIBus(
            bus.Broadcast, 'SetComponentProperty', sm_pair, path, value)
        log("set %r: %s" % (path, result.IsSuccess() if result else None))

    scratch = os.path.join(SCRIPT_DIR, "results", "m8shots")
    os.makedirs(scratch, exist_ok=True)

    def capture(name):
        target = os.path.join(scratch, name).replace("\\", "/")
        if os.path.exists(target):
            os.remove(target)
        atom.FrameCaptureRequestBus(bus.Broadcast, 'CaptureScreenshot', target)
        general.idle_wait_frames(30)
        return target if os.path.exists(target) else None

    log("")
    log("=== edit-mode pair: the noise floor ===")
    general.idle_wait_frames(60)
    general.set_current_view_position(3.0, 3.0, 2.0)
    general.set_current_view_rotation(-20.0, 0.0, 135.0)
    general.idle_wait_frames(30)
    edit_a = capture("edit_a.png")
    general.idle_wait_frames(60)
    edit_b = capture("edit_b.png")
    if edit_a and edit_b:
        delta, info = mean_abs_delta(edit_a, edit_b)
        if delta is None:
            log("  EDIT pair: %s" % info)
        else:
            log("  EDIT pair (%s): mean|d|=%.4f changed_px=%.4f"
                % (info, delta[0], delta[1]))
    else:
        log("  EDIT captures missing: %r %r" % (edit_a, edit_b))

    log("")
    log("=== game-mode pair: the animation signal ===")
    general.enter_game_mode()
    general.idle_wait_frames(300)   # dynamic bodies settle; the motion loops on
    log("in game mode: %s" % general.is_in_game_mode())
    game_a = capture("game_a.png")
    general.idle_wait_frames(45)
    game_b = capture("game_b.png")
    if game_a and game_b:
        delta, info = mean_abs_delta(game_a, game_b)
        if delta is None:
            log("  GAME pair: %s" % info)
        else:
            log("  GAME pair (%s): mean|d|=%.4f changed_px=%.4f"
                % (info, delta[0], delta[1]))
    else:
        log("  GAME captures missing: %r %r" % (game_a, game_b))

    general.exit_game_mode()
    general.idle_wait_frames(10)
    editor.ToolsApplicationRequestBus(bus.Broadcast, 'DeleteEntityById', entity_id)


try:
    main()
    log("")
    log("RESULT: PASS")
except Exception:
    log("FATAL: " + traceback.format_exc())
    log("")
    log("RESULT: FAIL")
    _failures.append("fatal")

_handle.close()

import azlmbr.legacy.general as _general
if not _failures:
    _general.exit_no_prompt()
else:
    os._exit(1)

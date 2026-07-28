"""
probe_texture_export.py — which textures will UE refuse to export, and why?

`material_export.export_all` exports every texture through an
`AssetExportTask` with a `.tga` filename, and on a real marketplace pack that
failed outright:

    LogExporter: Warning: No tga exporter found for Texture2D
        /Game/LS_Scifi_ModernCity/Textures/T_Grunge_06_O
    MaterialExportError: texture export failed: .../T_Grunge_06_O

`UTextureExporterTGA::SupportsObject` accepts only some source formats, so the
refusal is a property of the texture, not of the pack. Before writing a
fallback this asks the two questions that decide which fallback is right:

  1. HOW MANY of this level's textures does TGA refuse, and what do they have
     in common (source format, compression settings, sRGB)?
  2. Does PNG accept the ones TGA refuses? If it does, the fallback is an
     export format; if it does not, the fallback has to be pixel access.

It exports to a scratch directory and deletes as it goes, so it answers the
question without producing the assets -- a failed export must not leave a
half-written texture set that a later run mistakes for a complete one.

Args:  --manifest=<path to manifest.json>  (forward slashes)
       --scratch=<writable directory>
Run:   UnrealEditor.exe <project>.uproject \\
           -ExecutePythonScript="<repo>/Tests/ue/probe_texture_export.py \\
               --manifest=... --scratch=... --result=..." -unattended -nop4 -nosplash
"""

import json
import os
import sys
import traceback

import unreal


def parse_args(argv):
    options = {"manifest": "", "scratch": "", "result": ""}
    unknown = []
    for token in argv:
        if not token.startswith("--"):
            unknown.append(token)
            continue
        key, sep, value = token[2:].partition("=")
        if key not in options or not sep:
            unknown.append(token)
        else:
            options[key] = value
    if unknown:
        raise ValueError("unrecognized argument(s): %s"
                         % ", ".join(repr(u) for u in unknown))
    return options


def try_export(texture, path):
    """(ok, error) for one AssetExportTask."""
    task = unreal.AssetExportTask()
    task.object = texture
    task.filename = path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    try:
        ran = unreal.Exporter.run_asset_export_task(task)
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:80])
    if not ran:
        return False, "run_asset_export_task returned False"
    if not os.path.exists(path):
        return False, "task reported success but wrote no file"
    size = os.path.getsize(path)
    os.remove(path)
    if size == 0:
        return False, "wrote a zero-byte file"
    return True, "%d bytes" % size


def keep_export(texture, path):
    """Like `try_export` but KEEPS the file -- the cross-check needs to read it."""
    task = unreal.AssetExportTask()
    task.object = texture
    task.filename = path
    task.automated = True
    task.replace_identical = True
    task.prompt = False
    try:
        ran = unreal.Exporter.run_asset_export_task(task)
    except Exception:
        return False
    return bool(ran) and os.path.exists(path) and os.path.getsize(path) > 0


def describe(texture):
    out = {}
    for prop in ("compression_settings", "srgb", "lod_group",
                 "virtual_texture_streaming"):
        try:
            value = texture.get_editor_property(prop)
            out[prop] = str(value).split(".")[-1] if value is not None else None
        except Exception:
            out[prop] = "<unreadable>"
    # The source format is what the TGA exporter actually tests against.
    try:
        source = texture.get_editor_property("source")
        out["source_format"] = str(source.get_editor_property("format")).split(".")[-1]
    except Exception:
        try:
            out["source_format"] = str(texture.source.get_format()).split(".")[-1]
        except Exception:
            out["source_format"] = "<unreadable>"
    return out


def main():
    options = parse_args(sys.argv[1:])
    lines = []

    def log(message):
        lines.append(str(message))
        unreal.log("[probe_tex] " + str(message))

    status = "PASS"
    try:
        document = json.load(open(options["manifest"]))
        textures = [a for a in document["assets"] if a["kind"] == "texture"]
        # One entry per UE asset: the manifest lists a texture once per ROLE.
        by_path = {}
        for entry in textures:
            by_path.setdefault(entry["ue_path"], entry)
        log("manifest lists %d texture entries, %d distinct UE assets"
            % (len(textures), len(by_path)))

        scratch = options["scratch"] or unreal.Paths.project_saved_dir()
        os.makedirs(scratch, exist_ok=True)

        tga_fail, png_fail, missing = [], [], []
        formats = {}
        for index, (ue_path, _entry) in enumerate(sorted(by_path.items())):
            texture = unreal.EditorAssetLibrary.load_asset(ue_path)
            if texture is None:
                missing.append(ue_path)
                continue
            info = describe(texture)
            key = (info["source_format"], info["compression_settings"])
            formats.setdefault(key, {"n": 0, "tga_ok": 0, "png_ok": 0})
            formats[key]["n"] += 1

            base = os.path.join(scratch, "probe_%04d" % index)
            tga_ok, tga_why = try_export(texture, base + ".tga")
            png_ok, png_why = try_export(texture, base + ".png")
            if tga_ok:
                formats[key]["tga_ok"] += 1
            else:
                tga_fail.append((ue_path, info, tga_why))
            if png_ok:
                formats[key]["png_ok"] += 1
            else:
                png_fail.append((ue_path, info, png_why))

        log("")
        log("=== by (source format, compression settings) ===")
        log("  %-22s %-26s %5s %8s %8s" % ("source format", "compression", "n", "tga ok", "png ok"))
        for (fmt, comp), counts in sorted(formats.items()):
            log("  %-22s %-26s %5d %8d %8d"
                % (fmt, comp, counts["n"], counts["tga_ok"], counts["png_ok"]))

        log("")
        log("=== TGA refused %d of %d ===" % (len(tga_fail), len(by_path)))
        for ue_path, info, why in tga_fail[:40]:
            log("  %-64s %-18s %s" % (ue_path.split("/")[-1],
                                      info["source_format"], why))
        log("")
        log("=== PNG refused %d of %d ===" % (len(png_fail), len(by_path)))
        for ue_path, info, why in png_fail[:40]:
            log("  %-64s %-18s %s" % (ue_path.split("/")[-1],
                                      info["source_format"], why))
        if missing:
            log("")
            log("=== %d texture(s) would not load ===" % len(missing))
            for ue_path in missing[:20]:
                log("  " + ue_path)

        # --- ground truth for the PNG decoder -----------------------------
        # `ueo3de/png.py` exists to rescue textures TGA refuses, and its unit
        # tests encode fixtures with the same reading of the spec that the
        # decoder uses -- so a shared misreading would round-trip happily.
        # This is the independent check: export the SAME texture both ways and
        # compare the decoder's output against the bytes UE's own TGA exporter
        # wrote. Different implementation, same pixels, or the decoder is wrong.
        #
        # Bounded by a pixel budget rather than a texture count: decoding is a
        # per-pixel Python loop, and a couple of 4K textures would take longer
        # than the entire rest of this probe. What is skipped is REPORTED, so
        # the coverage is never overstated.
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "UE", "UEtoO3DEFixture", "Plugins", "UEO3DEExporter", "Content", "Python"))
        from ueo3de import png as png_module
        from ueo3de import tga as tga_module

        BUDGET = 3000000
        log("")
        log("=== PNG decoder vs UE's own TGA output (budget %d pixels) ===" % BUDGET)
        spent = 0
        compared = skipped = mismatched = 0
        for ue_path in sorted(by_path):
            if spent >= BUDGET:
                skipped += 1
                continue
            texture = unreal.EditorAssetLibrary.load_asset(ue_path)
            if texture is None:
                continue
            base = os.path.join(scratch, "xcheck")
            tga_path, png_path = base + ".tga", base + ".png"
            for stale in (tga_path, png_path):
                if os.path.exists(stale):
                    os.remove(stale)
            task_ok = (keep_export(texture, tga_path) and keep_export(texture, png_path))
            if not task_ok:
                continue
            try:
                reference = tga_module.read(tga_path)
                pixels = reference["width"] * reference["height"]
                if spent + pixels > BUDGET:
                    skipped += 1
                    continue
                spent += pixels
                decoded = png_module.read(png_path)
                same_size = (decoded["width"] == reference["width"]
                             and decoded["height"] == reference["height"])
                stride = reference["bpp"] // 8
                # UE writes TGA bottom-up unless bit 5 says otherwise; compare
                # in the reference's own row order.
                top_down = bool(reference["descriptor"] & 0x20)
                bad = 0
                for row in range(reference["height"]):
                    src_row = row if top_down else reference["height"] - 1 - row
                    for col in range(reference["width"]):
                        ref = src_row * reference["width"] + col
                        got = row * decoded["width"] + col
                        rb = reference["pixels"][ref * stride:ref * stride + 3]
                        gb = decoded["pixels"][got * 4:got * 4 + 3]
                        # reference is BGR, decoded is RGB
                        if (rb[0], rb[1], rb[2]) != (gb[2], gb[1], gb[0]):
                            bad += 1
                            if bad > 8:
                                break
                    if bad > 8:
                        break
                compared += 1
                if not same_size or bad:
                    mismatched += 1
                    log("  MISMATCH %-52s size_ok=%s differing_pixels>=%d"
                        % (ue_path.split("/")[-1], same_size, bad))
            except Exception as exc:
                log("  could not compare %s: %s: %s"
                    % (ue_path.split("/")[-1], type(exc).__name__, str(exc)[:80]))
            finally:
                for stale in (tga_path, png_path):
                    if os.path.exists(stale):
                        os.remove(stale)
        log("  compared %d texture(s) pixel-for-pixel, %d skipped for budget, "
            "%d MISMATCHED" % (compared, skipped, mismatched))
        if compared == 0:
            log("  NO texture was compared -- this check proved nothing")
        elif mismatched:
            log("  the PNG decoder disagrees with UE on real images")
        else:
            log("  the PNG decoder reproduces UE's TGA output exactly")

        log("")
        if tga_fail and not png_fail:
            log("VERDICT: PNG accepts everything TGA refuses -- the fallback is "
                "an export FORMAT, not pixel access.")
        elif not tga_fail:
            log("VERDICT: TGA accepted everything here; the earlier failure is "
                "not reproducible from this list and needs a closer look.")
        else:
            log("VERDICT: %d texture(s) are refused by BOTH exporters -- a "
                "format fallback is not enough on its own." % len(
                    set(p for p, _i, _w in tga_fail)
                    & set(p for p, _i, _w in png_fail)))
    except Exception:
        log("PROBE FAILED")
        log(traceback.format_exc())
        unreal.log_error("[probe_tex] " + traceback.format_exc())
        status = "FAIL"

    lines.append("RESULT: " + status)
    result_path = options["result"] or os.path.join(
        options["scratch"] or ".", "probe_texture_export_result.txt")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(result_path)), exist_ok=True)
        with open(result_path, "w") as handle:
            handle.write("\n".join(lines) + "\n")
    except Exception:
        unreal.log_error("[probe_tex] could not write " + str(result_path))

    print("RESULT: " + status)
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception:
        pass
    return 0 if status == "PASS" else 1


raise SystemExit(main())

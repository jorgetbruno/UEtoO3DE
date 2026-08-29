"""
material_export.py — UE material graphs -> manifest material data + textures (M4).

Scope set by spike S4.0 (measured over 41 real master materials + the fixture):
recognition is **per property, not per material** -- most "unsupported"
materials fail on one channel while the rest map cleanly, so each property is
classified independently and every unmapped one becomes a coded warning rather
than sinking the whole material. Material INSTANCES (88 in the spike project --
the actual content) resolve through their parent's graph with instance
parameter values read from the leaf.

Recognized per property (BaseColor, Normal, Roughness, Metallic, AO,
Opacity/OpacityMask):

    TextureSample / TextureSampleParameter2D    -> texture map (channel-aware)
    Constant / ScalarParameter                  -> scalar value
    Constant3Vector/Constant4Vector/VectorParam -> colour value
    Multiply(texture, constant-like)            -> texture + tint factor

Anything else -> `MAT_EXPR_UNSUPPORTED` for that property. A material whose
BASE COLOUR cannot be mapped gets no material data at all (`material_data:
None`) and its entities keep the backend's default material -- visibly grey
beats silently wrong.

Textures are exported ONCE per (texture, role) as TGA, named with the Atom
image builder's filemask suffixes so colour space is decided by the pipeline
itself (measured from ImageBuilder.settings):

    _basecolor -> Albedo (sRGB)      _normal -> Normals preset
    _roughness/_metallic -> Reflectance (linear)     _ao -> AmbientOcclusion

Channel-packed sources (the ORM case: one texture driving AO/R, Roughness/G,
Metallic/B) are exported once raw, then split into 8-bit GRAYSCALE PNGs by
`tga.write_channel_png` -- pure Python, testable offline. PNG because the
engine's TgaLoader rejects grayscale TGA ("unsupported type code [3]",
measured) and channel-replicated RGB TGA was 5.1x the bytes.

Normal maps: UE authors DirectX-style (green down); the manifest records
`flip_y: true` and the importer sets StandardPBR's `normal.flipY` (plan M4:
"a silent, ugly failure otherwise").
"""

import os

import unreal

from . import naming
from . import param_roles

# Property -> (UE enum name, manifest key, role suffix)
_PROPERTIES = (
    ("MP_BASE_COLOR", "base_color", "basecolor"),
    ("MP_NORMAL", "normal", "normal"),
    ("MP_ROUGHNESS", "roughness", "roughness"),
    ("MP_METALLIC", "metallic", "metallic"),
    ("MP_AMBIENT_OCCLUSION", "occlusion", "ao"),
    ("MP_OPACITY", "opacity", "opacity"),
    ("MP_OPACITY_MASK", "opacity_mask", "opacity"),
)

_TEXTURE_KINDS = ("MaterialExpressionTextureSample",
                  "MaterialExpressionTextureSampleParameter2D",
                  # Texture OBJECTS: how master-material functions receive
                  # their textures (the function samples internally). At the
                  # call site the input expression is the object, and it
                  # carries the same parameter/texture identity.
                  "MaterialExpressionTextureObjectParameter",
                  "MaterialExpressionTextureObject")
_SCALAR_KINDS = ("MaterialExpressionConstant", "MaterialExpressionScalarParameter")
_COLOR_KINDS = ("MaterialExpressionConstant3Vector", "MaterialExpressionConstant4Vector",
                "MaterialExpressionVectorParameter")

_BLEND_MODES = {
    "BLEND_OPAQUE": "opaque",
    "BLEND_MASKED": "masked",
    "BLEND_TRANSLUCENT": "translucent",
}


class MaterialExportError(Exception):
    pass


def _expression_kind(node):
    return node.get_class().get_name()


# Pass-through / statically-resolvable wrappers, followed before classifying.
# Measured on real content (probe_m4_matattrs): master materials route nearly
# everything through Reroute nodes and StaticSwitch(Parameter)s, and some feed
# the whole MaterialAttributes pin from a switch between two attribute sets.
_FOLLOW_DEPTH_LIMIT = 16


def _switch_value(node, instance):
    """The effective boolean of a StaticSwitch(Parameter) at export time."""
    kind = _expression_kind(node)
    if kind == "MaterialExpressionStaticSwitchParameter":
        name = node.get_editor_property("parameter_name")
        # Static switches cannot be overridden on a dynamic instance, so a
        # MaterialInstanceDynamic answers through its nearest constant
        # ancestor (the getter takes MaterialInstanceConstant only).
        constant = instance
        while constant is not None and _is_dynamic(constant):
            try:
                constant = constant.get_editor_property("parent")
            except Exception:
                constant = None
        if isinstance(constant, unreal.MaterialInstanceConstant):
            try:
                return bool(unreal.MaterialEditingLibrary
                            .get_material_instance_static_switch_parameter_value(
                                constant, name))
            except Exception:
                pass
        return bool(node.get_editor_property("default_value"))
    return None  # plain StaticSwitch: value pin unreadable; caller falls back


def _follow(master, node, instance):
    """Follow Reroute and decided StaticSwitch(Parameter) nodes to substance.

    Returns (node, channel_hint). ComponentMask with a single active channel
    contributes the channel hint and keeps following its input.
    """
    mel = unreal.MaterialEditingLibrary
    channel_hint = None
    for _ in range(_FOLLOW_DEPTH_LIMIT):
        if node is None:
            return None, channel_hint
        kind = _expression_kind(node)

        if kind in ("MaterialExpressionReroute", "MaterialExpressionNamedRerouteUsage"):
            inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
            node = inputs[0] if inputs else None
            continue

        if kind in ("MaterialExpressionStaticSwitchParameter",
                    "MaterialExpressionStaticSwitch"):
            value = _switch_value(node, instance)
            inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
            names = [str(n) for n in
                     (mel.get_material_expression_input_names(node) or [])]
            if value is None or not inputs:
                return node, channel_hint  # cannot decide; classify as-is (fails loudly)
            wanted = "True" if value else "False"
            chosen = None
            for name, expression in zip(names, inputs):
                if name == wanted:
                    chosen = expression
                    break
            node = chosen if chosen is not None else (inputs[0] if value else inputs[-1])
            continue

        if kind == "MaterialExpressionComponentMask":
            flags = [bool(node.get_editor_property(flag)) for flag in ("r", "g", "b", "a")]
            if sum(flags) == 1:
                channel_hint = "RGBA"[flags.index(True)]
            inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
            node = inputs[0] if inputs else None
            continue

        return node, channel_hint
    return node, channel_hint


def _find_texture(master, node, instance, max_nodes=64, max_depth=8):
    """Nearest texture expression beneath `node`, input-order DFS, bounded.

    Master materials bury the channel's texture under arbitrary value math
    (Desaturation, nested Multiplies, contrast helpers -- measured shape in
    probe_m4_tree). Enumerating those node kinds is a losing game; what the
    channel IS is its texture, and the math is an approximation the report
    makes visible. Returns (texture_node, channel_hint) or (None, None).
    """
    mel = unreal.MaterialEditingLibrary
    stack = [(node, 0, None)]
    visited = 0
    while stack and visited < max_nodes:
        current, depth, hint = stack.pop(0)
        current, follow_hint = _follow(master, current, instance)
        if current is None:
            continue
        visited += 1
        hint = follow_hint or hint
        if _expression_kind(current) in _TEXTURE_KINDS:
            return current, hint
        if depth >= max_depth:
            continue
        try:
            inputs = list(mel.get_inputs_for_material_expression(master, current) or [])
        except Exception:
            continue
        for part in inputs:
            if part is not None:
                stack.append((part, depth + 1, hint))
    return None, None


def _base_material_and_instance(material):
    """(master Material for graph reading, leaf instance for parameter values)."""
    if isinstance(material, unreal.MaterialInstance):
        base = material.get_base_material()
        return base, material
    return material, None


def _is_dynamic(instance):
    return isinstance(instance, unreal.MaterialInstanceDynamic)


def _instance_scalar(instance, name):
    """A scalar parameter through the right getter for the instance type.

    MaterialEditingLibrary's getters take MaterialInstanceConstant only --
    handing them a MaterialInstanceDynamic (a runtime instance a Blueprint
    construction script made, common on marketplace props) raises a
    TypeError and used to end the whole export. The dynamic instance's own
    K2 getters resolve its overrides through its parent chain.
    """
    if _is_dynamic(instance):
        return instance.get_scalar_parameter_value(name)
    return unreal.MaterialEditingLibrary.get_material_instance_scalar_parameter_value(
        instance, name)


def _instance_vector(instance, name):
    if _is_dynamic(instance):
        return instance.get_vector_parameter_value(name)
    return unreal.MaterialEditingLibrary.get_material_instance_vector_parameter_value(
        instance, name)


def _instance_texture(instance, name):
    if _is_dynamic(instance):
        return instance.get_texture_parameter_value(name)
    return unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
        instance, name)


def _linear_color_to_rgb(value):
    return [float(value.r), float(value.g), float(value.b)]


def _scalar_of(node, instance):
    kind = _expression_kind(node)
    if kind == "MaterialExpressionConstant":
        return float(node.get_editor_property("r"))
    if kind == "MaterialExpressionScalarParameter":
        name = node.get_editor_property("parameter_name")
        if instance is not None:
            return float(_instance_scalar(instance, name))
        return float(node.get_editor_property("default_value"))
    return None


def _color_of(node, instance):
    kind = _expression_kind(node)
    if kind in ("MaterialExpressionConstant3Vector", "MaterialExpressionConstant4Vector"):
        return _linear_color_to_rgb(node.get_editor_property("constant"))
    if kind == "MaterialExpressionVectorParameter":
        name = node.get_editor_property("parameter_name")
        if instance is not None:
            return _linear_color_to_rgb(_instance_vector(instance, name))
        return _linear_color_to_rgb(node.get_editor_property("default_value"))
    return None


def _texture_of(node, instance):
    kind = _expression_kind(node)
    if kind in ("MaterialExpressionTextureSample", "MaterialExpressionTextureObject"):
        return node.get_editor_property("texture")
    if kind in ("MaterialExpressionTextureSampleParameter2D",
                "MaterialExpressionTextureObjectParameter"):
        name = node.get_editor_property("parameter_name")
        if instance is not None:
            found = _instance_texture(instance, name)
            if found is not None:
                return found
        return node.get_editor_property("texture")
    return None


class TextureBank:
    """Dedupes (texture, role[, channel]) -> planned export records."""

    def __init__(self, registry):
        self._registry = registry
        self._records = {}

    def request(self, texture, role_suffix, channel=None):
        """Plan an export; returns the manifest texture entry."""
        ue_path = naming.package_path(unreal.SystemLibrary.get_path_name(texture))
        role_key = role_suffix if channel is None else "%s@%s" % (role_suffix, channel)
        key = (ue_path, role_key)
        if key in self._records:
            return self._records[key]["entry"]

        stem = self._registry.claim(ue_path)  # idempotent for the same asset
        # The CHANNEL belongs in the filename, not just in the guid: one
        # texture can be requested for the same role both whole and as an
        # ORM channel split (measured on L_Showcase's T_Grass_ORM, which
        # produced ao/roughness/metallic twice over), and a role-only name
        # made the two write the SAME file -- whichever exported last won,
        # so one material silently got the wrong image data. The channel
        # goes BEFORE the role because the role must stay the filename
        # SUFFIX: that is what selects the Atom image preset.
        if channel is None:
            relative = "%s_%s.tga" % (stem, role_suffix)
        else:
            # Channel splits are GRAYSCALE PNG (see tga.write_channel_png:
            # the engine rejects grayscale TGA, and channel-replicated RGB
            # TGA was 5.1x the bytes). Whole textures stay TGA.
            relative = "%s_%s_%s.png" % (stem, str(channel).lower(), role_suffix)
        guid = naming.asset_guid(ue_path + "#" + role_key)
        entry = {
            "guid": guid,
            "kind": "texture",
            "ue_path": ue_path,
            "name": texture.get_name(),
            "o3de_relative_path": relative,
            "srgb": bool(texture.get_editor_property("srgb")),
            "role": role_suffix,
            "channel": channel,
        }
        self._records[key] = {"entry": entry, "texture": texture}
        return entry

    def entries(self):
        return [record["entry"] for record in self._records.values()]

    def find_by_guid(self, guid):
        """(key, texture, entry) for a planned record, or (None, None, None)."""
        for key, record in self._records.items():
            if record["entry"]["guid"] == guid:
                return key, record["texture"], record["entry"]
        return None, None, None

    def discard(self, key):
        """MARK a request as replaced by channel splits; pruned at end of walk.

        This used to pop the record immediately, which was order-dependent
        and wrong: the bank dedupes by (texture, role), so when material A
        (roughness only -> whole-texture request) classified BEFORE material B
        (same texture on three roles -> split), B's discard removed the very
        record A's already-emitted spec referenced. A's manifest guid then
        pointed at a texture that was never exported -- and whether that
        happened depended on nothing but classification order. The record is
        only dropped if, once EVERY material is classified, no spec still
        references it (`prune_unreferenced`).
        """
        self._discarded = getattr(self, "_discarded", set())
        self._discarded.add(key)

    def prune_unreferenced(self, referenced_guids):
        """Drop discard-marked records nothing references. Call after the walk.

        Returns the pruned entries' guids, for the caller's log.
        """
        pruned = []
        for key in sorted(getattr(self, "_discarded", set())):
            record = self._records.get(key)
            if record is None:
                continue
            if record["entry"]["guid"] in referenced_guids:
                continue
            pruned.append(record["entry"]["guid"])
            del self._records[key]
        self._discarded = set()
        return pruned

    def _run_export_task(self, texture, path):
        task = unreal.AssetExportTask()
        task.object = texture
        task.filename = path
        task.automated = True
        task.replace_identical = True
        task.prompt = False
        return (unreal.Exporter.run_asset_export_task(task)
                and os.path.exists(path)
                and os.path.getsize(path) > 0)

    def _export_raw(self, texture, ue_path, raw_path, log=None):
        """Write one raw TGA, falling back through PNG when TGA is refused.

        `UTextureExporterTGA::SupportsObject` accepts only some source formats,
        and a texture it refuses used to end the whole export:

            No tga exporter found for Texture2D .../T_Grunge_06_O
            MaterialExportError: texture export failed: .../T_Grunge_06_O

        Measured across that level's 155 distinct textures
        (Tests/ue/probe_texture_export.py): TGA refused 1, PNG refused 0. So
        the fallback is an export FORMAT, and the PNG is converted back to a
        TGA at the same path rather than being passed through -- the manifest
        was written three steps earlier and already names a `.tga`, and the
        opacity/ORM channel split reads pixels out of the file. Converting
        keeps every one of those unchanged and unaware.
        """
        from . import png

        if self._run_export_task(texture, raw_path):
            return raw_path

        png_path = raw_path[:-len(".tga")] + ".png"
        if not self._run_export_task(texture, png_path):
            raise MaterialExportError(
                "texture export failed as both TGA and PNG: %s. UE refuses "
                "this texture's source format outright; convert it in UE (a "
                "plain 8-bit RGBA source exports cleanly) or exclude it."
                % ue_path)
        png.to_tga(png_path, raw_path)
        os.remove(png_path)
        if log is not None:
            log("  %s: TGA refused by UE, exported as PNG and converted"
                % ue_path)
        return raw_path

    def export_all(self, output_root, raw_root, log=None):
        """Export raw TGAs (one per unique texture) then derive per-role files."""
        from . import tga

        os.makedirs(raw_root, exist_ok=True)
        raw_by_path = {}
        for record in self._records.values():
            ue_path = record["entry"]["ue_path"]
            if ue_path in raw_by_path:
                continue
            # The filename must be injective per SOURCE ASSET. The old
            # flattened path ("/"->"_") mapped /Game/Foo/Bar/T and
            # /Game/Foo_Bar/T onto one file, so whichever exported second
            # silently overwrote the first and both materials split channels
            # out of the SAME raw image. The asset guid is already unique per
            # ue_path, so it disambiguates; the readable stem stays for humans.
            raw_path = os.path.join(
                raw_root, "%s_%s.tga" % (
                    naming.sanitize_path(ue_path).replace("/", "_"),
                    naming.asset_guid(ue_path)[:8]))
            raw_by_path[ue_path] = self._export_raw(
                record["texture"], ue_path, raw_path, log=log)

        exported = []
        for record in self._records.values():
            entry = record["entry"]
            raw = raw_by_path[entry["ue_path"]]
            out_path = os.path.join(output_root, entry["o3de_relative_path"]).replace("\\", "/")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            if entry["channel"] is None:
                tga.copy(raw, out_path)
            else:
                tga.write_channel_png(raw, out_path, entry["channel"])
            exported.append(out_path)
            if log is not None:
                log("  %-52s <- %s%s" % (entry["o3de_relative_path"], entry["ue_path"],
                                         " ch " + entry["channel"] if entry["channel"] else ""))
        return exported


def classify_property(master, instance, prop_enum, bank, role_suffix, warnings, subject):
    """One property -> a manifest spec dict, or None when undriven/unsupported."""
    mel = unreal.MaterialEditingLibrary
    node = mel.get_material_property_input_node(master, prop_enum)
    if node is None:
        return None
    output_name = str(mel.get_material_property_input_node_output_name(master, prop_enum) or "")
    return classify_expression(master, instance, node, output_name, bank,
                               role_suffix, warnings, subject)


def classify_expression(master, instance, node, output_name, bank, role_suffix,
                        warnings, subject, depth=0):
    """Classify an expression feeding a property-shaped input."""
    mel = unreal.MaterialEditingLibrary
    node, channel_hint = _follow(master, node, instance)
    if node is None:
        return None
    if channel_hint and output_name not in ("R", "G", "B", "A"):
        output_name = channel_hint
    kind = _expression_kind(node)

    if kind in _TEXTURE_KINDS:
        texture = _texture_of(node, instance)
        if texture is None:
            warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                         "%s: texture expression with no texture bound" % role_suffix)
            return None
        channel = output_name if output_name in ("R", "G", "B", "A") else None
        if channel is not None and role_suffix in ("basecolor", "normal"):
            channel = None  # RGB roles take the full image
        entry = bank.request(texture, role_suffix, channel)
        return {"source": "texture", "texture_guid": entry["guid"],
                "channel": channel, "factor": None}

    scalar = _scalar_of(node, instance)
    if scalar is not None:
        return {"source": "scalar", "value": scalar}

    color = _color_of(node, instance)
    if color is not None:
        return {"source": "color", "value": color}

    if kind == "MaterialExpressionMaterialFunctionCall":
        # Master materials wrap channels in helper functions (CheapContrast,
        # tint, detail-blend, ...). The function body is not walkable through
        # MEL, but the channel's identity is whatever feeds the call's primary
        # input -- measured shape: CheapContrast_RGB(In=Multiply(texture,...),
        # Contrast=param). So the call's inputs are classified RECURSIVELY,
        # primary-named pins first, and the first texture-yielding one wins;
        # the function's own math is dropped and visibly reported.
        if depth >= 4:
            warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                         "%s: function calls nested deeper than 4" % role_suffix)
            return None
        try:
            names = [str(n) for n in
                     (mel.get_material_expression_input_names(node) or [])]
        except Exception:
            names = []
        try:
            inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
        except Exception:
            inputs = []

        def priority(pair):
            name = pair[0].lower()
            for rank, key in enumerate(("in", "input", "texture", "base", "albedo", "a")):
                if name == key:
                    return rank
            return 99

        pairs = sorted(zip(names + ["?"] * (len(inputs) - len(names)), inputs),
                       key=priority)
        function_asset = node.get_editor_property("material_function")
        function_name = function_asset.get_name() if function_asset else "function"

        from .warnings import Warnings as _ScratchWarnings
        fallback_spec = None
        for name, part in pairs:
            if part is None:
                continue
            scratch = _ScratchWarnings()
            spec = classify_expression(master, instance, part, output_name, bank,
                                       role_suffix, scratch, subject, depth + 1)
            if spec is not None and spec.get("source") == "texture":
                warnings.add("MAT_FUNCTION_PASSTHROUGH", subject,
                             "%s: %s approximated by its %r input"
                             % (role_suffix, function_name, name))
                return spec
            if spec is not None and fallback_spec is None and priority((name, part)) < 99:
                fallback_spec = (spec, name)
        if fallback_spec is not None:
            spec, name = fallback_spec
            warnings.add("MAT_FUNCTION_PASSTHROUGH", subject,
                         "%s: %s approximated by its %r input (non-texture)"
                         % (role_suffix, function_name, name))
            return spec
        texture_node, hint = _find_texture(master, node, instance)
        if texture_node is not None:
            texture = _texture_of(texture_node, instance)
            if texture is not None:
                channel = output_name if output_name in ("R", "G", "B", "A") else hint
                if channel is not None and role_suffix in ("basecolor", "normal"):
                    channel = None
                warnings.add("MAT_FUNCTION_PASSTHROUGH", subject,
                             "%s: %s approximated by the nearest texture beneath it"
                             % (role_suffix, function_name))
                entry = bank.request(texture, role_suffix, channel)
                return {"source": "texture", "texture_guid": entry["guid"],
                        "channel": channel, "factor": None}
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "%s driven by %s with no classifiable input"
                     % (role_suffix, function_name))
        return None

    if kind == "MaterialExpressionMultiply":
        inputs = mel.get_inputs_for_material_expression(master, node)
        # Follow wrappers on each operand too: real masters put Reroutes and
        # decided switches between the Multiply and its texture.
        parts = [_follow(master, p, instance)[0]
                 for p in (inputs or []) if p is not None]
        texture_node = next((p for p in parts if p is not None
                             and _expression_kind(p) in _TEXTURE_KINDS), None)
        if texture_node is None:
            # An operand may itself be a wrapper function around the texture
            # (normal-flatten helpers etc.); recurse through the classifier,
            # which handles the single-texture-input passthrough.
            for part in parts:
                if part is not None and _expression_kind(part) == "MaterialExpressionMaterialFunctionCall":
                    spec = classify_expression(master, instance, part, output_name,
                                               bank, role_suffix, warnings, subject, depth + 1)
                    if spec is not None and spec.get("source") == "texture":
                        return spec
        factor_node = next((p for p in parts if p is not None
                            and (_scalar_of(p, instance) is not None
                                 or _color_of(p, instance) is not None)), None)
        if texture_node is not None:
            texture = _texture_of(texture_node, instance)
            if texture is not None:
                entry = bank.request(texture, role_suffix, None)
                factor = None
                if factor_node is not None:
                    factor = _scalar_of(factor_node, instance)
                    if factor is None:
                        factor = _color_of(factor_node, instance)
                return {"source": "texture", "texture_guid": entry["guid"],
                        "channel": None, "factor": factor}
        texture_node, hint = _find_texture(master, node, instance)
        if texture_node is not None:
            texture = _texture_of(texture_node, instance)
            if texture is not None:
                channel = output_name if output_name in ("R", "G", "B", "A") else hint
                if channel is not None and role_suffix in ("basecolor", "normal"):
                    channel = None
                warnings.add("MAT_FUNCTION_PASSTHROUGH", subject,
                             "%s: Multiply approximated by the nearest texture "
                             "beneath it" % role_suffix)
                entry = bank.request(texture, role_suffix, channel)
                return {"source": "texture", "texture_guid": entry["guid"],
                        "channel": channel, "factor": None}
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "%s: Multiply without a recognizable texture*constant shape"
                     % role_suffix)
        return None

    # Last resort before dropping the channel: the nearest texture in the
    # subtree, with the surrounding math dropped and reported. A channel with
    # no texture anywhere beneath it stays unmapped.
    texture_node, hint = _find_texture(master, node, instance)
    if texture_node is not None:
        texture = _texture_of(texture_node, instance)
        if texture is not None:
            channel = output_name if output_name in ("R", "G", "B", "A") else hint
            if channel is not None and role_suffix in ("basecolor", "normal"):
                channel = None
            warnings.add("MAT_FUNCTION_PASSTHROUGH", subject,
                         "%s: %s approximated by the nearest texture beneath it"
                         % (role_suffix, kind))
            entry = bank.request(texture, role_suffix, channel)
            return {"source": "texture", "texture_guid": entry["guid"],
                    "channel": channel, "factor": None}

    warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                 "%s driven by %s" % (role_suffix, kind))
    return None


# Manifest key -> the role name used in PACKED_CHANNEL_ORDER.
_PACKABLE_KEYS = {"roughness": "roughness", "metallic": "metallic",
                  "occlusion": "ao"}


def split_shared_packed_texture(properties, bank, warnings, subject):
    """One texture feeding several of roughness/metallic/AO is a PACKED map.

    THE BUG THIS FIXES, measured on Vehicles/VOL4_RetroCars: roughness,
    metallic and occlusion all resolved to `TX_Car_24a_RMA` with NO channel
    hint, so each was exported WHOLE. The three files came out byte-identical
    (same MD5, 50,331,666 bytes each -- three 48 MB copies of one image), and
    Atom then read the same data for all three. Metallic and AO were being
    driven by the roughness map; the cars rendered dark and wrongly reflective.

    It reaches here and not the name-heuristic path because the graph WAS
    walkable: `_follow` found a texture for each property but there was no
    ComponentMask between them, so `channel_hint` stayed None three times over.
    Nothing downstream could tell that one texture was doing three jobs.

    The tell is unambiguous -- the SAME texture guid on two or more of those
    three roles, none of them channel-masked. No single-role material can look
    like that, so this cannot misfire on a mesh that genuinely uses one map for
    one job. Which channel carries which role comes from the packing token in
    the texture's own name (param_roles.PACKED_CHANNEL_ORDER); an unlabelled
    one falls back to ORM and says so.
    """
    # GROUPED BY THE SOURCE TEXTURE, NOT BY THE GUID. `TextureBank.request`
    # derives the guid from `ue_path + "#" + role`, so one texture requested
    # for roughness, metallic and AO comes back as THREE DIFFERENT guids --
    # which is exactly why the three exports had different filenames and
    # identical bytes. Grouping on guid finds nothing, every time.
    shared = {}
    resolved = {}
    for key, role in _PACKABLE_KEYS.items():
        spec = properties.get(key)
        if not spec or spec.get("source") != "texture" or spec.get("channel"):
            continue
        bank_key, texture, entry = bank.find_by_guid(spec["texture_guid"])
        if texture is None:
            continue
        resolved[key] = (bank_key, texture, entry)
        shared.setdefault(entry.get("ue_path"), []).append((key, role))

    for ue_path, members in shared.items():
        if len(members) < 2:
            continue
        bank_key, texture, entry = resolved[members[0][0]]
        order, token = param_roles.packed_channel_order(entry.get("name") or "")
        role_to_channel = {role: channel for channel, role in order.items()}
        for key, role in members:
            channel = role_to_channel.get(role)
            if channel is None:
                continue
            split = bank.request(texture, role, channel)
            properties[key] = dict(properties[key],
                                   texture_guid=split["guid"], channel=channel)
        # Each role had its OWN whole-texture request (one guid per role), so
        # every one of them has to be dropped or the full 48 MB copies still
        # get written and the manifest carries textures nothing references.
        for key, _role in members:
            member_key = resolved[key][0]
            if member_key is not None:
                bank.discard(member_key)
        warnings.add(
            "MAT_PACKED_TEXTURE_SPLIT", subject,
            "%s drives %s from one texture with no channel mask, so it is a "
            "packed map; split as %s%s"
            % (entry.get("name"), "/".join(key for key, _r in members),
               token.upper() if token else "ORM",
               "" if token else " (no ORM/ARM/RMA/MRA token in the name -- "
                               "assumed; if it packs differently these are swapped)"))


# MakeMaterialAttributes input names -> (manifest key, role suffix)
_ATTRIBUTE_INPUTS = {
    "BaseColor": ("base_color", "basecolor"),
    "Normal": ("normal", "normal"),
    "Roughness": ("roughness", "roughness"),
    "Metallic": ("metallic", "metallic"),
    "AmbientOcclusion": ("occlusion", "ao"),
    "Opacity": ("opacity", "opacity"),
    "OpacityMask": ("opacity_mask", "opacity"),
}


# Function-call input pins that pass MaterialAttributes through: taking this
# branch keeps the BASE surface and drops the function's blend/overlay math.
_ATTRIBUTES_PASSTHROUGH_PINS = ("basematerial", "material", "base", "input", "in", "a")


def _classify_material_attributes(master, instance, bank, warnings, subject):
    """Properties of a use_material_attributes master.

    Follows the attributes pin through wrappers. Three shapes convert, tried
    in order (each measured on real content):

      1. MakeMaterialAttributes -> per-attribute classification (MM_Master);
      2. a material-function CALL with an attributes pass-through pin
         (MM_Building: MF_MaterialBlend's `BaseMaterial`) -> unwrap it,
         report the dropped blend, repeat;
      3. a call with NO classifiable pins (MF_BaseMaterial_Simple: its
         textures are parameter nodes INSIDE the function, unreachable from
         Python) -> classify from the master's flat texture PARAMETER LIST
         by role-shaped names (param_roles.py), values from the instance.

    Anything else is reported and the material falls back to default.
    """
    mel = unreal.MaterialEditingLibrary
    prop = getattr(unreal.MaterialProperty, "MP_MATERIAL_ATTRIBUTES", None)
    node = mel.get_material_property_input_node(master, prop) if prop else None
    node, _hint = _follow(master, node, instance)

    for _bound in range(8):
        if node is None or _expression_kind(node) != "MaterialExpressionMaterialFunctionCall":
            break
        function_asset = node.get_editor_property("material_function")
        function_name = function_asset.get_name() if function_asset else "function"
        try:
            names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
            inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
        except Exception:
            names, inputs = [], []
        passthrough = None
        for name, expression in zip(names, inputs):
            if expression is None:
                continue
            if name.lower().replace(" ", "") in _ATTRIBUTES_PASSTHROUGH_PINS:
                passthrough = (name, expression)
                break
        if passthrough is None:
            # Dead end at a bare call: the parameter-name fallback.
            return _classify_by_parameter_names(master, instance, bank,
                                                warnings, subject, function_name)
        warnings.add("MAT_FUNCTION_PASSTHROUGH", subject,
                     "attributes: %s approximated by its %r input (its "
                     "blend/overlay math is dropped)"
                     % (function_name, passthrough[0]))
        node, _hint = _follow(master, passthrough[1], instance)

    if node is None or _expression_kind(node) != "MaterialExpressionMakeMaterialAttributes":
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "material attributes pin driven by %s"
                     % (_expression_kind(node) if node else "nothing"))
        return {}

    names = [str(n) for n in (mel.get_material_expression_input_names(node) or [])]
    inputs = list(mel.get_inputs_for_material_expression(master, node) or [])
    properties = {}
    for name, expression in zip(names, inputs):
        mapping = _ATTRIBUTE_INPUTS.get(name)
        if mapping is None or expression is None:
            continue
        key, role = mapping
        spec = classify_expression(master, instance, expression, "", bank, role,
                                   warnings, subject)
        if spec is not None:
            properties[key] = spec
    return properties


def _resolve_texture_parameter(master, instance, name):
    """The texture bound to parameter `name`, instance value first."""
    mel = unreal.MaterialEditingLibrary
    if instance is not None:
        try:
            value = _instance_texture(instance, name)
            if value is not None:
                return value
        except Exception:
            pass
    for api in ("get_material_default_texture_parameter_value",
                "get_texture_parameter_default_value"):
        try:
            value = getattr(mel, api)(master, name)
            if value is not None:
                return value
        except Exception:
            continue
    return None


def _classify_by_parameter_names(master, instance, bank, warnings, subject,
                                 function_name):
    """Last-resort classification from the master's texture parameter names.

    See param_roles.py for the (pure, tested) name->role rules. An ORM-named
    parameter expands to the packed-texture channel split M4 already does for
    explicit ORM graphs (R -> occlusion, G -> roughness, B -> metallic).
    """
    mel = unreal.MaterialEditingLibrary
    try:
        names = [str(n) for n in (mel.get_texture_parameter_names(master) or [])]
    except Exception as exc:
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "attributes end in %s and texture parameters are not "
                     "enumerable (%s)" % (function_name, type(exc).__name__))
        return {}

    roles = param_roles.pick_parameter_roles(names)
    if not roles:
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "attributes end in %s (no classifiable pins) and no "
                     "texture parameter name matches a role; parameters: %s"
                     % (function_name, ", ".join(sorted(names)) or "none"))
        return {}

    properties = {}

    def request(parameter_name, role, channel, key):
        texture = _resolve_texture_parameter(master, instance, parameter_name)
        if texture is None:
            return
        entry = bank.request(texture, role, channel)
        properties[key] = {"source": "texture", "texture_guid": entry["guid"],
                           "channel": channel, "factor": None}

    if "basecolor" in roles:
        request(roles["basecolor"], "basecolor", None, "base_color")
    if "normal" in roles:
        request(roles["normal"], "normal", None, "normal")
    if "orm" in roles:
        # THE TOKEN NAMES THE ORDER. ORM/ARM/RMA/MRA pack the same three maps
        # in different channels, and this used to split all four as ORM --
        # correct for two of them and wrong on all three channels for the
        # other two. See param_roles.PACKED_CHANNEL_ORDER for the measurement.
        order, token = param_roles.packed_channel_order(roles["orm"])
        role_keys = {"ao": "occlusion", "roughness": "roughness",
                     "metallic": "metallic"}
        for channel in ("R", "G", "B"):
            role = order[channel]
            request(roles["orm"], role, channel, role_keys[role])
        if token is None:
            warnings.add(
                "MAT_PACKED_ORDER_ASSUMED", subject,
                "packed texture parameter %r declares no ORM/ARM/RMA/MRA "
                "token, so ORM channel order was assumed; if the source packs "
                "them differently, roughness/metallic/AO are swapped"
                % roles["orm"])
    else:
        if "roughness" in roles:
            request(roles["roughness"], "roughness", None, "roughness")
        if "metallic" in roles:
            request(roles["metallic"], "metallic", None, "metallic")
        if "ao" in roles:
            request(roles["ao"], "ao", None, "occlusion")

    if properties:
        warnings.add("MAT_PARAMS_BY_NAME", subject,
                     "%s exposes no walkable graph; classified from parameter "
                     "names %s -- role assignment is heuristic"
                     % (function_name,
                        ", ".join("%s=%r" % (r, n) for r, n in sorted(roles.items()))))
    else:
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "attributes end in %s; role-named parameters exist (%s) "
                     "but none resolved to a texture"
                     % (function_name, ", ".join(sorted(roles.values()))))
    return properties


def build_material_data(material, bank, warnings):
    """Classify one material (or instance). Returns a manifest dict or None.

    None means "leave the entities on the default material": emitted when the
    base colour channel cannot be mapped -- a material that renders with the
    wrong albedo is worse than a visibly grey one.
    """
    master, instance = _base_material_and_instance(material)
    if master is None:
        return None
    subject = naming.package_path(unreal.SystemLibrary.get_path_name(material))

    raw_blend = str(master.get_editor_property("blend_mode")).split(".")[-1].split(":")[0]
    blend = _BLEND_MODES.get(raw_blend)
    if blend is None:
        warnings.add("MAT_BLEND_UNSUPPORTED", subject,
                     "blend mode %s; treated as translucent" % raw_blend)
        blend = "translucent"

    properties = {}
    enum = unreal.MaterialProperty
    if bool(master.get_editor_property("use_material_attributes")):
        # The master feeds everything through the single MaterialAttributes
        # pin (measured on real content: MakeMaterialAttributes behind Reroute
        # and StaticSwitch wrappers). Individual property inputs are all empty
        # in this mode, so the attribute node's named inputs are classified
        # instead.
        properties = _classify_material_attributes(
            master, instance, bank, warnings, subject)
    else:
        for enum_name, key, role in _PROPERTIES:
            prop = getattr(enum, enum_name, None)
            if prop is None:
                continue
            spec = classify_property(master, instance, prop, bank, role, warnings, subject)
            if spec is not None:
                properties[key] = spec

    # BOTH branches, one call. This ran only on the use_material_attributes
    # path at first, so a PLAIN master driving roughness/metallic/AO from one
    # unmasked texture still exported three byte-identical full-RGB TGAs with
    # the wrong data in every slot -- the exact RetroCars failure, reachable
    # by the other road. No-op-safe: single-role and already-channelled specs
    # pass through untouched.
    split_shared_packed_texture(properties, bank, warnings, subject)

    if not properties:
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "no property could be mapped; entities keep the default material")
        return None

    if "base_color" not in properties:
        driven = unreal.MaterialEditingLibrary.get_material_property_input_node(
            master, enum.MP_BASE_COLOR) is not None
        if driven:
            # base colour exists but is unmappable: default material, loudly.
            warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                         "base colour unmappable; entities keep the default material")
            return None

    return {
        "blend_mode": blend,
        "two_sided": bool(master.get_editor_property("two_sided")),
        "properties": properties,
    }

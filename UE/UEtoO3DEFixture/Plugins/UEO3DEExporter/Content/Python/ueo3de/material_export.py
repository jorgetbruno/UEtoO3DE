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
Metallic/B) are exported once raw, then split into grayscale TGAs by
`tga.write_grayscale_from_channel` -- pure Python, testable offline (plan M4:
"simpler and more testable than channel-selection plumbing").

Normal maps: UE authors DirectX-style (green down); the manifest records
`flip_y: true` and the importer sets StandardPBR's `normal.flipY` (plan M4:
"a silent, ugly failure otherwise").
"""

import os

import unreal

from . import naming

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
                  "MaterialExpressionTextureSampleParameter2D")
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
        if instance is not None:
            try:
                return bool(unreal.MaterialEditingLibrary
                            .get_material_instance_static_switch_parameter_value(
                                instance, name))
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


def _base_material_and_instance(material):
    """(master Material for graph reading, leaf instance for parameter values)."""
    if isinstance(material, unreal.MaterialInstance):
        base = material.get_base_material()
        return base, material
    return material, None


def _linear_color_to_rgb(value):
    return [float(value.r), float(value.g), float(value.b)]


def _scalar_of(node, instance):
    kind = _expression_kind(node)
    if kind == "MaterialExpressionConstant":
        return float(node.get_editor_property("r"))
    if kind == "MaterialExpressionScalarParameter":
        name = node.get_editor_property("parameter_name")
        if instance is not None:
            return float(unreal.MaterialEditingLibrary
                         .get_material_instance_scalar_parameter_value(instance, name))
        return float(node.get_editor_property("default_value"))
    return None


def _color_of(node, instance):
    kind = _expression_kind(node)
    if kind in ("MaterialExpressionConstant3Vector", "MaterialExpressionConstant4Vector"):
        return _linear_color_to_rgb(node.get_editor_property("constant"))
    if kind == "MaterialExpressionVectorParameter":
        name = node.get_editor_property("parameter_name")
        if instance is not None:
            return _linear_color_to_rgb(
                unreal.MaterialEditingLibrary
                .get_material_instance_vector_parameter_value(instance, name))
        return _linear_color_to_rgb(node.get_editor_property("default_value"))
    return None


def _texture_of(node, instance):
    kind = _expression_kind(node)
    if kind == "MaterialExpressionTextureSample":
        return node.get_editor_property("texture")
    if kind == "MaterialExpressionTextureSampleParameter2D":
        name = node.get_editor_property("parameter_name")
        if instance is not None:
            found = (unreal.MaterialEditingLibrary
                     .get_material_instance_texture_parameter_value(instance, name))
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
        relative = "%s_%s.tga" % (stem, role_suffix)
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

    def export_all(self, output_root, raw_root, log=None):
        """Export raw TGAs (one per unique texture) then derive per-role files."""
        from . import tga

        os.makedirs(raw_root, exist_ok=True)
        raw_by_path = {}
        for record in self._records.values():
            ue_path = record["entry"]["ue_path"]
            if ue_path in raw_by_path:
                continue
            raw_path = os.path.join(
                raw_root, naming.sanitize_path(ue_path).replace("/", "_") + ".tga")
            task = unreal.AssetExportTask()
            task.object = record["texture"]
            task.filename = raw_path
            task.automated = True
            task.replace_identical = True
            task.prompt = False
            if not unreal.Exporter.run_asset_export_task(task) or not os.path.exists(raw_path):
                raise MaterialExportError("texture export failed: " + ue_path)
            raw_by_path[ue_path] = raw_path

        exported = []
        for record in self._records.values():
            entry = record["entry"]
            raw = raw_by_path[entry["ue_path"]]
            out_path = os.path.join(output_root, entry["o3de_relative_path"]).replace("\\", "/")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            if entry["channel"] is None:
                tga.copy(raw, out_path)
            else:
                tga.write_grayscale_from_channel(raw, out_path, entry["channel"])
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
                        warnings, subject):
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

    if kind == "MaterialExpressionMultiply":
        inputs = mel.get_inputs_for_material_expression(master, node)
        # Follow wrappers on each operand too: real masters put Reroutes and
        # decided switches between the Multiply and its texture.
        parts = [_follow(master, p, instance)[0]
                 for p in (inputs or []) if p is not None]
        texture_node = next((p for p in parts if p is not None
                             and _expression_kind(p) in _TEXTURE_KINDS), None)
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
        warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                     "%s: Multiply without a recognizable texture*constant shape"
                     % role_suffix)
        return None

    warnings.add("MAT_EXPR_UNSUPPORTED", subject,
                 "%s driven by %s" % (role_suffix, kind))
    return None


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


def _classify_material_attributes(master, instance, bank, warnings, subject):
    """Properties of a use_material_attributes master.

    Follows the attributes pin through wrappers; a MakeMaterialAttributes node
    yields per-attribute classification, anything else is reported and the
    material falls back to default (base colour unresolvable).
    """
    mel = unreal.MaterialEditingLibrary
    prop = getattr(unreal.MaterialProperty, "MP_MATERIAL_ATTRIBUTES", None)
    node = mel.get_material_property_input_node(master, prop) if prop else None
    node, _hint = _follow(master, node, instance)
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

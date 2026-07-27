"""
material_build.py — manifest material_data -> StandardPBR `.material` JSON (M4).

PURE. Every property name below was read from the StandardPBR materialtype's
own property groups in the 26.05 SDK (BaseColor/Normal/Roughness/Metallic/
Occlusion/Opacity/GeneralCommon PropertyGroup.json), not guessed.

Decisions that encode plan requirements:

  * `normal.flipY: true` on every normal map -- UE authors DirectX-style
    (green down), Atom samples OpenGL-style. "A silent, ugly failure
    otherwise" (plan M4), and the acceptance test asserts the flag.
  * Blend modes: opaque -> Opaque (omitted; it is the default), masked ->
    Cutout, translucent -> Blended.
  * Alpha comes from a SPLIT file: the exporter extracts the UE texture's
    alpha into its own grayscale `_opacity.tga`, so `opacity.alphaSource` is
    "Split" with `opacity.textureMap` pointing at it.
  * `opacity.factor` defaults to 0.5 in the material type -- correct for
    nothing we import -- so it is set explicitly: 1.0 under a texture, the
    scalar value when UE drove opacity with a constant.
  * Texture references are RELATIVE TO THE .material FILE (`./`-style), the
    engine's own convention in shipped examples and immune to scan-folder
    ambiguity.

A material whose `material_data` is null never reaches this module: those
entities keep the backend's default material by design.
"""

import json
import os
import posixpath

MATERIAL_TYPE = "Materials/Types/StandardPBR.materialtype"
MATERIAL_TYPE_VERSION = 5


class MaterialBuildError(Exception):
    pass


def _relative_reference(material_relative_path, texture_relative_path):
    """Path from the .material file's folder to the texture, POSIX separators."""
    origin = posixpath.dirname(material_relative_path)
    return posixpath.relpath(texture_relative_path, origin)


def _texture_path(spec, assets_by_guid, material_path, subject, key):
    guid = spec.get("texture_guid")
    target = assets_by_guid.get(guid)
    if target is None or target.get("kind") != "texture":
        raise MaterialBuildError(
            "%s: %s references texture %r which is not in the manifest"
            % (subject, key, guid))
    return _relative_reference(material_path, target["o3de_relative_path"])


def build(asset_entry, assets_by_guid):
    """One manifest material entry -> .material document dict."""
    data = asset_entry.get("material_data")
    if not data:
        raise MaterialBuildError(
            "%s has no material_data; entities keep the default material and "
            "no .material file should be written" % asset_entry["ue_path"])

    material_path = asset_entry["o3de_relative_path"]
    subject = asset_entry["ue_path"]
    properties = data.get("properties") or {}
    values = {}

    base = properties.get("base_color")
    if base is not None:
        if base["source"] == "texture":
            values["baseColor.textureMap"] = _texture_path(
                base, assets_by_guid, material_path, subject, "base_color")
            values["baseColor.useTexture"] = True
            factor = base.get("factor")
            if isinstance(factor, (int, float)):
                values["baseColor.factor"] = float(factor)
            elif isinstance(factor, list) and len(factor) == 3:
                values["baseColor.color"] = [float(c) for c in factor] + [1.0]
        elif base["source"] == "color":
            values["baseColor.color"] = [float(c) for c in base["value"]] + [1.0]
        elif base["source"] == "scalar":
            grey = float(base["value"])
            values["baseColor.color"] = [grey, grey, grey, 1.0]

    normal = properties.get("normal")
    if normal is not None and normal["source"] == "texture":
        values["normal.textureMap"] = _texture_path(
            normal, assets_by_guid, material_path, subject, "normal")
        values["normal.useTexture"] = True
        # UE normal maps are DirectX-style (green down); Atom samples the
        # OpenGL convention. Without this every surface lights inside-out.
        values["normal.flipY"] = True

    for key, prefix in (("roughness", "roughness"), ("metallic", "metallic")):
        spec = properties.get(key)
        if spec is None:
            continue
        if spec["source"] == "texture":
            values[prefix + ".textureMap"] = _texture_path(
                spec, assets_by_guid, material_path, subject, key)
            values[prefix + ".useTexture"] = True
        elif spec["source"] == "scalar":
            values[prefix + ".factor"] = float(spec["value"])
            values[prefix + ".useTexture"] = False

    occlusion = properties.get("occlusion")
    if occlusion is not None and occlusion["source"] == "texture":
        values["occlusion.diffuseTextureMap"] = _texture_path(
            occlusion, assets_by_guid, material_path, subject, "occlusion")
        values["occlusion.diffuseUseTexture"] = True

    blend = data.get("blend_mode", "opaque")
    opacity_spec = properties.get("opacity_mask") or properties.get("opacity")
    if blend == "masked":
        values["opacity.mode"] = "Cutout"
    elif blend == "translucent":
        values["opacity.mode"] = "Blended"
    if blend in ("masked", "translucent"):
        if opacity_spec is not None and opacity_spec["source"] == "texture":
            values["opacity.alphaSource"] = "Split"
            values["opacity.textureMap"] = _texture_path(
                opacity_spec, assets_by_guid, material_path, subject, "opacity")
            values["opacity.factor"] = 1.0
        elif opacity_spec is not None and opacity_spec["source"] == "scalar":
            values["opacity.alphaSource"] = "None"
            values["opacity.factor"] = float(opacity_spec["value"])
        else:
            values["opacity.factor"] = 1.0

    if data.get("two_sided"):
        values["general.doubleSided"] = True

    return {
        "materialType": MATERIAL_TYPE,
        "materialTypeVersion": MATERIAL_TYPE_VERSION,
        "propertyValues": values,
    }


def write(asset_entry, assets_by_guid, project_assets_root):
    """Write the .material file into the project. Returns its absolute path."""
    document = build(asset_entry, assets_by_guid)
    path = os.path.join(project_assets_root,
                        asset_entry["o3de_relative_path"]).replace("\\", "/")
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path

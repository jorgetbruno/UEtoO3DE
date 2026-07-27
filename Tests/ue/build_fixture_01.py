"""
build_fixture_01.py — builds the Fixture_01 acceptance level for UEtoO3DE (plan v2.2, M0).

Creates (deterministically — no randomness, no wall-clock anywhere):
  Assets:
    /Game/Textures/T_Fixture_BaseColor   (256x256 white, sRGB, default compression)
    /Game/Textures/T_Fixture_Normal      (sRGB off, TC_NORMALMAP)
    /Game/Textures/T_Fixture_ORM         (sRGB off, linear masks)
    /Game/Textures/T_Fixture_Scalar      (sRGB off, linear)
    /Game/Materials/M_Fixture_PBR        (TextureSamples only: BaseColor/Normal/Roughness/Metallic)
    /Game/Materials/M_Fixture_ORM        (one packed ORM texture, R->AO, G->Roughness, B->Metallic)
    /Game/Materials/M_Fixture_Masked     (BlendMode = Masked, alpha -> OpacityMask)
    /Game/Materials/M_Fixture_Translucent(BlendMode = Translucent, alpha -> Opacity)
    /Game/Materials/M_Fixture_Unsupported(Lerp driven by VertexColor -> warnings path in M4)
    /Game/Meshes/SM_LetterF              (asymmetric static mesh, 3 boxes via Geometry Scripting)
    /Game/Meshes/SM_TwoTone              (TWO material slots: ID0 base box -> M_Fixture_PBR,
                                          ID1 top box -> M_Fixture_ORM; per-slot fidelity canary)
  Level: /Game/Maps/Fixture_01 (non World-Partition)

Deterministic layout (UE cm, Z-up, left-handed):
    Fixture_Floor        Plane   (0,0,0)            scale (10,10,1)   static
    Prim_Box             Cube    (300,0,25)         scale (2,1,0.5)   static   M_Fixture_PBR        (non-uniform scale actor)
    Prim_Sphere          Sphere  (600,0,50)         scale (1,1,1)     static   M_Fixture_ORM
    Prim_Cylinder        Cylinder(900,0,50)         scale (1,1,1)     static   M_Fixture_Masked
    SM_LetterF           LetterF (1500,0,0)         identity rot      static   (asset default material)
    SM_TwoTone           TwoTone (1800,0,0)         identity rot      static   (asset slots: PBR + ORM)
    RotatedParent_Cube   Cube    (0,400,50)         rot (P0,Y45,R0)   static   M_Fixture_Translucent
    RotatedChild_Sphere  Sphere  rel (150,0,50)     rel rot (P30,Y0)  static   M_Fixture_Unsupported (attached to RotatedParent_Cube)
    Cube_Dynamic         Cube    (-300,0,100)       movable, Simulate Physics = true
    Cube_Kinematic       Cube    (-600,0,100)       movable, collision on, Simulate Physics = false
    TriggerBox_01        TriggerBox (-900,0,100)    overlap events on
    Light_Point          (300,300,250)   intensity 12.5 cd, color (1.0,0.6,0.3), attenuation 600
    Light_Spot           (700,300,400)   rot (P-45,Y-90), 40 cd, color (0.4,0.7,1.0), cone 15/30, att 1000
    Light_Directional    (0,0,600)       rot (P-50,Y30), 5.0 lux, color (1.0,0.95,0.85)
    Atmo_SkyLight        (0,0,200)       intensity 0.8, real-time capture
    Atmo_HeightFog       (0,0,0)         density 0.05, falloff 0.2
    PPV_01               (0,0,0)         unbound (infinite extent)

SM_LetterF geometry (asset space, cm, overall 100 x 50 x 200):
    stem:       box  25x25x200 at (-37.5,  0.0,   0)  -> X -50..-25, Y -12.5..12.5, Z   0..200
    top arm:    box 100x25x30  at (  0.0,  0.0, 170)  -> X -50.. 50, Y -12.5..12.5, Z 170..200
    middle arm: box  75x25x25  at (-12.5,  0.0, 100)  -> X -50.. 25, Y -12.5..12.5, Z 100..125
    side nub:   box  25x25x25  at (-37.5, 25.0, 175)  -> X -50..-25, Y  12.5..37.5, Z 175..200

`GeometryScriptPrimitiveOriginMode.BASE` centers a box in X and Y and bases it
in Z, so each box spans loc +/- dim/2 horizontally. The arms are therefore
offset in X rather than sharing a center, and the nub breaks the Y symmetry a
flat letter would otherwise have.

Asymmetric about ALL THREE planes, which is the entire point of this mesh
(plan M0): a mirrored level passes every other assertion in the fixture because
boxes, spheres and cylinders are all mirror-symmetric. The measurable property
is the vertex centroid's offset from the bounding-box center -- (-21.875,
6.25, 46.25) cm -- since the X bounds are symmetric by design (the top arm
spans the full width). Y-asymmetry matters most of all: Lane A's basis map
negates Y, so a mesh symmetric in Y cannot detect the case where the geometry
lane fails to apply the same reflection.

Run:  run_ue_python.bat build_fixture_01.py
Idempotent: existing assets are deleted/rebuilt before creation.
"""

import traceback

import unreal


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

MAP_PATH = "/Game/Maps/Fixture_01"
MESHES_DIR = "/Game/Meshes"
MATERIALS_DIR = "/Game/Materials"
TEXTURES_DIR = "/Game/Textures"

SM_LETTERF_PATH = MESHES_DIR + "/SM_LetterF"
SM_TWOTONE_PATH = MESHES_DIR + "/SM_TwoTone"

ENGINE_CUBE = "/Engine/BasicShapes/Cube.Cube"
ENGINE_SPHERE = "/Engine/BasicShapes/Sphere.Sphere"
ENGINE_CYLINDER = "/Engine/BasicShapes/Cylinder.Cylinder"
ENGINE_PLANE = "/Engine/BasicShapes/Plane.Plane"

RESULT_TAG = "BUILD_FIXTURE_01"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def log(msg):
    unreal.log("[" + RESULT_TAG + "] " + str(msg))


def ensure_dir(path):
    if not unreal.EditorAssetLibrary.does_directory_exist(path):
        unreal.EditorAssetLibrary.make_directory(path)


def delete_asset_if_exists(path):
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)


def save_asset(path):
    if not unreal.EditorAssetLibrary.save_asset(path):
        raise RuntimeError("failed to save asset: " + path)


def _unwrap(result):
    """UE Python packs UFUNCTION return values as (return_value, out_param1, ...).
    Geometry Script functions carry an extra EGeometryScriptOutcomePins out pin
    (ExpandEnumAsExecs); this returns the first element that is not an outcome enum."""
    if isinstance(result, tuple):
        for item in result:
            if not isinstance(item, unreal.GeometryScriptOutcomePins):
                return item
        return result[0]
    return result


# ---------------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------------

def create_texture(name, srgb, compression):
    """Create a real, savable 256x256 solid-white Texture2D via UTexture2DFactoryNew
    (verified: UnrealEd EditorFactories.cpp — Init2DWithMipChain + white memset).
    Pixel content is irrelevant to the fixture; only asset existence + settings matter."""
    path = TEXTURES_DIR + "/" + name
    delete_asset_if_exists(path)
    factory = unreal.Texture2DFactoryNew()
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    tex = asset_tools.create_asset(name, TEXTURES_DIR, unreal.Texture2D, factory)
    if tex is None:
        raise RuntimeError("failed to create texture asset: " + path)
    # set_editor_property triggers Pre/PostEditChange in the Python wrapper,
    # so compression/sRGB changes are applied to the texture resource.
    tex.set_editor_property("srgb", srgb)
    tex.set_editor_property("compression_settings", compression)
    save_asset(path)
    return tex


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

def _new_material(name):
    path = MATERIALS_DIR + "/" + name
    delete_asset_if_exists(path)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    mat = asset_tools.create_asset(name, MATERIALS_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError("failed to create material asset: " + path)
    return mat, path


def _tex_sample(mat, texture, x, y):
    expr = unreal.MaterialEditingLibrary.create_material_expression(
        mat, unreal.MaterialExpressionTextureSample, x, y)
    expr.set_editor_property("texture", texture)
    return expr


def _connect_prop(expr, output_name, material_property):
    unreal.MaterialEditingLibrary.connect_material_property(expr, output_name, material_property)


def build_materials(tex_basecolor, tex_normal, tex_orm, tex_scalar):
    mel = unreal.MaterialEditingLibrary

    # --- M_Fixture_PBR: TextureSamples only (the M4 recognized subset) ---
    mat, path = _new_material("M_Fixture_PBR")
    ts_base = _tex_sample(mat, tex_basecolor, -600, 0)
    ts_norm = _tex_sample(mat, tex_normal, -600, 300)
    ts_rough = _tex_sample(mat, tex_scalar, -600, 600)
    ts_metal = _tex_sample(mat, tex_scalar, -600, 900)
    _connect_prop(ts_base, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    _connect_prop(ts_norm, "RGB", unreal.MaterialProperty.MP_NORMAL)
    _connect_prop(ts_rough, "R", unreal.MaterialProperty.MP_ROUGHNESS)
    _connect_prop(ts_metal, "R", unreal.MaterialProperty.MP_METALLIC)
    mel.recompile_material(mat)
    save_asset(path)

    # --- M_Fixture_ORM: one packed ORM texture (R=AO, G=Roughness, B=Metallic) ---
    mat, path = _new_material("M_Fixture_ORM")
    ts_orm = _tex_sample(mat, tex_orm, -600, 0)
    _connect_prop(ts_orm, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    _connect_prop(ts_orm, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
    _connect_prop(ts_orm, "G", unreal.MaterialProperty.MP_ROUGHNESS)
    _connect_prop(ts_orm, "B", unreal.MaterialProperty.MP_METALLIC)
    mel.recompile_material(mat)
    save_asset(path)

    # --- M_Fixture_Masked ---
    mat, path = _new_material("M_Fixture_Masked")
    ts = _tex_sample(mat, tex_basecolor, -600, 0)
    _connect_prop(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    _connect_prop(ts, "A", unreal.MaterialProperty.MP_OPACITY_MASK)
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
    mel.recompile_material(mat)
    save_asset(path)

    # --- M_Fixture_Translucent ---
    mat, path = _new_material("M_Fixture_Translucent")
    ts = _tex_sample(mat, tex_basecolor, -600, 0)
    _connect_prop(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    _connect_prop(ts, "A", unreal.MaterialProperty.MP_OPACITY)
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    mel.recompile_material(mat)
    save_asset(path)

    # --- M_Fixture_Unsupported: Lerp of vertex color and constants ---
    # Deliberately WITHOUT any texture anywhere beneath BaseColor: M4's
    # texture-DFS approximation legitimately rescues texture-bearing graphs
    # (that is how real foliage converts), so a canary with a texture in its
    # Lerp converts too and tests nothing. Vertex color + constants cannot be
    # approximated by any texture, present or future.
    mat, path = _new_material("M_Fixture_Unsupported")
    vcol = mel.create_material_expression(mat, unreal.MaterialExpressionVertexColor, -900, 0)
    const_b = mel.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -900, 300)
    const_b.set_editor_property("constant", unreal.LinearColor(0.0, 0.5, 1.0, 1.0))
    alpha = mel.create_material_expression(mat, unreal.MaterialExpressionConstant, -900, 600)
    alpha.set_editor_property("r", 0.5)
    lerp = mel.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -400, 100)
    mel.connect_material_expressions(vcol, "", lerp, "A")
    mel.connect_material_expressions(const_b, "", lerp, "B")
    mel.connect_material_expressions(alpha, "", lerp, "Alpha")
    _connect_prop(lerp, "", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.recompile_material(mat)
    save_asset(path)

    return {
        "pbr": unreal.EditorAssetLibrary.load_asset(MATERIALS_DIR + "/M_Fixture_PBR"),
        "orm": unreal.EditorAssetLibrary.load_asset(MATERIALS_DIR + "/M_Fixture_ORM"),
        "masked": unreal.EditorAssetLibrary.load_asset(MATERIALS_DIR + "/M_Fixture_Masked"),
        "translucent": unreal.EditorAssetLibrary.load_asset(MATERIALS_DIR + "/M_Fixture_Translucent"),
        "unsupported": unreal.EditorAssetLibrary.load_asset(MATERIALS_DIR + "/M_Fixture_Unsupported"),
    }


# ---------------------------------------------------------------------------
# SM_LetterF via Geometry Scripting
# ---------------------------------------------------------------------------

def build_letter_f():
    """Append 4 boxes (Origin=Base -> centered in X/Y, based in Z) and bake to
    a real StaticMesh asset at /Game/Meshes/SM_LetterF.

    Each box is offset so the result is asymmetric about all three planes; see
    the module docstring for the resulting extents and why they matter."""
    delete_asset_if_exists(SM_LETTERF_PATH)

    dyn = unreal.DynamicMesh()
    opts = unreal.GeometryScriptPrimitiveOptions()
    origin = unreal.GeometryScriptPrimitiveOriginMode.BASE

    def box(loc_x, loc_y, loc_z, dim_x, dim_y, dim_z):
        nonlocal dyn
        xform = unreal.Transform(location=unreal.Vector(loc_x, loc_y, loc_z))
        dyn = dyn.append_box(opts, xform, dim_x, dim_y, dim_z, 0, 0, 0, origin)

    box(-37.5, 0.0, 0.0,    25.0, 25.0, 200.0)   # stem       X -50..-25
    box(0.0, 0.0, 170.0,   100.0, 25.0, 30.0)    # top arm    X -50.. 50
    box(-12.5, 0.0, 100.0,  75.0, 25.0, 25.0)    # middle arm X -50.. 25
    box(-37.5, 25.0, 175.0, 25.0, 25.0, 25.0)    # side nub   Y  12.5..37.5

    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    # collision enabled by default (bEnableCollision=true) — keep defaults otherwise.
    result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, SM_LETTERF_PATH, create_opts)
    mesh = _unwrap(result)
    if mesh is None:
        raise RuntimeError("CreateNewStaticMeshAssetFromMesh returned no mesh for SM_LetterF")
    save_asset(SM_LETTERF_PATH)
    return unreal.EditorAssetLibrary.load_asset(SM_LETTERF_PATH)


def build_two_tone(materials):
    """Two boxes with distinct material IDs baked to /Game/Meshes/SM_TwoTone.

    The per-slot fidelity canary (M4): material ID 0 (base box) carries
    M_Fixture_PBR, ID 1 (top box) M_Fixture_ORM, as named entries in the
    asset's static_materials. The export must carry BOTH through the FBX and
    the importer must land each on its own model slot; a single-slot
    regression renders the top box with the base material and fails the
    artifact checks."""
    delete_asset_if_exists(SM_TWOTONE_PATH)

    dyn = unreal.DynamicMesh()
    dyn.enable_material_i_ds()
    opts = unreal.GeometryScriptPrimitiveOptions()
    origin = unreal.GeometryScriptPrimitiveOriginMode.BASE

    xform = unreal.Transform(location=unreal.Vector(0.0, 0.0, 0.0))
    dyn = dyn.append_box(opts, xform, 100.0, 50.0, 100.0, 0, 0, 0, origin)
    base_triangles = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn)
    xform = unreal.Transform(location=unreal.Vector(0.0, 0.0, 150.0))
    dyn = dyn.append_box(opts, xform, 50.0, 50.0, 50.0, 0, 0, 0, origin)
    total_triangles = unreal.GeometryScript_MeshQueries.get_num_triangle_i_ds(dyn)
    for triangle in range(base_triangles, total_triangles):
        dyn.set_triangle_material_id(triangle, 1)

    create_opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    mesh = _unwrap(unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        dyn, SM_TWOTONE_PATH, create_opts))
    if mesh is None:
        raise RuntimeError("CreateNewStaticMeshAssetFromMesh returned no mesh for SM_TwoTone")

    slots = []
    for slot_name, material in (("Base", materials["pbr"]), ("Top", materials["orm"])):
        entry = unreal.StaticMaterial()
        entry.set_editor_property("material_slot_name", slot_name)
        entry.set_editor_property("material_interface", material)
        slots.append(entry)
    mesh.set_editor_property("static_materials", slots)
    save_asset(SM_TWOTONE_PATH)
    return unreal.EditorAssetLibrary.load_asset(SM_TWOTONE_PATH)


# ---------------------------------------------------------------------------
# level construction
# ---------------------------------------------------------------------------

def spawn_static_mesh_actor(actor_sub, label, mesh, location, rotation=(0.0, 0.0, 0.0),
                            scale=None, mobility=unreal.ComponentMobility.STATIC, material=None):
    actor = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(*location), unreal.Rotator(*rotation))
    if actor is None:
        raise RuntimeError("failed to spawn actor: " + label)
    actor.set_actor_label(label)
    smc = actor.static_mesh_component
    smc.set_static_mesh(mesh)
    smc.set_mobility(mobility)
    if scale is not None:
        actor.set_actor_scale3d(unreal.Vector(*scale))
    if material is not None:
        smc.set_material(0, material)
    return actor


def build_level(meshes, materials):
    level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    # Non World-Partition level (plan global constraint 11: WP levels are out of scope).
    # Idempotent rebuild: if the map exists, open it and wipe its actors instead of
    # deleting the asset (deleting an open level is unreliable in -unattended).
    if unreal.EditorAssetLibrary.does_asset_exist(MAP_PATH):
        if not level_sub.load_level(MAP_PATH):
            raise RuntimeError("failed to load existing level " + MAP_PATH)
        actor_sub.destroy_actors(actor_sub.get_all_level_actors())
    else:
        if not level_sub.new_level(MAP_PATH):
            raise RuntimeError("failed to create level " + MAP_PATH)

    movable = unreal.ComponentMobility.MOVABLE

    # --- floor + primitives ---
    spawn_static_mesh_actor(actor_sub, "Fixture_Floor", meshes["plane"], (0.0, 0.0, 0.0), scale=(10.0, 10.0, 1.0))
    spawn_static_mesh_actor(actor_sub, "Prim_Box", meshes["cube"], (300.0, 0.0, 25.0),
                            scale=(2.0, 1.0, 0.5), material=materials["pbr"])   # non-uniform scale actor
    spawn_static_mesh_actor(actor_sub, "Prim_Sphere", meshes["sphere"], (600.0, 0.0, 50.0), material=materials["orm"])
    spawn_static_mesh_actor(actor_sub, "Prim_Cylinder", meshes["cylinder"], (900.0, 0.0, 50.0), material=materials["masked"])

    # --- asymmetric mesh (handedness canary) ---
    spawn_static_mesh_actor(actor_sub, "SM_LetterF", meshes["letter_f"], (1500.0, 0.0, 0.0))

    # --- two-slot mesh (per-slot material fidelity canary) ---
    spawn_static_mesh_actor(actor_sub, "SM_TwoTone", meshes["two_tone"], (1800.0, 0.0, 0.0))

    # --- rotated child under rotated parent (transform composition canary) ---
    parent = spawn_static_mesh_actor(actor_sub, "RotatedParent_Cube", meshes["cube"],
                                     (0.0, 400.0, 50.0), rotation=(0.0, 45.0, 0.0),
                                     material=materials["translucent"])
    child = spawn_static_mesh_actor(actor_sub, "RotatedChild_Sphere", meshes["sphere"],
                                    (0.0, 0.0, 0.0), material=materials["unsupported"])
    # K2_AttachToActor (ScriptName AttachToActor): attach keeping world transform,
    # then set the exact relative transform for determinism.
    if not child.attach_to_actor(parent, "",
                                 unreal.AttachmentRule.KEEP_WORLD,
                                 unreal.AttachmentRule.KEEP_WORLD,
                                 unreal.AttachmentRule.KEEP_WORLD, False):
        raise RuntimeError("failed to attach RotatedChild_Sphere to RotatedParent_Cube")
    child.root_component.set_relative_location_and_rotation(
        unreal.Vector(150.0, 0.0, 50.0), unreal.Rotator(30.0, 0.0, 0.0), False, True)

    # --- physics / kinematic / trigger ---
    dyn = spawn_static_mesh_actor(actor_sub, "Cube_Dynamic", meshes["cube"], (-300.0, 0.0, 100.0),
                                  mobility=movable)
    dyn.static_mesh_component.set_simulate_physics(True)

    spawn_static_mesh_actor(actor_sub, "Cube_Kinematic", meshes["cube"], (-600.0, 0.0, 100.0),
                            mobility=movable)   # collision defaults on, Simulate Physics off

    trigger = actor_sub.spawn_actor_from_class(unreal.TriggerBox, unreal.Vector(-900.0, 0.0, 100.0))
    trigger.set_actor_label("TriggerBox_01")
    # ATriggerBox defaults to the "Trigger" overlap profile; assert overlap events explicitly.
    collision = trigger.get_component_by_class(unreal.PrimitiveComponent)
    if collision is not None:
        collision.set_editor_property("generate_overlap_events", True)

    # --- lights (distinct, non-default intensities/colors for M5) ---
    point = actor_sub.spawn_actor_from_class(unreal.PointLight, unreal.Vector(300.0, 300.0, 250.0))
    point.set_actor_label("Light_Point")
    plc = point.point_light_component
    plc.set_intensity(12.5)
    plc.set_light_color(unreal.LinearColor(1.0, 0.6, 0.3, 1.0), True)
    plc.set_editor_property("attenuation_radius", 600.0)

    spot = actor_sub.spawn_actor_from_class(unreal.SpotLight, unreal.Vector(700.0, 300.0, 400.0),
                                            unreal.Rotator(-45.0, -90.0, 0.0))
    spot.set_actor_label("Light_Spot")
    slc = spot.spot_light_component
    slc.set_intensity(40.0)
    slc.set_light_color(unreal.LinearColor(0.4, 0.7, 1.0, 1.0), True)
    slc.set_editor_property("inner_cone_angle", 15.0)
    slc.set_editor_property("outer_cone_angle", 30.0)
    slc.set_editor_property("attenuation_radius", 1000.0)

    sun = actor_sub.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 600.0),
                                           unreal.Rotator(-50.0, 30.0, 0.0))
    sun.set_actor_label("Light_Directional")
    dlc = sun.light_component   # ALight::LightComponent (verified, Engine/Classes/Engine/Light.h)
    dlc.set_intensity(5.0)
    dlc.set_light_color(unreal.LinearColor(1.0, 0.95, 0.85, 1.0), True)

    # --- environment (M6) ---
    sky = actor_sub.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0.0, 0.0, 200.0))
    sky.set_actor_label("Atmo_SkyLight")
    sky.light_component.set_intensity(0.8)
    sky.light_component.set_editor_property("real_time_capture", True)

    fog = actor_sub.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0.0, 0.0, 0.0))
    fog.set_actor_label("Atmo_HeightFog")
    fog.component.set_editor_property("fog_density", 0.05)
    fog.component.set_editor_property("fog_height_falloff", 0.2)

    ppv = actor_sub.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, 0.0))
    ppv.set_actor_label("PPV_01")
    ppv.set_editor_property("unbound", True)

    if not level_sub.save_current_level():
        raise RuntimeError("failed to save level " + MAP_PATH)
    log("level saved: " + MAP_PATH)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ensure_dir(MESHES_DIR)
    ensure_dir(MATERIALS_DIR)
    ensure_dir(TEXTURES_DIR)
    ensure_dir("/Game/Maps")

    log("creating textures")
    tex_base = create_texture("T_Fixture_BaseColor", True, unreal.TextureCompressionSettings.TC_DEFAULT)
    tex_normal = create_texture("T_Fixture_Normal", False, unreal.TextureCompressionSettings.TC_NORMALMAP)
    tex_orm = create_texture("T_Fixture_ORM", False, unreal.TextureCompressionSettings.TC_MASKS)
    tex_scalar = create_texture("T_Fixture_Scalar", False, unreal.TextureCompressionSettings.TC_GRAYSCALE)

    log("creating materials")
    materials = build_materials(tex_base, tex_normal, tex_orm, tex_scalar)

    log("building SM_LetterF")
    letter_f = build_letter_f()

    log("building SM_TwoTone")
    two_tone = build_two_tone(materials)

    meshes = {
        "cube": unreal.EditorAssetLibrary.load_asset(ENGINE_CUBE),
        "sphere": unreal.EditorAssetLibrary.load_asset(ENGINE_SPHERE),
        "cylinder": unreal.EditorAssetLibrary.load_asset(ENGINE_CYLINDER),
        "plane": unreal.EditorAssetLibrary.load_asset(ENGINE_PLANE),
        "letter_f": letter_f,
        "two_tone": two_tone,
    }
    for key, mesh in meshes.items():
        if mesh is None:
            raise RuntimeError("failed to load mesh: " + key)

    log("building level")
    build_level(meshes, materials)


try:
    main()
except Exception:
    unreal.log_error("[" + RESULT_TAG + "] FAILED")
    unreal.log_error(traceback.format_exc())
    print("RESULT: FAIL")
    raise

print("RESULT: PASS")

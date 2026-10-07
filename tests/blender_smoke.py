"""Real Blender/FBX regression, no third-party assets or test dependencies.

blender --background --factory-startup --disable-autoexec --python-exit-code 1
  --python tests/blender_smoke.py -- /absolute/owned/validation-folder
"""
from pathlib import Path
import importlib.util
import json
import shutil
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "avatarforge"))
from blender_worker import constant_socket_value, covered_uniform_pixels, run, export_and_verify, preview
from bone_aliases import map_humanoid


def fixture(folder):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.use_preferences_save = False
    data = bpy.data.armatures.new("FixtureSkeleton")
    rig = bpy.data.objects.new("FixtureRig", data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    definitions = [
        ("Hips", None, (0, 0, 0.9), (0, 0, 1.0)),
        ("Spine", "Hips", (0, 0, 1.0), (0, 0, 1.2)),
        ("Chest", "Spine", (0, 0, 1.2), (0, 0, 1.4)),
        ("Neck", "Chest", (0, 0, 1.4), (0, 0, 1.5)),
        ("Head", "Neck", (0, 0, 1.5), (0, 0, 1.75)),
        ("Breast.L", "Chest", (0.1, -0.08, 1.3), (0.1, -0.2, 1.3)),
        ("Butt.L", "Hips", (0.1, 0.05, 0.95), (0.1, 0.15, 0.95)),
        ("Unweighted_Jiggle", "Hips", (0, 0, 1), (0, 0.1, 1)),
        ("CTRL_Breast_Jiggle", "Chest", (.1, .05, 1.3), (.1, .15, 1.3)),
        ("CTRL_Unused", "Hips", (0, .2, 1), (0, .3, 1)),
    ]
    for side, direction in (("Left", 1), ("Right", -1)):
        definitions.extend([
            (side + "UpperLeg", "Hips", (direction * .1, 0, .9), (direction * .1, 0, .5)),
            (side + "LowerLeg", side + "UpperLeg", (direction * .1, 0, .5), (direction * .1, 0, .1)),
            (side + "Foot", side + "LowerLeg", (direction * .1, 0, .1), (direction * .1, -.15, .05)),
            (side + "Shoulder", "Chest", (direction * .05, 0, 1.4), (direction * .15, 0, 1.4)),
            (side + "UpperArm", side + "Shoulder", (direction * .15, 0, 1.4), (direction * .4, 0, 1.4)),
            (side + "LowerArm", side + "UpperArm", (direction * .4, 0, 1.4), (direction * .65, 0, 1.4)),
            (side + "Hand", side + "LowerArm", (direction * .65, 0, 1.4), (direction * .75, 0, 1.4)),
        ])
    for name, parent, head, tail in definitions:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    data.bones["Unweighted_Jiggle"].use_deform = False
    data.bones["CTRL_Breast_Jiggle"].use_deform = False
    data.bones["CTRL_Unused"].use_deform = False
    constraint = rig.pose.bones["Spine"].constraints.new("COPY_TRANSFORMS")
    constraint.target, constraint.subtarget = rig, "CTRL_Unused"
    vertices = [(-.2, -.1, .8), (.2, -.1, .8), (.2, .1, .8), (-.2, .1, .8),
                (-.2, -.1, 1.4), (.2, -.1, 1.4), (.2, .1, 1.4), (-.2, .1, 1.4)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh_data = bpy.data.meshes.new("FixtureMeshData")
    mesh_data.from_pydata(vertices, [], faces)
    mesh = bpy.data.objects.new("FixtureBody", mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.parent = rig
    mesh.hide_select = True
    modifier = mesh.modifiers.new("Skin", "ARMATURE")
    modifier.object = rig
    mesh.vertex_groups.new(name="Hips").add(list(range(8)), .6, "REPLACE")
    mesh.vertex_groups.new(name="Breast.L").add(list(range(8)), .2, "REPLACE")
    mesh.vertex_groups.new(name="Butt.L").add(list(range(8)), .2, "REPLACE")
    mesh.shape_key_add(name="Basis", from_mix=False)
    smile = mesh.shape_key_add(name="Smile", from_mix=False)
    smile.value = 0
    smile.data[4].co.x -= .03
    blink = mesh.shape_key_add(name="Blink", from_mix=False)
    blink.value = 0
    blink.data[5].co.z -= .02
    mesh_data.uv_layers.new(name="UVMap")
    for polygon in mesh_data.polygons:
        for loop, uv in zip(polygon.loop_indices, ((0, 0), (1, 0), (1, 1), (0, 1))):
            mesh_data.uv_layers.active.data[loop].uv = uv
    material = bpy.data.materials.new("FixtureSkin")
    material.use_nodes = True
    image = bpy.data.images.new("FixtureAlbedo", width=64, height=64)
    image.generated_color = (.3, .5, .7, 1)
    image.pixels[:] = (.3, .5, .7, 1) * (64 * 64)
    image.pack()
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (.8, .8, .8, 1)
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    mesh_data.materials.append(material)
    # Blender 5.2 FBX export crashes when a length-3 custom property contains ints.
    rig["avatarforge_int_vector"] = [1, 0, 0]
    rig.pose.bones["Hips"]["avatarforge_pose_int_vector"] = [2, 0, 0]
    source = folder / "fixture.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    return source, {name for name, *_ in definitions if name != "CTRL_Unused"}


def accessory_parts(folder):
    """Child-of follower rigs are wearable parts. Hidden ones stay in the FBX, renderer-off later."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    source, _ = fixture(folder)
    rig = bpy.data.objects["FixtureRig"]
    arm_data = bpy.data.armatures.new("OptionSkeleton")
    option = bpy.data.objects.new("OptionRig", arm_data)
    bpy.context.scene.collection.objects.link(option)
    bpy.context.view_layer.objects.active = option
    bpy.ops.object.mode_set(mode="EDIT")
    bone = arm_data.edit_bones.new("Genital")
    bone.head, bone.tail = (0, 0, 0), (0, 0.05, 0)
    bpy.ops.object.mode_set(mode="OBJECT")
    option.location = (0.15, -0.2, 0.4)
    constraint = option.constraints.new("CHILD_OF")
    constraint.target = rig
    constraint.subtarget = "Hips"
    with bpy.context.temp_override(object=option, active_object=option):
        bpy.ops.constraint.childof_set_inverse(constraint=constraint.name)
    bpy.context.view_layer.update()

    def part_mesh(name, points, collection, parent):
        data = bpy.data.meshes.new(name + "Data")
        data.from_pydata(points, [], [(0, 1, 2)])
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        obj.parent = parent
        obj.modifiers.new("Skin", "ARMATURE").object = parent
        obj.vertex_groups.new(name="Genital").add([0, 1, 2], 1, "REPLACE")
        return obj

    vulva = part_mesh("Vulva", [(0, 0, 0), (0.05, 0, 0), (0, 0.05, 0)], bpy.context.scene.collection, option)
    vulva.shape_key_add(name="Basis", from_mix=False)
    opened = vulva.shape_key_add(name="Open", from_mix=False)
    opened.data[0].co.z += 0.02
    hidden_collection = bpy.data.collections.new("HiddenOptionCollection")
    bpy.context.scene.collection.children.link(hidden_collection)
    bpy.context.view_layer.layer_collection.children[hidden_collection.name].exclude = True
    part_mesh("HiddenOption", [(0, 0, 0.1), (0.04, 0, 0.1), (0, 0.04, 0.1)], hidden_collection, option)
    part_mesh("WGT-Option", [(0, 0, 0), (0.01, 0, 0), (0, 0.01, 0)], bpy.context.scene.collection, option)
    other_data = bpy.data.armatures.new("OtherSkeleton")
    other = bpy.data.objects.new("OtherRig", other_data)
    bpy.context.scene.collection.objects.link(other)
    bpy.context.view_layer.objects.active = other
    bpy.ops.object.mode_set(mode="EDIT")
    other_bone = other_data.edit_bones.new("Root")
    other_bone.head, other_bone.tail = (1, 0, 0), (1, 0.1, 0)
    bpy.ops.object.mode_set(mode="OBJECT")
    part_mesh("OtherCharacter", [(1, 0, 0), (1.02, 0, 0), (1, 0.02, 0)], bpy.context.scene.collection, other)
    bpy.context.view_layer.update()
    before_mesh = tuple(vulva.matrix_world.translation)
    before_head = tuple(option.matrix_world @ option.data.bones["Genital"].head_local)
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    result = run({"source": str(source), "output": str(folder / "fixture-accessory"),
                  "preset": "preserve", "options": {"preview": False, "bake_materials": False}})
    assert result["status"] != "blocked", result["issues"]
    assert set(result["selection"]["meshes"]) == {"FixtureBody", "Vulva", "HiddenOption"}, result["selection"]
    assert "WGT-Option" in result["selection"]["excluded_meshes"]
    assert "OtherCharacter" in result["selection"]["excluded_meshes"]
    parts = {part["mesh"]: part for part in result["optional_parts"]}
    assert parts["Vulva"]["start_hidden"] is False
    assert parts["HiddenOption"]["start_hidden"] is True
    assert parts["Vulva"]["armature"] == "OptionRig" and parts["Vulva"]["bone"] == "Hips"
    assert parts["HiddenOption"]["bone"] == "Hips"
    assert "Open" in result["integrity"]["export_shape_keys"]["Vulva"]
    assert result["integrity"]["export_meshes"]["Vulva"] == {"vertices": 3, "triangles": 1}
    assert result["integrity"]["export_meshes"]["HiddenOption"] == {"vertices": 3, "triangles": 1}
    assert "WGT-Option" not in result["integrity"]["export_meshes"]
    assert "Genital" in result["integrity"]["export_bones"]
    assert "Genital" in result["integrity"]["export_weighted_bones"]
    assert not result["integrity"]["missing_weighted_bones"], result["integrity"]["missing_weighted_bones"]
    assert any(item["code"] == "multiple_character_rigs" for item in result["issues"])
    assert any(item["code"] == "accessory_parts" and item["severity"] == "info" for item in result["issues"])
    assert result["material_recipe"]["bake_size"] == 64, result["material_recipe"]
    assert "OptionRig" not in bpy.data.objects
    exported = bpy.data.objects["FixtureRig"]
    genital = exported.data.bones["Genital"]
    assert genital.parent and genital.parent.name == "Hips", genital.parent
    vulva = bpy.data.objects["Vulva"]
    assert vulva.parent and vulva.parent.name == "FixtureRig"
    mesh_delta = max(abs(actual - expected) for actual, expected in zip(vulva.matrix_world.translation, before_mesh))
    head = exported.matrix_world @ genital.head_local
    head_delta = max(abs(actual - expected) for actual, expected in zip(head, before_head))
    assert mesh_delta < 1e-3, (tuple(vulva.matrix_world.translation), before_mesh, mesh_delta)
    assert head_delta < 1e-3, (tuple(head), before_head, head_delta)


def main():
    folder = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    source, expected_bones = fixture(folder)
    scene = bpy.context.scene
    if hasattr(scene.render.image_settings, "media_type"):
        scene.render.image_settings.media_type = "VIDEO"
    preview_media_type = getattr(scene.render.image_settings, "media_type", None)
    state = (scene.render.engine, scene.cycles.device, scene.cycles.samples,
             scene.cycles.use_denoising, scene.camera, scene.world,
             scene.render.resolution_x, scene.render.resolution_y,
             scene.render.resolution_percentage, scene.render.filepath,
             scene.render.image_settings.file_format)
    objects = set(bpy.data.objects)
    preview_report = {"issues": []}
    preview([bpy.data.objects["FixtureBody"]], folder, preview_report)
    assert preview_report["preview_renderer"] == "cycles_cpu", preview_report
    assert (folder / "preview.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert getattr(scene.render.image_settings, "media_type", None) == preview_media_type
    assert set(bpy.data.objects) == objects
    assert state == (scene.render.engine, scene.cycles.device, scene.cycles.samples,
                     scene.cycles.use_denoising, scene.camera, scene.world,
                     scene.render.resolution_x, scene.render.resolution_y,
                     scene.render.resolution_percentage, scene.render.filepath,
                     scene.render.image_settings.file_format)
    report = run({"source": str(source), "output": str(folder / "fixture-preserve"), "preset": "preserve", "options": {"target_triangles": 1}})
    assert report["optimization"]["target_triangles"] is None
    assert any(item["code"] == "preserve_target_ignored" for item in report["issues"])
    assert report["status"] == "needs_review", report["issues"]
    assert report["integrity"]["fbx_roundtrip_verified"]
    imported_rig = bpy.data.objects["FixtureRig"]
    vector = [float(item) for item in imported_rig["avatarforge_int_vector"]]
    pose_vector = [float(item) for item in imported_rig.pose.bones["Hips"]["avatarforge_pose_int_vector"]]
    assert vector == [1.0, 0.0, 0.0], vector
    assert pose_vector == [2.0, 0.0, 0.0], pose_vector
    assert any(item["code"] == "custom_property_vectors" and item["severity"] == "info" for item in report["issues"])
    assert set(report["integrity"]["export_bones"]) == expected_bones
    assert not report["integrity"]["missing_bones"]
    assert not report["integrity"]["missing_shape_keys"]
    assert report["integrity"]["export_shape_keys"]["FixtureBody"] == ["Smile", "Blink"]
    shapes = bpy.data.objects["FixtureBody"].data.shape_keys
    assert abs((shapes.key_blocks["Smile"].data[4].co - shapes.reference_key.data[4].co).length - .03) < 1e-5
    assert abs((shapes.key_blocks["Blink"].data[5].co - shapes.reference_key.data[5].co).length - .02) < 1e-5
    assert max((point.co - base.co).length for point, base in zip(shapes.key_blocks["Blink"].data, shapes.reference_key.data)) < .02001
    assert not report["missing_required_humanoid"]
    assert {entry["category"] for entry in report["physics"]} >= {"breasts", "butt", "secondary"}
    assert all(not entry["approved"] for entry in report["physics"])
    assert report["materials"][0]["base_color_texture"]
    assert report["materials"][0]["base_color"] == [1.0, 1.0, 1.0, 1.0]
    assert (folder / "fixture-preserve" / "preview.png").stat().st_size > 100
    assert report["preview_renderer"] == "cycles_cpu"
    assert not any(obj.name.startswith("AvatarForge_Preview") for obj in bpy.data.objects)
    relocated = folder / "portable output – 移动"
    shutil.copytree(folder / "fixture-preserve", relocated)
    bpy.ops.wm.open_mainfile(filepath=str(relocated / "model.blend"), use_scripts=False)
    portable_images = [image for image in bpy.data.images if image.filepath.replace("\\", "/").startswith("//textures/") and not image.packed_file]
    assert portable_images, "Exported blend must store portable texture paths"
    for image in portable_images:
        path = Path(bpy.path.abspath(image.filepath)).resolve()
        assert path.is_relative_to(relocated.resolve()) and path.is_file(), (image.name, image.filepath)
        image.reload()
        assert image.size[0] > 0 and image.pixels[:4]
    for preset in ("balanced", "mobile"):
        result = run({"source": str(source), "output": str(folder / ("fixture-" + preset)), "preset": preset,
                      "options": {"target_triangles": 1, "preview": False, "physics_roots": ["Breast.L"]}})
        assert result["status"] == "needs_review", result["issues"]
        assert not result["integrity"]["missing_bones"] and not result["integrity"]["missing_shape_keys"]
        assert result["physics"][0]["approved"]
        assert result["optimization"]["shape_key_meshes_protected"] == ["FixtureBody"]
        assert result["summary"]["triangles"] == 12
    mobile = run({"source": str(source), "output": str(folder / "fixture-mobile-default"), "preset": "mobile", "options": {"preview": False}})
    assert mobile["optimization"]["target_triangles"] == 15000
    assert not mobile["integrity"]["missing_shape_keys"]
    result = run({"source": str(folder / "fixture-preserve" / "model.fbx"), "output": str(folder / "fixture-fbx"),
                  "preset": "preserve", "options": {"preview": False}})
    assert not result["integrity"]["missing_bones"] and not result["integrity"]["missing_shape_keys"]
    assert result["status"] == "ready", result["issues"]
    # Source UDIMs must become actual portable atlas pixels, preserving original topology/keys.
    source, _ = fixture(folder)
    for number, color in ((1001, (1, 0, 0, 1)), (1002, (0, 0, 1, 1))):
        tile = bpy.data.images.new("FixtureTile" + str(number), width=8, height=8)
        tile.pixels[:] = color * 64
        tile.filepath_raw = str(folder / ("tile." + str(number) + ".png"))
        tile.file_format = "PNG"
        tile.save()
    image = bpy.data.images.new("FixtureUDIM", width=8, height=8, tiled=True)
    image.tiles.new(1002)
    image.filepath = str(folder / "tile.<UDIM>.png")
    bpy.data.materials["FixtureSkin"].node_tree.nodes.get("Image Texture").image = image
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    result = run({"source": str(source), "output": str(folder / "fixture-udim"), "preset": "balanced", "options": {"preview": False, "bake_materials": False}})
    assert len(result["udim_atlases"]) == 1, result["issues"]
    assert result["udim_atlases"][0]["size"] == [16, 8]
    assert not result["integrity"]["missing_shape_keys"]
    assert result["materials"][0]["base_color_scale"] == [0.5, 1.0]
    atlas = bpy.data.images.load(str(folder / "fixture-udim" / result["materials"][0]["base_color_texture"]))
    pixels = atlas.pixels[:]
    assert pixels[0] > .9 and pixels[2] < .1
    assert pixels[8 * 4] < .1 and pixels[8 * 4 + 2] > .9
    source, _ = fixture(folder)
    shader = bpy.data.materials["FixtureSkin"].node_tree.nodes.get("Principled BSDF")
    shader.inputs["Metallic"].default_value = .25
    shader.inputs["Roughness"].default_value = .4
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    result = run({"source": str(source), "output": str(folder / "fixture-baked"), "preset": "balanced",
                  "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    assert len(result.get("baked_materials", [])) == 1, result["issues"]
    assert not result["integrity"]["missing_bones"] and not result["integrity"]["missing_shape_keys"]
    assert not result["integrity"]["missing_weighted_bones"]
    material = result["materials"][0]
    center = (32 * 64 + 32) * 4
    assert not material.get("metallic_smoothness_texture") and not material.get("roughness_texture")
    assert not material.get("emission_texture") and not material.get("normal_texture")
    assert abs(material["metallic"] - .25) < 1e-6 and abs(material["roughness"] - .4) < 1e-6
    assert material["emission_color"] == [0, 0, 0, 1]
    assert material["alpha_mode"] == "OPAQUE"
    assert material["base_color"] == [1.0, 1.0, 1.0, 1.0]
    # Inspect every covered pixel, including the final pixel, while ignoring
    # unused UV background. Packed smoothness alpha is not a coverage mask.
    pixels = bpy.data.images.new("ExactCoveredPixels", width=8, height=8, alpha=True, float_buffer=True)
    coverage = bpy.data.images.new("ExactCoverage", width=8, height=8, alpha=True, float_buffer=True)
    rgba = [.9, .1, .7, 0] * 64
    mask = [0, 0, 0, 0] * 64
    for index in (1, 2, 63):
        rgba[index * 4:index * 4 + 4] = [.25, .25, .25, .6]
        mask[index * 4:index * 4 + 4] = [1, 1, 1, 1]
    pixels.pixels[:] = rgba
    coverage.pixels[:] = mask
    inspected = covered_uniform_pixels(pixels, coverage)
    assert inspected["pixels_checked"] == 64 and inspected["covered_pixels"] == 3
    rgba[63 * 4] = .26
    pixels.pixels[:] = rgba
    assert covered_uniform_pixels(pixels, coverage) is None
    source, _ = fixture(folder)
    body = bpy.data.objects["FixtureBody"]
    for point in body.data.uv_layers[0].data:
        point.uv *= .25
    tree = body.data.materials[0].node_tree
    shader = tree.nodes.get("Principled BSDF")
    for name, value in (("Metallic", .25), ("Roughness", .4)):
        node = tree.nodes.new("ShaderNodeValue")
        node.outputs[0].default_value = value
        tree.links.new(node.outputs[0], shader.inputs[name])
        assert abs(constant_socket_value(shader.inputs[name]) - value) < 1e-6
        node.mute = True
        assert constant_socket_value(shader.inputs[name]) is None
        node.mute = False
    black_image = bpy.data.images.new("CoveredBlackEmission", width=8, height=8, alpha=True)
    black_image.pixels[:] = [0, 0, 0, 1] * 64
    black_image.pack()
    black = tree.nodes.new("ShaderNodeTexImage")
    black.image = black_image
    tree.links.new(black.outputs["Color"], shader.inputs["Emission Color"])
    shader.inputs["Emission Strength"].default_value = 1
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    constant = run({"source": str(source), "output": str(folder / "fixture-covered-constants"), "preset": "balanced",
                    "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    entry = constant["materials"][0]
    assert not entry.get("metallic_smoothness_texture") and not entry.get("emission_texture")
    exact = [item for item in constant["baked_channel_optimizations"] if item["proof"].startswith("exact_covered")]
    assert len(exact) == 1, constant["baked_channel_optimizations"]
    assert all(item["pixels_checked"] == 4096 and 0 < item["covered_pixels"] < 4096 for item in exact)
    source, _ = fixture(folder)
    tree = bpy.data.materials["FixtureSkin"].node_tree
    shader = tree.nodes.get("Principled BSDF")
    detail = bpy.data.images.new("VaryingSurface", width=16, height=16, alpha=True)
    detail.pixels[:] = [(component) for y in range(16) for x in range(16)
                       for component in ((.2, .2, .2, .35) if x < 8 else (.8, .8, .8, 1))]
    detail.pack()
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = detail
    tree.links.new(texture.outputs["Color"], shader.inputs["Roughness"])
    tree.links.new(texture.outputs["Color"], shader.inputs["Emission Color"])
    tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
    shader.inputs["Emission Strength"].default_value = 1
    normal_image = bpy.data.images.new("VaryingNormal", width=16, height=16, alpha=True)
    normal_image.colorspace_settings.name = "Non-Color"
    normal_image.pixels[:] = [component for y in range(16) for x in range(16)
                            for component in ((.5, .5, 1, 1) if x < 8 else (.7, .5, .9, 1))]
    normal_image.pack()
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = normal_image
    normal_map = tree.nodes.new("ShaderNodeNormalMap")
    tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
    tree.links.new(normal_map.outputs["Normal"], shader.inputs["Normal"])
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    varying = run({"source": str(source), "output": str(folder / "fixture-varying-channels"), "preset": "balanced",
                   "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    entry = varying["materials"][0]
    assert all(entry.get(name + "_texture") for name in ("base_color", "normal", "metallic_smoothness", "emission"))
    assert entry["alpha_mode"] == "BLEND"
    assert not varying["baked_channel_optimizations"], varying["baked_channel_optimizations"]
    image = bpy.data.images.load(str(folder / "fixture-varying-channels" / entry["base_color_texture"]))
    assert min(image.pixels[3::4]) < .5 and max(image.pixels[3::4]) > .99
    source, _ = fixture(folder)
    shader = bpy.data.materials["FixtureSkin"].node_tree.nodes.get("Principled BSDF")
    shader.inputs["Emission Color"].default_value = (.2, .05, .01, 1)
    shader.inputs["Emission Strength"].default_value = 3
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    scalar = run({"source": str(source), "output": str(folder / "fixture-emission-scalar"), "preset": "balanced",
                  "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    assert not scalar["materials"][0].get("emission_texture")
    assert abs(scalar["materials"][0]["emission_color"][0] - .2) < 1e-6
    assert scalar["materials"][0]["emission_strength"] == 3
    source, _ = fixture(folder)
    clone = bpy.data.objects["FixtureBody"].copy()
    clone.data = clone.data.copy()
    clone.name = "FixtureCloth"
    clone.location.x = .6
    bpy.context.scene.collection.objects.link(clone)
    material = bpy.data.materials.new("SecondSkin")
    material.use_nodes = True
    material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0, 1, 0, 1)
    clone.data.materials.clear()
    clone.data.materials.append(material)
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    result = run({"source": str(source), "output": str(folder / "fixture-batch-baked"), "preset": "balanced",
                  "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    assert len(result.get("baked_materials", [])) == 2, result["issues"]
    assert not result["integrity"]["missing_shape_keys"] and not result["integrity"]["missing_weighted_bones"]
    second = next(item for item in result["materials"] if item["name"] == "SecondSkin")
    image = bpy.data.images.load(str(folder / "fixture-batch-baked" / second["base_color_texture"]))
    green = image.pixels[center:center + 4]
    assert green[0] < .1 and green[1] > .9 and green[2] < .1, green
    source, _ = fixture(folder)
    for item in bpy.data.objects["FixtureBody"].data.uv_layers.active.data:
        item.uv = (0, 0)
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    invalid_uv = run({"source": str(source), "output": str(folder / "fixture-degenerate-uv"), "preset": "balanced",
                      "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    assert not invalid_uv.get("baked_materials")
    assert any(item["code"] == "material_bake_skipped" for item in invalid_uv["issues"])
    assert invalid_uv["status"] == "needs_review"
    assert invalid_uv["materials"][0]["base_color_texture"], invalid_uv["issues"]
    # Repeating source images may live outside tile 1001 without being UDIMs.
    # Actual baking must cover those faces and communicate the atlas scale.
    source, _ = fixture(folder)
    body = bpy.data.objects["FixtureBody"]
    for point in body.data.uv_layers[0].data:
        point.uv.x += 2
        point.uv.y += 1
    for index in range(5):
        body.data.uv_layers.new(name="AuthoredUV" + str(index))
    atlas = body.data.uv_layers.new(name="AF_Atlas_3x2_Source")
    for old, new in zip(body.data.uv_layers[0].data, atlas.data):
        new.uv = (old.uv.x / 3, old.uv.y / 2)
    shader = body.data.materials[0].node_tree.nodes.get("Principled BSDF")
    tree = body.data.materials[0].node_tree
    tree.links.remove(shader.inputs["Base Color"].links[0])
    shader.inputs["Base Color"].default_value = (.8, .1, .05, 1)
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    tiled = run({"source": str(source), "output": str(folder / "fixture-repeat-tile-baked"), "preset": "balanced",
                 "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    material = tiled["materials"][0]
    assert material["base_color_scale"] == [1 / 3, 1 / 2], material
    assert not material.get("normal_texture") and not material.get("metallic_smoothness_texture") and not material.get("emission_texture")
    image = bpy.data.images.load(str(folder / "fixture-repeat-tile-baked" / material["base_color_texture"]))
    offset = (48 * 64 + 53) * 4
    red = image.pixels[offset:offset + 4]
    expected_rgb = [1.055 * value ** (1 / 2.4) - .055 for value in (.8, .1, .05)]
    assert all(abs(actual - expected) < .01 for actual, expected in zip(red, expected_rgb)) and red[3] > .99, red
    assert min(point.uv.x for point in bpy.data.objects["FixtureBody"].data.uv_layers[0].data) > 1.99
    assert not tiled["integrity"]["missing_shape_keys"] and not tiled["integrity"]["missing_weighted_bones"]
    source, _ = fixture(folder)
    shader = bpy.data.objects["FixtureBody"].data.materials[0].node_tree.nodes.get("Principled BSDF")
    shader.inputs["Alpha"].default_value = .35
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    transparent = run({"source": str(source), "output": str(folder / "fixture-scalar-alpha"), "preset": "preserve",
                       "options": {"preview": False, "bake_materials": False}})
    assert transparent["materials"][0]["alpha_mode"] == "BLEND"
    assert abs(transparent["materials"][0]["base_color"][3] - .35) < 1e-6
    source, _ = fixture(folder)
    body = bpy.data.objects["FixtureBody"]
    for index in range(7):
        body.data.uv_layers.new(name="RequiredUV" + str(index))
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    full_uv = run({"source": str(source), "output": str(folder / "fixture-full-uv"), "preset": "balanced",
                   "options": {"preview": False, "bake_materials": True, "bake_size": 64}})
    assert full_uv["status"] == "needs_review" and not full_uv.get("baked_materials"), full_uv["issues"]
    assert any(item["code"] == "material_bake_skipped" for item in full_uv["issues"])
    assert len(bpy.data.objects["FixtureBody"].data.uv_layers) == 8
    # Generated rigs have named controls and independently constrained deform
    # roots. Choosing controls gives a valid-looking map which cannot drive skin.
    source, _ = fixture(folder)
    rig, mesh = bpy.data.objects["FixtureRig"], bpy.data.objects["FixtureBody"]
    mapped, _, _ = map_humanoid([bone.name for bone in rig.data.bones])
    generated = {}
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for item in mapped:
        original = rig.data.edit_bones[item["boneName"]]
        name = "DEF-" + original.name + ("_1" if item["humanName"].endswith(("UpperLeg", "LowerLeg", "UpperArm", "LowerArm")) else "")
        deform = rig.data.edit_bones.new(name)
        deform.matrix, deform.length = original.matrix.copy(), original.length
        generated[item["boneName"]] = name
    bridge = rig.data.edit_bones.new("P-HairFollower")
    bridge.head, bridge.tail = (0, 0, 1.6), (0, .1, 1.6)
    follower = rig.data.edit_bones.new("HairFollower")
    follower.head, follower.tail = (0, .1, 1.6), (0, .2, 1.6)
    follower.parent = bridge
    elbow = rig.data.edit_bones.new("Elbow.L")
    elbow.matrix, elbow.length = rig.data.edit_bones["LeftLowerArm"].matrix.copy(), .05
    elbow.parent = rig.data.edit_bones["LeftUpperArm"]
    elbow.use_deform = False
    deform_elbow = rig.data.edit_bones.new("DEF-Elbow.L")
    deform_elbow.matrix, deform_elbow.length = elbow.matrix.copy(), elbow.length
    deform_elbow.parent = elbow
    bpy.ops.object.mode_set(mode="OBJECT")
    for control, deform in generated.items():
        rig.data.bones[control].use_deform = False
        constraint = rig.pose.bones[deform].constraints.new("COPY_TRANSFORMS")
        constraint.target, constraint.subtarget = rig, control
        group = mesh.vertex_groups.get(control)
        if group:
            group.name = deform
    constraint = rig.pose.bones["P-HairFollower"].constraints.new("ARMATURE")
    target = constraint.targets.new()
    target.target, target.subtarget, target.weight = rig, generated["Head"], 1
    mesh.vertex_groups.new(name="HairFollower").add([7], .05, "REPLACE")
    mesh.vertex_groups.new(name="Elbow.L").add([6], .03, "REPLACE")
    mesh.vertex_groups.new(name="DEF-Elbow.L").add([5], .02, "REPLACE")
    heads = {bone.name: tuple(bone.head_local) for bone in rig.data.bones}
    generated_source = folder / "fixture-generated-source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(generated_source))
    source = generated_source
    result = run({"source": str(source), "output": str(folder / "fixture-generated"), "preset": "balanced", "options": {"preview": False}})
    assert not result["missing_required_humanoid"]
    assert all(item["boneName"].startswith("DEF-") for item in result["humanoid"])
    assert next(item["boneName"] for item in result["humanoid"] if item["humanName"] == "LeftLowerArm") == generated["LeftLowerArm"]
    assert not any(item["code"] == "humanoid_hierarchy" for item in result["issues"])
    assert result["generated_hierarchy_repair"]["rest_matrix_max_delta"] < 1e-5
    assert not result["integrity"]["missing_bones"] and not result["integrity"]["missing_shape_keys"] and not result["integrity"]["missing_weighted_bones"]
    exported = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    assert exported.data.bones[generated["Head"]] in exported.data.bones["HairFollower"].parent_recursive
    assert exported.data.bones[generated["Chest"]] in exported.data.bones["Breast.L"].parent_recursive
    for bone in exported.data.bones:
        assert max(abs(bone.head_local[i] - heads[bone.name][i]) for i in range(3)) < 1e-4, bone.name
    # Inject a real dropped influence while leaving its bone intact. The FBX
    # integrity gate must reject this even for a non-physics named bone.
    body = bpy.data.objects["FixtureBody"]
    body.vertex_groups.remove(body.vertex_groups[generated["Hips"]])
    damaged = {"issues": [], "weighted_bones": result["weighted_bones"]}
    destination = folder / "fixture-influence-loss"
    destination.mkdir(exist_ok=True)
    export_and_verify(exported, [body], destination, result["integrity"]["export_bones"], [],
                      result["integrity"]["export_shape_keys"], damaged)
    assert damaged["integrity"]["missing_weighted_bones"] == [generated["Hips"]]
    assert any(item["code"] == "export_weight_integrity" and item["severity"] == "error" for item in damaged["issues"])
    # Blender's real collapse reducer drops this sparse boundary influence.
    # Retaining the source grid gives a usable export with an open budget item.
    fixture(folder)
    rig = bpy.data.objects["FixtureRig"]
    bpy.data.objects.remove(bpy.data.objects["FixtureBody"], do_unlink=True)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=12, y_subdivisions=12)
    grid = bpy.context.object
    grid.name, grid.parent = "SparseGrid", rig
    modifier = grid.modifiers.new("Skin", "ARMATURE")
    modifier.object = rig
    grid.vertex_groups.new(name="Hips").add(list(range(len(grid.data.vertices))), 1, "REPLACE")
    grid.vertex_groups.new(name="Breast.L").add([10], 1, "REPLACE")
    sparse_source = folder / "fixture-decimation-source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(sparse_source))
    result = run({"source": str(sparse_source), "output": str(folder / "fixture-decimation-fallback"), "preset": "balanced",
                  "options": {"preview": False, "target_triangles": 1}})
    assert result["status"] == "needs_review"
    assert result["optimization"]["skin_weight_meshes_protected"] == ["SparseGrid"]
    assert result["optimization"]["triangles_before"] == result["optimization"]["triangles_after"]
    assert not result["integrity"]["missing_weighted_bones"]
    assert any(item["code"] == "decimation_skin_preserved" for item in result["issues"])
    # Authored fitting values, visibility and occlusion must survive conversion,
    # and the baker must use render/export UVs rather than an unused edit UV.
    fixture(folder)
    body = bpy.data.objects["FixtureBody"]
    rig = bpy.data.objects["FixtureRig"]
    body.data.shape_keys.key_blocks["Smile"].value = .4
    rig["authored_fit"] = .4
    curve = body.data.shape_keys.key_blocks["Smile"].driver_add("value")
    curve.driver.type = "AVERAGE"
    variable = curve.driver.variables.new()
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = rig
    variable.targets[0].data_path = '["authored_fit"]'
    body.data.shape_keys.key_blocks["Blink"].slider_min = -1
    body.data.shape_keys.key_blocks["Blink"].value = -.2
    body.vertex_groups.new(name="VisibleMask").add([0, 1, 4, 5], 1, "REPLACE")
    rig.data.bones["Unweighted_Jiggle"].use_deform = True
    body.vertex_groups.new(name="Unweighted_Jiggle").add([2], .1, "REPLACE")
    mask = body.modifiers.new("Authored body occlusion", "MASK")
    mask.vertex_group = "VisibleMask"
    unused = body.data.uv_layers.new(name="UnusedEditUV")
    body.data.uv_layers.active_index = len(body.data.uv_layers) - 1
    body.data.uv_layers[0].active_render = True
    shader = bpy.data.materials["FixtureSkin"].node_tree.nodes.get("Principled BSDF")
    texture = bpy.data.materials["FixtureSkin"].node_tree.nodes.get("Image Texture")
    bpy.data.materials["FixtureSkin"].node_tree.links.new(texture.outputs["Color"], shader.inputs["Roughness"])
    rig.pose.bones["CTRL_Unused"]["opacity"] = 1.0
    curve = shader.inputs["Alpha"].driver_add("default_value")
    curve.driver.type = "AVERAGE"
    variable = curve.driver.variables.new()
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = rig
    variable.targets[0].data_path = 'pose.bones["CTRL_Unused"]["opacity"]'
    hidden = body.copy()
    hidden.data = body.data.copy()
    hidden.name = "HiddenAlternative"
    bpy.context.scene.collection.objects.link(hidden)
    hidden.hide_set(True)
    visual_source = folder / "fixture-visual-source.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(visual_source))
    visual = run({"source": str(visual_source), "output": str(folder / "fixture-visual"), "preset": "preserve",
                  "options": {"preview": False, "bake_size": 64}})
    assert visual["status"] != "blocked", visual["issues"]
    assert visual["selection"]["meshes"] == ["FixtureBody"]
    assert "HiddenAlternative" in visual["selection"]["excluded_meshes"]
    assert visual["shape_keys"][0]["values"] == [.4000000059604645, -.20000000298023224]
    assert visual["shape_keys"][0]["source_value_drivers_frozen"] == 1
    assert any(item["path"].endswith('inputs[4].default_value') or 'default_value' in item["path"] for item in visual["source_inputs_frozen"])
    assert visual["visibility_masks"][0]["vertices_after"] == 4
    assert visual["visibility_masks"][0]["faces_after"] == 1
    assert "Unweighted_Jiggle" in visual["intentionally_masked_weighted_bones"]
    assert "Unweighted_Jiggle" in visual["integrity"]["export_bones"]
    assert not visual["integrity"]["missing_shape_keys"] and not visual["integrity"]["missing_weighted_bones"]
    assert visual["baked_materials"][0]["destination_uv"] == "UV0"
    assert visual["material_recipe"]["mode"] == "auto"
    assert visual["materials"][0]["metallic_smoothness_scale"] == [1, 1]
    assert not visual["materials"][0].get("emission_texture")
    assert visual["materials"][0]["alpha_mode"] == "OPAQUE"
    assert not any(i["code"] == "material_bake_skipped" for i in visual["issues"])
    body = bpy.data.objects["FixtureBody"]
    shapes = body.data.shape_keys
    assert max((a.co-b.co).length for a,b in zip(shapes.key_blocks["Smile"].data, shapes.reference_key.data)) > .02999
    assert max((a.co-b.co).length for a,b in zip(shapes.key_blocks["Blink"].data, shapes.reference_key.data)) > .01999
    bpy.ops.wm.open_mainfile(filepath=str(folder / "fixture-visual" / "model.blend"))
    backup = bpy.data.meshes[visual["visibility_masks"][0]["unmasked_source_mesh_backup"]]
    assert backup.use_fake_user and len(backup.vertices) == 8 and len(backup.polygons) == 6
    assert [key.name for key in backup.shape_keys.key_blocks] == ["Basis", "Smile", "Blink"]
    assert len(bpy.data.objects["FixtureBody"].data.vertices) == 4
    material_backup = bpy.data.materials["AF_Source_FixtureSkin"]
    assert material_backup.use_fake_user
    source_shader = material_backup.node_tree.nodes.get("Principled BSDF")
    assert source_shader and source_shader.inputs["Roughness"].is_linked
    # A non-reference Basis is a real morph, collision is an authored body
    # modifier, and source auto-pack must not steal portable output images.
    source, _ = fixture(folder)
    body = bpy.data.objects["FixtureBody"]
    body.data.shape_keys.reference_key.name = "Rest"
    fitting = body.shape_key_add(name="Basis", from_mix=False)
    fitting.data[4].co.z += .25
    fitting.value = .6
    body.modifiers.new("AuthoredCollision", "COLLISION")
    bpy.data.use_autopack = True
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    source_bytes = source.read_bytes()
    repaired = run({"source": str(source), "output": str(folder / "fixture-authored-edge-cases"),
                    "preset": "preserve", "options": {"preview": False, "bake_materials": False}})
    assert source.read_bytes() == source_bytes
    assert repaired["status"] == "needs_review", repaired["issues"]
    assert repaired["material_recipe"]["bake_size"] == 64, repaired.get("material_recipe")
    assert repaired["selection"]["meshes"] == ["FixtureBody"]
    assert repaired["integrity"]["source_meshes"]["FixtureBody"] == {"vertices": 8, "triangles": 12}
    assert not repaired["integrity"]["geometry_errors"]
    assert not repaired["integrity"]["missing_shape_keys"]
    rename = next(entry for entry in repaired["shape_key_renames"] if entry["source"] == "Basis")
    assert rename["export"] == "AF_Basis_Morph", rename
    shapes = bpy.data.objects["FixtureBody"].data.shape_keys
    morph = shapes.key_blocks[rename["export"]]
    assert abs(max((point.co - base.co).length for point, base in zip(morph.data, shapes.reference_key.data)) - .25) < 1e-5
    fitting_defaults = next(entry for entry in repaired["shape_keys"] if entry["object"] == "FixtureBody")
    assert abs(fitting_defaults["values"][fitting_defaults["names"].index(rename["export"])] - .6) < 1e-6
    moved = folder / "autopack portable output – 移动"
    shutil.copytree(folder / "fixture-authored-edge-cases", moved)
    bpy.ops.wm.open_mainfile(filepath=str(moved / "model.blend"), use_scripts=False)
    assert not bpy.data.use_autopack
    shapes = bpy.data.objects["FixtureBody"].data.shape_keys
    assert abs(shapes.key_blocks[rename["export"]].value - .6) < 1e-6
    image = bpy.data.objects["FixtureBody"].data.materials[0].node_tree.nodes.get("Image Texture").image
    assert not image.packed_file and image.filepath.replace("\\", "/").startswith("//textures/")
    portable_path = Path(bpy.path.abspath(image.filepath)).resolve()
    assert portable_path.is_relative_to(moved.resolve()) and portable_path.is_file()
    image.reload()
    assert image.size[0] == 64 and len(image.pixels) == 64 * 64 * 4
    assert all(abs(actual - expected) < .01 for actual, expected in zip(image.pixels[:4], (.3, .5, .7, 1)))
    # Explicit hidden selection must reach the FBX through hidden/excluded
    # parent collections; positive names on another mesh cannot prove that.
    source, _ = fixture(folder)
    rig = bpy.data.objects["FixtureRig"]
    parent = bpy.data.collections.new("HiddenParent")
    child = bpy.data.collections.new("HiddenChild")
    bpy.context.scene.collection.children.link(parent)
    parent.children.link(child)
    parent.hide_viewport = parent.hide_render = True
    bpy.context.view_layer.layer_collection.children[parent.name].exclude = True
    data = bpy.data.meshes.new("HiddenPartData")
    data.from_pydata([(0, 0, 1), (.1, 0, 1), (0, .1, 1)], [], [(0, 1, 2)])
    part = bpy.data.objects.new("HiddenPart", data)
    child.objects.link(part)
    part.parent = rig
    part.vertex_groups.new(name="Hips").add([0, 1, 2], 1, "REPLACE")
    part.modifiers.new("Skin", "ARMATURE").object = rig
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    included = run({"source": str(source), "output": str(folder / "fixture-hidden-collection"),
                    "preset": "preserve", "options": {"include_hidden": True, "preview": False}})
    assert included["status"] != "blocked", included["issues"]
    assert set(included["selection"]["meshes"]) == {"FixtureBody", "HiddenPart"}
    assert not included["integrity"]["geometry_errors"], included["integrity"]
    assert set(included["integrity"]["export_meshes"]) == {"FixtureBody", "HiddenPart"}
    assert included["integrity"]["export_meshes"]["HiddenPart"] == {"vertices": 3, "triangles": 1}
    # Unsupported source appearance must require review even when the named
    # Principled shader and all named bones/morphs still round-trip correctly.
    for case in ("disconnected-surface", "subdivision"):
        source, _ = fixture(folder)
        rig = bpy.data.objects["FixtureRig"]
        for bone in rig.pose.bones:
            for constraint in list(bone.constraints):
                bone.constraints.remove(constraint)
        body = bpy.data.objects["FixtureBody"]
        if case == "disconnected-surface":
            tree = body.data.materials[0].node_tree
            output = tree.nodes.get("Material Output")
            for link in list(output.inputs["Surface"].links):
                tree.links.remove(link)
        else:
            modifier = body.modifiers.new("AuthoredSubdivision", "SUBSURF")
            modifier.levels = modifier.render_levels = 2
        bpy.ops.wm.save_as_mainfile(filepath=str(source))
        reviewed = run({"source": str(source), "output": str(folder / ("fixture-" + case)),
                       "preset": "preserve", "options": {"preview": False}})
        assert reviewed["status"] == "needs_review", reviewed["issues"]
        assert not reviewed["integrity"]["geometry_errors"]
        if case == "disconnected-surface":
            assert any(item["severity"] == "warning" and "FixtureSkin" in item["message"] for item in reviewed["issues"])
        else:
            assert any(entry["mesh"] == "FixtureBody" and "SUBSURF" in entry["modifiers"] for entry in reviewed["nonportable_modifiers"])
    # A mask belongs to its object, even when another object shares its source
    # mesh. Editing the masked copy must retain every unmasked sibling vertex.
    source, _ = fixture(folder)
    body = bpy.data.objects["FixtureBody"]
    sibling = body.copy()
    sibling.name = "UnmaskedSharedBody"
    sibling.location.x = .7
    bpy.context.scene.collection.objects.link(sibling)
    assert sibling.data == body.data
    body.vertex_groups.new(name="VisibleMask").add([0, 1, 4, 5], 1, "REPLACE")
    body.modifiers.new("AuthoredMask", "MASK").vertex_group = "VisibleMask"
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    shared = run({"source": str(source), "output": str(folder / "fixture-shared-mesh-mask"),
                  "preset": "preserve", "options": {"preview": False, "bake_materials": False}})
    assert shared["status"] != "blocked", shared["issues"]
    assert not shared["integrity"]["geometry_errors"]
    assert shared["integrity"]["export_meshes"]["FixtureBody"] == {"vertices": 4, "triangles": 2}
    assert shared["integrity"]["export_meshes"]["UnmaskedSharedBody"] == {"vertices": 8, "triangles": 12}
    shapes = bpy.data.objects["UnmaskedSharedBody"].data.shape_keys
    assert abs(max((point.co - base.co).length for point, base in zip(shapes.key_blocks["Smile"].data, shapes.reference_key.data)) - .03) < 1e-5
    # Numbered GAME joints have an explicit naming convention. Flattened
    # weighted branches must regain a portable tree without moving bind bones.
    source, _ = fixture(folder)
    rig, body = bpy.data.objects["FixtureRig"], bpy.data.objects["FixtureBody"]
    game_names = {"Hips": "GAME_C1_hip1", "Spine": "GAME_C1_spine1", "Chest": "GAME_C1_spine2",
                  "Neck": "GAME_C1_neck1", "Head": "GAME_C1_head1"}
    for side in ("Left", "Right"):
        for human, joint in (("Shoulder", "clav1"), ("UpperArm", "arm1"), ("LowerArm", "arm2"),
                             ("Hand", "arm3"), ("UpperLeg", "leg1"), ("LowerLeg", "leg2"), ("Foot", "leg3")):
            game_names[side + human] = "GAME_" + side[0] + "1_" + joint
    for original, name in game_names.items():
        rig.data.bones[original].name = name
        group = body.vertex_groups.get(original) or body.vertex_groups.get(name) or body.vertex_groups.new(name=name)
        group.name = name
        group.add([0], .01, "ADD")
    mapped, missing, ambiguous = map_humanoid(list(game_names.values()))
    assert not missing and not ambiguous, (missing, ambiguous)
    assert {item["humanName"]: item["boneName"] for item in mapped} == game_names
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name in game_names.values():
        rig.data.edit_bones[name].parent = None
    bpy.ops.object.mode_set(mode="OBJECT")
    heads = {bone.name: tuple(bone.head_local) for bone in rig.data.bones}
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    game = run({"source": str(source), "output": str(folder / "fixture-game-joints"),
                "preset": "preserve", "options": {"preview": False}})
    assert game["status"] != "blocked", game["issues"]
    assert not game["missing_required_humanoid"]
    assert game["generated_hierarchy_repair"]["changes"]
    assert game["generated_hierarchy_repair"]["rest_matrix_max_delta"] < 1e-5
    assert not game["integrity"]["missing_bones"] and not game["integrity"]["missing_weighted_bones"]
    exported = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    assert exported.data.bones[game_names["Hips"]] in exported.data.bones[game_names["LeftFoot"]].parent_recursive
    assert exported.data.bones[game_names["Chest"]] in exported.data.bones[game_names["RightHand"]].parent_recursive
    assert "vrchat_spine_repair" not in game
    for bone in exported.data.bones:
        assert max(abs(bone.head_local[index] - heads[bone.name][index]) for index in range(3)) < 1e-4, bone.name
    # VRChat's upload check requires the neck and both shoulders to be direct
    # children of UpperChest. Extra GAME spine segments must not leave the neck
    # one ancestor lower, and the breast chain on those segments must stay put.
    source, _ = fixture(folder)
    rig, body = bpy.data.objects["FixtureRig"], bpy.data.objects["FixtureBody"]
    rename = {"Hips": "GAME_C1_HIP1", "Spine": "GAME_C1_SPINE1", "Chest": "GAME_C1_SPINE2",
              "Neck": "GAME_C1_NECK1", "Head": "GAME_C1_HEAD1"}
    for side in ("Left", "Right"):
        for human, joint in (("Shoulder", "clav1"), ("UpperArm", "arm1"), ("LowerArm", "arm2"),
                             ("Hand", "arm3"), ("UpperLeg", "leg1"), ("LowerLeg", "leg2"), ("Foot", "leg3")):
            rename[side + human] = "GAME_" + side[0] + "1_" + joint
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for original, name in rename.items():
        rig.data.edit_bones[original].name = name
    spine3 = rig.data.edit_bones.new("GAME_C1_SPINE3")
    spine3.head, spine3.tail = (0, 0, 1.42), (0, 0, 1.5)
    spine3.parent = rig.data.edit_bones["GAME_C1_SPINE2"]
    spine4 = rig.data.edit_bones.new("GAME_C1_SPINE4")
    spine4.head, spine4.tail = (0, 0, 1.5), (0, 0, 1.58)
    spine4.parent = spine3
    spine5 = rig.data.edit_bones.new("GAME_C1_SPINE5")
    spine5.head, spine5.tail = (0, 0, 1.58), (0, 0, 1.66)
    spine5.parent = spine4
    rig.data.edit_bones["GAME_C1_NECK1"].parent = spine5
    rig.data.edit_bones["Breast.L"].parent = spine5
    for side in ("L", "R"):
        rig.data.edit_bones["GAME_" + side + "1_clav1"].parent = spine4
    spine_heads = {bone.name: tuple(bone.head) for bone in rig.data.edit_bones}
    bpy.ops.object.mode_set(mode="OBJECT")
    for original, name in rename.items():
        group = body.vertex_groups.get(original)
        if group:
            group.name = name
        else:
            body.vertex_groups.new(name=name).add([0], .01, "ADD")
    body.vertex_groups.new(name="GAME_C1_SPINE3").add([0], .01, "ADD")
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    extra_spine = run({"source": str(source), "output": str(folder / "fixture-extra-spine"),
                       "preset": "preserve", "options": {"preview": False, "bake_materials": False}})
    assert extra_spine["status"] != "blocked", extra_spine["issues"]
    assert not any(item["code"] == "vrchat_spine_hierarchy" for item in extra_spine["issues"]), extra_spine["issues"]
    repair = extra_spine["vrchat_spine_repair"]
    assert repair["torso"] == "GAME_C1_SPINE3" and repair["changes"] == [
        {"bone": "GAME_C1_NECK1", "human": "Neck", "old_parent": "GAME_C1_SPINE5", "parent": "GAME_C1_SPINE3"},
        {"bone": "GAME_L1_clav1", "human": "LeftShoulder", "old_parent": "GAME_C1_SPINE4", "parent": "GAME_C1_SPINE3"},
        {"bone": "GAME_R1_clav1", "human": "RightShoulder", "old_parent": "GAME_C1_SPINE4", "parent": "GAME_C1_SPINE3"}]
    mapping = {item["humanName"]: item["boneName"] for item in extra_spine["humanoid"]}
    assert mapping["UpperChest"] == "GAME_C1_SPINE3" and mapping["Chest"] == "GAME_C1_SPINE2"
    assert mapping["Neck"] == "GAME_C1_NECK1" and mapping["LeftShoulder"] == "GAME_L1_clav1"
    exported = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    assert exported.data.bones["GAME_C1_NECK1"].parent.name == "GAME_C1_SPINE3"
    assert exported.data.bones["GAME_L1_clav1"].parent.name == "GAME_C1_SPINE3"
    assert exported.data.bones["GAME_R1_clav1"].parent.name == "GAME_C1_SPINE3"
    assert exported.data.bones["GAME_L1_arm1"].parent.name == "GAME_L1_clav1"
    assert exported.data.bones["GAME_R1_arm1"].parent.name == "GAME_R1_clav1"
    assert exported.data.bones["GAME_C1_HEAD1"].parent.name == "GAME_C1_NECK1"
    assert exported.data.bones["Breast.L"].parent.name == "GAME_C1_SPINE5"
    assert exported.data.bones["GAME_C1_SPINE5"].parent.name == "GAME_C1_SPINE4"
    for bone in exported.data.bones:
        assert max(abs(bone.head_local[index] - spine_heads[bone.name][index]) for index in range(3)) < 1e-4, bone.name
    # The same direct-parent rule applies when the torso bone is Chest because
    # no UpperChest alias exists. The unrelated segment stays in the skeleton.
    source, _ = fixture(folder)
    rig = bpy.data.objects["FixtureRig"]
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    rib = rig.data.edit_bones.new("Rib")
    rib.head, rib.tail = (0, 0, 1.42), (0, 0, 1.48)
    rib.parent = rig.data.edit_bones["Chest"]
    rig.data.edit_bones["Neck"].parent = rib
    rig.data.edit_bones["LeftShoulder"].parent = rib
    rig.data.edit_bones["RightShoulder"].parent = rib
    rib_heads = {bone.name: tuple(bone.head) for bone in rig.data.edit_bones}
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    chest_parent = run({"source": str(source), "output": str(folder / "fixture-chest-neck-gap"),
                        "preset": "preserve", "options": {"preview": False, "bake_materials": False}})
    assert chest_parent["status"] != "blocked", chest_parent["issues"]
    assert not any(item["code"] == "vrchat_spine_hierarchy" for item in chest_parent["issues"]), chest_parent["issues"]
    assert chest_parent["vrchat_spine_repair"]["torso"] == "Chest"
    assert chest_parent["vrchat_spine_repair"]["changes"] == [
        {"bone": "Neck", "human": "Neck", "old_parent": "Rib", "parent": "Chest"},
        {"bone": "LeftShoulder", "human": "LeftShoulder", "old_parent": "Rib", "parent": "Chest"},
        {"bone": "RightShoulder", "human": "RightShoulder", "old_parent": "Rib", "parent": "Chest"}]
    assert "UpperChest" not in {item["humanName"] for item in chest_parent["humanoid"]}
    exported = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    assert exported.data.bones["Neck"].parent.name == "Chest"
    assert exported.data.bones["LeftShoulder"].parent.name == "Chest"
    assert exported.data.bones["RightShoulder"].parent.name == "Chest"
    assert exported.data.bones["LeftUpperArm"].parent.name == "LeftShoulder"
    assert exported.data.bones["Rib"].parent.name == "Chest"
    assert exported.data.bones["Head"].parent.name == "Neck"
    for bone in exported.data.bones:
        assert max(abs(bone.head_local[index] - rib_heads[bone.name][index]) for index in range(3)) < 1e-4, bone.name
    # Sparse skins must not bind every unused jiggle/control bone into every
    # mesh. Keep all transforms and exact weighted bindings/morphs instead.
    from io_scene_fbx import parse_fbx, encode_bin
    source, _ = fixture(folder)
    rig, body = bpy.data.objects["FixtureRig"], bpy.data.objects["FixtureBody"]
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for index in range(96):
        bone = rig.data.edit_bones.new("Unweighted_Extra_" + str(index))
        bone.head, bone.tail = (0, index * .001, 1), (0, index * .001, 1.1)
        bone.parent = rig.data.edit_bones["Hips"]
    bpy.ops.object.mode_set(mode="OBJECT")
    sibling = body.copy()
    sibling.data = body.data.copy()
    sibling.name = "SparseSkinSibling"
    sibling.location.x = .7
    bpy.context.scene.collection.objects.link(sibling)
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    original_writer = encode_bin.write
    sparse = run({"source": str(source), "output": str(folder / "fixture-sparse-skin"),
                  "preset": "preserve", "options": {"preview": False, "bake_materials": False}})
    assert encode_bin.write is original_writer
    assert sparse["status"] != "blocked", sparse["issues"]
    assert sparse["fbx_empty_skin_clusters_removed"] == (len(sparse["export_bones"]) - 3) * 2
    assert not sparse["integrity"]["missing_bones"] and not sparse["integrity"]["missing_weighted_bones"]
    assert not sparse["integrity"]["missing_shape_keys"] and not sparse["integrity"]["geometry_errors"]
    tree, _ = parse_fbx.parse(str(folder / "fixture-sparse-skin" / "model.fbx"))
    globals_ = next(element for element in tree.elems if element.id == b"GlobalSettings")
    properties = next(element for element in globals_.elems if element.id == b"Properties70")
    assert next(element.props[4] for element in properties.elems if element.props[0] == b"UnitScaleFactor") == 1.0
    objects = next(element for element in tree.elems if element.id == b"Objects")
    clusters = [element for element in objects.elems if element.id == b"Deformer" and element.props[2] == b"Cluster"]
    assert len(clusters) == 6
    assert all(len(next(child for child in element.elems if child.id == b"Indexes").props[0]) == 8 for element in clusters)
    models = [element for element in objects.elems if element.id == b"Model" and element.props[2] == b"LimbNode"]
    assert len(models) == len(sparse["export_bones"])
    exported = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    for index in range(96):
        bone = exported.data.bones["Unweighted_Extra_" + str(index)]
        assert max(abs(actual - expected) for actual, expected in zip(bone.head_local, (0, index * .001, 1))) < 1e-4
    for mesh_name in ("FixtureBody", "SparseSkinSibling"):
        mesh = bpy.data.objects[mesh_name]
        shapes = mesh.data.shape_keys
        assert abs(max((point.co - base.co).length for point, base in zip(shapes.key_blocks["Smile"].data, shapes.reference_key.data)) - .03) < 1e-5
        for vertex in mesh.data.vertices:
            weights = {mesh.vertex_groups[group.group].name: group.weight for group in vertex.groups}
            assert abs(weights["Hips"] - .6) < 1e-5
            assert abs(weights["Breast.L"] - .2) < 1e-5 and abs(weights["Butt.L"] - .2) < 1e-5
    # A shader that is not one Principled node still has to produce Unity color.
    source, _ = fixture(folder)
    tree = bpy.data.materials["FixtureSkin"].node_tree
    tree.nodes.clear()
    emission = tree.nodes.new("ShaderNodeEmission")
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    painted = bpy.data.images.new("CustomEmit", width=8, height=8, alpha=True)
    painted.pixels[:] = [channel for y in range(8) for x in range(8)
                         for channel in ((1, 0, .2, 1) if x < 4 else (0, .8, .9, 1))]
    painted.pack()
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = painted
    tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    emitted = run({"source": str(source), "output": str(folder / "fixture-custom-emission"), "preset": "balanced",
                   "options": {"preview": False, "bake_materials": "auto", "bake_size": 64}})
    entry = emitted["materials"][0]
    assert entry.get("base_color_texture") and entry.get("emission_texture"), emitted["issues"]
    assert any(item.get("method") == "surface_appearance" for item in emitted.get("baked_materials", [])), emitted["issues"]
    baked = bpy.data.images.load(str(folder / "fixture-custom-emission" / entry["base_color_texture"]))
    assert max(baked.pixels[0::4]) > .7 and max(baked.pixels[2::4]) > .5
    source, _ = fixture(folder)
    tree = bpy.data.materials["FixtureSkin"].node_tree
    tree.nodes.clear()
    glossy = tree.nodes.new("ShaderNodeBsdfGlossy")
    glossy.inputs["Color"].default_value = (.05, .8, .15, 1)
    glossy.inputs["Roughness"].default_value = .4
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(glossy.outputs["BSDF"], output.inputs["Surface"])
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    glossy_result = run({"source": str(source), "output": str(folder / "fixture-custom-glossy"), "preset": "balanced",
                         "options": {"preview": False, "bake_materials": "auto", "bake_size": 64}})
    entry = glossy_result["materials"][0]
    assert entry.get("base_color_texture") and not entry.get("emission_texture"), glossy_result["issues"]
    baked = bpy.data.images.load(str(folder / "fixture-custom-glossy" / entry["base_color_texture"]))
    covered = [(red, green) for red, green in zip(baked.pixels[0::4], baked.pixels[1::4]) if red + green > .1]
    assert covered and sum(green for _, green in covered) / len(covered) > sum(red for red, _ in covered) / len(covered) + .15
    accessory_parts(folder)
    # Leave a stable ordinary input for CLI/UI smoke checks after this suite.
    fixture(folder)
    print("AVATARFORGE_SMOKE_PASS " + json.dumps({"bones": len(expected_bones), "shape_keys": 2, "presets": 3, "fbx_roundtrip": True, "udim_atlas_pixels": True, "material_bake_pixels": True, "batch_bake_pixels": True, "generated_hierarchy_rest_positions": True, "dropped_influence_rejected": True, "decimation_influence_fallback": True, "authored_defaults_visibility_masks_render_uv": True, "repeating_tile_bake_pixels": True, "eight_uv_preservation": True, "scalar_alpha": True, "reopened_unmasked_backup": True, "reopened_source_material_backup": True, "video_preview_state_restored": True, "reserved_basis_morph_deformation_defaults": True, "autopack_portable_texture_pixels": True, "collision_body_selected": True, "explicit_hidden_collection_geometry": True, "disconnected_surface_review": True, "subdivision_review": True, "shared_mesh_mask_isolation": True, "numbered_game_joint_tree_rest_positions": True, "vrchat_extra_spine_direct_parent": True, "vrchat_chest_neck_direct_parent": True, "sparse_skin_cluster_bone_weight_morph_retention": True, "custom_surface_appearance_bake": True, "accessory_parts_parented": True, "preserve_source_bake_resolution": True, "integer_vector_custom_properties": True, "unselectable_mesh_exported": True}))


if __name__ == "__main__":
    main()

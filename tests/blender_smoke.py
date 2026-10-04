"""Real Blender/FBX regression, no third-party assets or test dependencies.

blender --background --factory-startup --disable-autoexec --python-exit-code 1
  --python tests/blender_smoke.py -- /absolute/owned/validation-folder
"""
from pathlib import Path
import importlib.util
import json
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "avatarforge"))
from blender_worker import constant_socket_value, covered_uniform_pixels, run, export_and_verify, preview
from bone_aliases import map_humanoid


def fixture(folder):
    bpy.ops.wm.read_factory_settings(use_empty=True)
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
    source = folder / "fixture.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(source))
    return source, {name for name, *_ in definitions if name != "CTRL_Unused"}


def main():
    folder = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    source, expected_bones = fixture(folder)
    scene = bpy.context.scene
    state = (scene.render.engine, scene.cycles.device, scene.cycles.samples,
             scene.cycles.use_denoising, scene.camera, scene.world,
             scene.render.resolution_x, scene.render.resolution_y,
             scene.render.resolution_percentage, scene.render.filepath,
             scene.render.image_settings.file_format)
    objects = set(bpy.data.objects)
    preview_report = {"issues": []}
    preview([bpy.data.objects["FixtureBody"]], folder, preview_report)
    assert preview_report["preview_renderer"] == "cycles_cpu", preview_report
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
    # Leave a stable ordinary input for CLI/UI smoke checks after this suite.
    fixture(folder)
    print("AVATARFORGE_SMOKE_PASS " + json.dumps({"bones": len(expected_bones), "shape_keys": 2, "presets": 3, "fbx_roundtrip": True, "udim_atlas_pixels": True, "material_bake_pixels": True, "batch_bake_pixels": True, "generated_hierarchy_rest_positions": True, "dropped_influence_rejected": True, "decimation_influence_fallback": True, "authored_defaults_visibility_masks_render_uv": True, "repeating_tile_bake_pixels": True, "eight_uv_preservation": True, "scalar_alpha": True, "reopened_unmasked_backup": True, "reopened_source_material_backup": True}))


if __name__ == "__main__":
    main()

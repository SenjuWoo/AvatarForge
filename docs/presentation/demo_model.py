"""Build the README demo character: a small procedural mannequin, CC0, no downloaded assets.

blender -b --factory-startup --python docs/presentation/demo_model.py -- <out.blend>

It has a standard humanoid armature, a secondary ponytail chain, two breast bones,
three shape keys and two textured materials, so a real AvatarForge conversion has
something to preserve. Only the presentation images use it; tests build their own fixtures.
"""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

out = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.use_preferences_save = False

BONES = [
    ("Hips", None, (0, 0, .95), (0, 0, 1.05)),
    ("Spine", "Hips", (0, 0, 1.05), (0, 0, 1.2)),
    ("Chest", "Spine", (0, 0, 1.2), (0, 0, 1.38)),
    ("Neck", "Chest", (0, 0, 1.42), (0, 0, 1.52)),
    ("Head", "Neck", (0, 0, 1.52), (0, 0, 1.78)),
    ("Hair.001", "Head", (0, .1, 1.66), (0, .16, 1.52)),
    ("Hair.002", "Hair.001", (0, .16, 1.52), (0, .17, 1.36)),
    ("Hair.003", "Hair.002", (0, .17, 1.36), (0, .16, 1.2)),
]
for side, s in (("Left", 1), ("Right", -1)):
    BONES += [
        (side + "UpperLeg", "Hips", (s * .09, 0, .93), (s * .1, 0, .52)),
        (side + "LowerLeg", side + "UpperLeg", (s * .1, 0, .52), (s * .1, .01, .1)),
        (side + "Foot", side + "LowerLeg", (s * .1, .01, .1), (s * .1, -.12, .03)),
        (side + "Shoulder", "Chest", (s * .03, 0, 1.38), (s * .15, 0, 1.38)),
        (side + "UpperArm", side + "Shoulder", (s * .17, 0, 1.38), (s * .43, 0, 1.38)),
        (side + "LowerArm", side + "UpperArm", (s * .43, 0, 1.38), (s * .66, 0, 1.38)),
        (side + "Hand", side + "LowerArm", (s * .66, 0, 1.38), (s * .76, 0, 1.38)),
        ("Breast." + side[0], "Chest", (s * .07, -.06, 1.27), (s * .08, -.16, 1.26)),
    ]

arm_data = bpy.data.armatures.new("DemoSkeleton")
rig = bpy.data.objects.new("DemoRig", arm_data)
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, parent, head, tail in BONES:
    bone = arm_data.edit_bones.new(name)
    bone.head, bone.tail = head, tail
    if parent:
        bone.parent = arm_data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
heads = {name: (Vector(h), Vector(t)) for name, _, h, t in BONES}

bm = bmesh.new()
uv = bm.loops.layers.uv.new("UVMap")
deform = bm.verts.layers.deform.new()
names = [name for name, *_ in BONES]
parts = []  # (bone, material index, [BMVert])


def add(bone, geometry, material=0, blend_to=None):
    """Append geometry rigidly weighted to one bone, optionally blended to its parent along the bone."""
    result = geometry()
    # Use the operator's own vertex list: the sphere op frees pole duplicates and later
    # primitives reuse those slots, so "everything after the old length" is not the new part.
    new = list(result["verts"])
    for face in {f for v in new for f in v.link_faces}:
        face.material_index = material
        face.smooth = True
    head, tail = heads[bone]
    axis = tail - head
    for vertex in new:
        if blend_to:
            t = max(0.0, min(1.0, (vertex.co - head).dot(axis) / axis.length_squared))
            w = min(1.0, .5 + t)
            vertex[deform][names.index(bone)] = w
            if w < 1:
                vertex[deform][names.index(blend_to)] = 1 - w
        else:
            vertex[deform][names.index(bone)] = 1.0
    parts.append((bone, material, new))
    return result


def limb(bone, radius, radius2=None, material=0, blend_to=None, segments=14):
    head, tail = heads[bone]
    direction = tail - head
    rotation = direction.to_track_quat("Z", "Y").to_matrix().to_4x4()
    matrix = Matrix.Translation((head + tail) / 2) @ rotation
    return add(bone, lambda: bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius,
               radius2=radius2 or radius, depth=direction.length * 1.08, matrix=matrix, calc_uvs=True),
               material, blend_to)


def ball(bone, center, radius, scale=(1, 1, 1), material=0):
    matrix = Matrix.Translation(center) @ Matrix.Diagonal((*scale, 1))
    return add(bone, lambda: bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=radius,
               matrix=matrix, calc_uvs=True), material)


ball("Hips", (0, 0, 1.0), .15, (1.05, .75, .7))
limb("Spine", .13, .14, blend_to="Hips")
limb("Chest", .145, .15, blend_to="Spine")
limb("Neck", .05, .045, blend_to="Chest")
ball("Head", (0, -.01, 1.63), .12, (.9, 1, 1.12))
EYES = (ball("Head", (.04, -.112, 1.655), .02, material=1), ball("Head", (-.04, -.112, 1.655), .02, material=1))
MOUTH = ball("Head", (0, -.117, 1.585), .016, (2.2, .6, .5), material=1)
for side, s in (("L", 1), ("R", -1)):
    ball("Breast." + side, (s * .075, -.1, 1.27), .07, (1, .9, .95))
for side in ("Left", "Right"):
    limb(side + "UpperLeg", .075, .06, blend_to="Hips")
    limb(side + "LowerLeg", .055, .042, blend_to=side + "UpperLeg")
    limb(side + "Foot", .045, .04, blend_to=side + "LowerLeg")
    limb(side + "Shoulder", .05, .05, blend_to="Chest")
    limb(side + "UpperArm", .045, .04, blend_to=side + "Shoulder")
    limb(side + "LowerArm", .038, .032, blend_to=side + "UpperArm")
    limb(side + "Hand", .035, .03)
ball("Head", (0, .04, 1.69), .125, (1.0, 1.0, .9), material=1)  # hair cap
limb("Hair.001", .055, .05, material=1)
limb("Hair.002", .05, .04, material=1, blend_to="Hair.001")
limb("Hair.003", .04, .015, material=1, blend_to="Hair.002")

bm.verts.index_update()  # mesh vertex order after to_mesh
eye_parts = [([v.index for v in eye["verts"]], sum((v.co for v in eye["verts"]), Vector()) / len(eye["verts"])) for eye in EYES]
mouth_part = [v.index for v in MOUTH["verts"]]
chest_part = [v.index for v in next(vs for bone, m, vs in parts if bone == "Chest")]
mesh_data = bpy.data.meshes.new("DemoBodyData")
bm.to_mesh(mesh_data)
bm.free()
body = bpy.data.objects.new("DemoBody", mesh_data)
bpy.context.scene.collection.objects.link(body)
body.parent = rig
for name in names:
    body.vertex_groups.new(name=name)
body.modifiers.new("Armature", "ARMATURE").object = rig

# Shape keys: a smile and a blink on the face, and a breath on the chest.
body.shape_key_add(name="Basis", from_mix=False)
smile = body.shape_key_add(name="Smile", from_mix=False)
blink = body.shape_key_add(name="Blink", from_mix=False)
breathe = body.shape_key_add(name="Breathe", from_mix=False)
for key in (smile, blink, breathe):
    key.value = 0.0  # neutral by default; the shapes are still in the file
for indices, center in eye_parts:  # Blink squashes each eye to a slit
    for index in indices:
        blink.data[index].co.z = center.z + (mesh_data.vertices[index].co.z - center.z) * .12
for index in mouth_part:  # Smile lifts and widens the mouth corners
    co = mesh_data.vertices[index].co
    smile.data[index].co.z = co.z + 16 * co.x * co.x
    smile.data[index].co.x = co.x * 1.15
for index in chest_part:
    co = mesh_data.vertices[index].co
    breathe.data[index].co.x = co.x * 1.08
    breathe.data[index].co.y = co.y * 1.12


def painted(name, size, a, b, stripes):
    image = bpy.data.images.new(name, width=size, height=size)
    pixels = []
    for y in range(size):
        t = y / (size - 1)
        for x in range(size):
            band = .5 + .5 * math.sin((x + y) / size * math.tau * stripes)
            k = t * .8 + band * .2
            pixels += [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k, 1.0]
    image.pixels[:] = pixels
    image.pack()
    return image


for label, colors, stripes in (("DemoSuit", ((.08, .55, .42), (.36, .29, .66)), 6), ("DemoHair", ((.22, .16, .45), (.55, .45, .9)), 14)):
    material = bpy.data.materials.new(label)
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = painted(label + "Albedo", 128, *colors, stripes)
    shader = material.node_tree.nodes["Principled BSDF"]
    shader.inputs["Roughness"].default_value = .55
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    mesh_data.materials.append(material)

out.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(out))
print("DEMO_MODEL", out.name, len(mesh_data.vertices), "verts", len(arm_data.bones), "bones",
      len(body.data.shape_keys.key_blocks) - 1, "shape keys")

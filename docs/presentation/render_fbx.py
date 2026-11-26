"""Render a converted model.fbx for the README: textured front, skeleton overlay, face shape keys.

blender --background --factory-startup --disable-autoexec --python docs/presentation/render_fbx.py -- <model.fbx> <out dir>

Reads only the FBX that AvatarForge exported, so the images show what Unity would receive.
When ImageMagick (`magick`) is on PATH, the panels are also laid out into <out dir>/conversion-render.png
with the counts from the report.json beside the FBX.
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import bpy
from mathutils import Vector

fbx, out = (Path(p).resolve() for p in sys.argv[sys.argv.index("--") + 1:][:2])
out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.preferences.use_preferences_save = False
bpy.ops.import_scene.fbx(filepath=str(fbx))
scene = bpy.context.scene
rig = next(o for o in scene.objects if o.type == "ARMATURE")
meshes = [o for o in scene.objects if o.type == "MESH"]

scene.render.engine = "BLENDER_WORKBENCH"
scene.render.film_transparent = True
scene.render.resolution_percentage = 100
shading = scene.display.shading
shading.light = "STUDIO"
shading.color_type = "TEXTURE"
shading.show_specular_highlight = True
shading.show_cavity = True
shading.cavity_type = "WORLD"
scene.display.render_aa = "16"
scene.view_settings.view_transform = "Standard"

corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
low = Vector(map(min, zip(*corners)))
high = Vector(map(max, zip(*corners)))
center = (low + high) / 2
height = high.z - low.z
frame = max(height, high.x - low.x) * 1.06  # T-pose arms are about as wide as the body is tall

camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(camera)
scene.camera = camera
camera.data.type = "ORTHO"


def shoot(name, yaw, target, scale, size):
    """Orthographic shot around the vertical axis; yaw 0 looks at the model's front (-Y)."""
    angle = math.radians(yaw)
    camera.location = target + Vector((math.sin(angle), -math.cos(angle), 0)) * 10
    camera.rotation_euler = (math.radians(90), 0, angle)
    camera.data.ortho_scale = scale
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.filepath = str(out / name)
    bpy.ops.render.render(write_still=True)


shoot("front.png", 0, center, frame, (720, 720))

# Skeleton overlay: one tapered stick per exported bone, drawn as its own transparent pass.
for o in meshes:
    o.hide_render = True
stick_mesh = bpy.data.meshes.new("Stick")
stick_mesh.from_pydata([(0, 0, 0), (.6, 0, .18), (0, .6, .18), (-.6, 0, .18), (0, -.6, .18), (0, 0, 1)], [],
                       [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1), (5, 2, 1), (5, 3, 2), (5, 4, 3), (5, 1, 4)])
secondary = {"Hair", "Breast"}
for bone in rig.data.bones:
    head = rig.matrix_world @ bone.head_local
    tail = rig.matrix_world @ bone.tail_local
    stick = bpy.data.objects.new("Bone_" + bone.name, stick_mesh)
    scene.collection.objects.link(stick)
    length = (tail - head).length
    stick.location = head
    stick.rotation_mode = "QUATERNION"
    stick.rotation_quaternion = (tail - head).to_track_quat("Z", "Y")
    stick.scale = (length * .22, length * .22, length)
    stick.color = (.95, .62, .3, 1) if any(bone.name.startswith(s) for s in secondary) else (.57, .91, .76, 1)
shading.color_type = "OBJECT"
shading.show_cavity = False
shading.show_xray = False
shoot("bones.png", 35, center, frame, (720, 720))
for o in scene.objects:
    if o.name.startswith("Bone_"):
        o.hide_render = True
for o in meshes:
    o.hide_render = False
shading.color_type = "TEXTURE"
shading.show_cavity = True
shoot("mesh34.png", 35, center, frame, (720, 720))

# Face close-up, neutral and with the exported Smile + Blink shape keys at full weight.
head_bone = rig.data.bones.get("Head")
face = rig.matrix_world @ ((head_bone.head_local + head_bone.tail_local) / 2) if head_bone else high - Vector((0, 0, height * .1))
shoot("face_neutral.png", 0, face, height * .2, (380, 380))
for o in meshes:
    keys = o.data.shape_keys
    if keys:
        for block in keys.key_blocks[1:]:
            block.value = 1.0 if block.name in {"Smile", "Blink"} else 0.0
shoot("face_keys.png", 0, face, height * .2, (380, 380))
print("RENDERED", sorted(p.name for p in out.glob("*.png")), len(rig.data.bones), "bones")

# One README image: the three panels plus the conversion's own counts from report.json.
report = json.loads((fbx.parent / "report.json").read_text(encoding="utf-8"))
summary = report["summary"]
magick = shutil.which("magick")
if magick:
    fonts = Path("C:/Windows/Fonts")
    bold, regular = (((fonts / "segoeuib.ttf").as_posix(), (fonts / "segoeui.ttf").as_posix()) if (fonts / "segoeui.ttf").exists()
                     else ("DejaVu-Sans-Bold", "DejaVu-Sans"))
    secondary_count = sum(1 for b in rig.data.bones if any(b.name.startswith(s) for s in secondary))
    keys = ", ".join(n for entry in report["shape_keys"] for n in entry["names"])
    cmd = [magick, "-size", "1600x900", "xc:#0c1017", "-fill", "#141b26",
           "-draw", "roundrectangle 24,128 704,846 18,18", "-draw", "roundrectangle 724,128 1404,846 18,18",
           "-draw", "roundrectangle 1424,128 1576,846 18,18",
           "(", str(out / "front.png"), "-resize", "660x660", ")", "-geometry", "+34+140", "-composite",
           "(", str(out / "mesh34.png"), str(out / "bones.png"), "-composite", "-resize", "660x660", ")",
           "-geometry", "+734+140", "-composite",
           "(", str(out / "face_neutral.png"), "-resize", "140x140", ")", "-geometry", "+1430+190", "-composite",
           "(", str(out / "face_keys.png"), "-resize", "140x140", ")", "-geometry", "+1430+420", "-composite",
           "-font", bold, "-fill", "#e7edf5", "-pointsize", "34",
           "-annotate", "+34+62", f"Converted model.fbx, preset {report['preset']}, verdict {report['status']}",
           "-font", regular, "-fill", "#9aa8bb", "-pointsize", "21",
           "-annotate", "+34+100", f"Generated test mannequin, re-imported and rendered in Blender {report.get('blender_version', '')}",
           "-fill", "#91e8c2", "-pointsize", "22",
           "-annotate", "+44+830", f"{summary['triangles']} triangles · {summary['materials']} materials · {summary['textures']} textures",
           "-annotate", "+744+830", f"{summary['bones']} bones kept · {secondary_count} secondary (orange)",
           "-pointsize", "17", "-fill", "#9aa8bb",
           "-annotate", "+1434+176", "neutral", "-annotate", "+1434+406", "Smile + Blink",
           "-annotate", "+1434+600", f"{summary['shape_keys']} shape keys", "-annotate", "+1434+624", "exported:",
           "-fill", "#91e8c2", "-annotate", "+1434+650", keys.replace(", ", chr(10)),
           "-depth", "8", str(out / "conversion-render.png")]
    subprocess.run(cmd, check=True)
    print("COMPOSED", out / "conversion-render.png")

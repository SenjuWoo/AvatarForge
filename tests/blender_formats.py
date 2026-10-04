"""Actual VRM1 and SMD/VTA -> worker -> FBX regressions using generated assets.

Install the pinned extended importers, then run in a disposable Blender profile:
blender --background --factory-startup --disable-autoexec --python-exit-code 1
  --python tests/blender_formats.py
"""
from pathlib import Path
import hashlib
import json
import re
import sys
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "avatarforge"))
sys.path.insert(0, str(ROOT / "tests"))
from blender_smoke import fixture
from blender_worker import enable_addons, run

ADDONS = [str(ROOT / ".runtime" / "addons")]
WEIGHTED = {"Hips", "Breast.L", "Butt.L"}
SHAPES = {"Smile": .03, "Blink": .02}


def open_model(source, suffix, module):
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    registration = {"issues": []}
    enable_addons(ADDONS, registration, suffix)
    assert module in registration["enabled_addons"], registration
    assert not registration["issues"], registration
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and obj.data.shape_keys:
            obj.show_only_shape_key = False
            obj.active_shape_key_index = 0
            for key in obj.data.shape_keys.key_blocks:
                key.value = 0
    bpy.context.view_layer.update()


def convert(source, output, *, shapes=True, options=None):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = run({
        "source": str(source), "output": str(output), "preset": "preserve",
        "addon_paths": ADDONS, "options": {"preview": False, **(options or {})},
    })
    assert result["status"] != "blocked", result
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert (output / "model.fbx").is_file()
    assert result["summary"]["triangles"] == 12, result
    assert result["summary"]["shape_keys"] == (2 if shapes else 0), result
    integrity = result["integrity"]
    assert integrity["fbx_roundtrip_verified"], integrity
    for field in ("missing_bones", "missing_weighted_bones", "missing_shape_keys"):
        assert not integrity[field], integrity
    assert WEIGHTED.issubset(integrity["export_weighted_bones"]), integrity
    # Inspect the actual FBX reimport scene: named keys without deformation fail.
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    assert len(meshes) == 1
    mesh = meshes[0]
    for vertex in mesh.data.vertices:
        weights = {mesh.vertex_groups[group.group].name: group.weight
                   for group in vertex.groups if group.weight > 1e-6}
        assert set(weights) == WEIGHTED, weights
        for name, expected in {"Hips": .6, "Breast.L": .2, "Butt.L": .2}.items():
            assert abs(weights[name] - expected) < 5e-4, weights
    keys = mesh.data.shape_keys
    if shapes:
        assert keys is not None
        actual = {key.name: max((point.co - base.co).length for point, base in
                               zip(key.data, keys.reference_key.data))
                  for key in keys.key_blocks if key != keys.reference_key}
        assert set(actual) == set(SHAPES), actual
        for name, distance in SHAPES.items():
            assert abs(actual[name] - distance) < 1e-4, actual
    else:
        assert keys is None or len(keys.key_blocks) <= 1
    rig = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    assert rig.data.bones["Chest"] in rig.data.bones["Breast.L"].parent_recursive
    assert rig.data.bones["Hips"] in rig.data.bones["Butt.L"].parent_recursive
    return result


def smd_routes(model, folder):
    open_model(model, ".smd", "io_scene_valvesource")
    from io_scene_valvesource.utils import State

    destination = folder / "smd-source"
    destination.mkdir()
    bpy.context.scene.vs.export_path = str(destination)
    bpy.context.scene.vs.export_format = "SMD"
    bpy.context.scene.vs.qc_compile = False
    bpy.ops.object.select_all(action="DESELECT")
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
    mesh.hide_viewport = False
    mesh.hide_set(False)
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    mesh_name = mesh.name  # The exporter uses undo and invalidates object pointers.
    State.update_scene()
    # Its exporter uses undo to restore a snapshot from before addon setup.
    # Detach this test process's callbacks before that snapshot removes Scene.vs.
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.depsgraph_update_post):
        for handler in list(handlers):
            if handler.__module__.startswith("io_scene_valvesource."):
                handlers.remove(handler)
    assert "FINISHED" in bpy.ops.export_scene.smd(export_scene=False)
    source = destination / (mesh_name + ".smd")
    sidecar = source.with_suffix(".vta")
    assert source.is_file() and sidecar.is_file()
    carried = set()
    section = None
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() == "nodes":
            section = "nodes"
        elif line.strip() == "end":
            section = None
        elif section == "nodes":
            match = re.match(r'\s*\d+\s+"([^"]+)"\s+-?\d+', line)
            assert match, line
            carried.add(match.group(1))
    assert WEIGHTED.issubset(carried), carried
    digest = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    automatic = convert(source, folder / "smd-matching-vta")
    assert automatic["vta_sidecar"]["status"] == "imported", automatic
    assert automatic["vta_sidecar"]["expected_shape_keys"] == ["Smile", "Blink"]
    assert carried.issubset(automatic["integrity"]["export_bones"]), automatic
    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == digest

    # Different stems need an explicit selection and must never silently claim ready.
    renamed = sidecar.with_name("facial-morphs.vta")
    sidecar.rename(renamed)
    unmatched = convert(source, folder / "smd-unmatched-vta", shapes=False)
    assert unmatched["status"] == "needs_review", unmatched
    assert any(item["code"] == "vta_selection_needed" for item in unmatched["issues"])
    explicit = convert(source, folder / "smd-explicit-vta", options={"vta_filepath": renamed.name})
    assert explicit["vta_sidecar"]["status"] == "imported", explicit
    assert hashlib.sha256(renamed.read_bytes()).hexdigest() == digest
    return {"bones": len(automatic["integrity"]["export_bones"]),
            "weighted_bones": len(automatic["integrity"]["export_weighted_bones"]),
            "shape_keys": 2, "matching_sidecar": True, "explicit_sidecar": True,
            "unmatched_sidecar_review": True, "fbx_deformation_verified": True,
            "fbx_weight_values_verified": True}


def vrm_route(model, folder):
    open_model(model, ".vrm", "io_scene_vrm")
    from io_scene_vrm.common.gltf import parse_glb
    from io_scene_vrm.editor.extension_accessor import get_armature_extension
    from io_scene_vrm.editor.vrm1.ops import assign_vrm1_humanoid_human_bones_automatically

    rig = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    rig_name = rig.name
    extension = get_armature_extension(rig.data)
    extension.spec_version = "1.0"
    meta = extension.vrm1.meta
    meta.vrm_name = "AvatarForge generated fixture"
    meta.version = "1"
    meta.authors.add().value = "AvatarForge tests"
    meta.avatar_permission = "everyone"
    meta.allow_redistribution = True
    meta.modification = "allowModificationRedistribution"
    assert "FINISHED" in assign_vrm1_humanoid_human_bones_automatically(bpy.context, rig)
    assert not extension.vrm1.humanoid.human_bones.error_messages()
    bpy.ops.object.select_all(action="DESELECT")
    for obj in bpy.context.scene.objects:
        if obj.type in {"ARMATURE", "MESH"}:
            obj.hide_viewport = False
            obj.hide_set(False)
            obj.select_set(True)
    bpy.context.view_layer.objects.active = rig
    source = folder / "fixture.vrm"
    assert "FINISHED" in bpy.ops.export_scene.vrm(
        filepath=str(source), armature_object_name=rig_name, use_addon_preferences=False,
        export_only_selections=True, export_all_influences=True, ignore_warning=True,
    )
    # Require a real VRM1 extension and skin data from the native addon exporter.
    document, binary = parse_glb(source.read_bytes())
    assert binary
    assert document["extensions"]["VRMC_vrm"]["specVersion"] == "1.0"
    carried = {document["nodes"][joint]["name"]
               for skin in document["skins"] for joint in skin["joints"]}
    assert WEIGHTED.issubset(carried), carried
    result = convert(source, folder / "vrm-conversion")
    assert carried.issubset(result["integrity"]["export_bones"]), result
    return {"spec_version": "1.0", "bones": len(result["integrity"]["export_bones"]),
            "weighted_bones": len(result["integrity"]["export_weighted_bones"]),
            "shape_keys": 2, "fbx_deformation_verified": True, "fbx_weight_values_verified": True}


def main():
    with tempfile.TemporaryDirectory(prefix="AvatarForgeFormatRegression-") as temporary:
        folder = Path(temporary)
        source, expected = fixture(folder)
        prepared = run({"source": str(source), "output": str(folder / "prepared"),
                        "preset": "preserve", "options": {"preview": False}})
        assert set(prepared["integrity"]["export_bones"]) == expected, prepared
        model = folder / "prepared" / "model.blend"
        evidence = {"smd_vta": smd_routes(model, folder), "vrm": vrm_route(model, folder)}
        print("AVATARFORGE_FORMATS_PASS " + json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()

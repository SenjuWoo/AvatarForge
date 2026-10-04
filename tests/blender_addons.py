"""Register the exact installed importer packages in a disposable Blender process."""
from pathlib import Path
import json
import logging
import sys
import tempfile

import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "avatarforge"))
from blender_worker import detach_source_logging_handlers, enable_addons, operator, run

report = {"issues": []}
enable_addons([str(root / ".runtime" / "addons")], report)
expected = {
    "SourceIO": "sourceio.mdl",
    "io_scene_valvesource": "import_scene.smd",
    "mmd_tools": "mmd_tools.import_model",
    "io_scene_vrm": "import_scene.vrm",
    "io_xnalara": "xps_tools.import_model",
}
report["operators"] = {name: operator(op) is not None for name, op in expected.items()}
print("AVATARFORGE_ADDON_REGISTRATION", json.dumps(report, ensure_ascii=False))
assert set(expected).issubset(report["enabled_addons"]), report
assert all(report["operators"].values()), report
print("AVATARFORGE_ADDONS_PASS")

# SourceIO log streams can outlive their Blender Text datablocks after a reset.
# Use its real logger and retain an orphaned handler to test the shutdown path;
# an unrelated Text-backed handler must not be changed by the worker's cleanup.
from SourceIO.logger import SourceLogMan

source_logger = SourceLogMan().get_logger("AvatarForgeShutdownRegression")
source_logger.info("Create the SourceIO Text stream before removing its datablock")
source_handler = source_logger._bpy_logger
source_text = source_handler.stream
assert isinstance(source_text, bpy.types.Text)
bpy.data.texts.remove(source_text)
unrelated_text = bpy.data.texts.new("AvatarForgeUnrelatedTextLogger")
unrelated_handler = logging.StreamHandler(unrelated_text)
unrelated_handler.setFormatter(logging.Formatter("%(message)s"))
try:
    detach_source_logging_handlers()
    assert source_handler.stream is sys.stdout
    assert unrelated_handler.stream is unrelated_text
    source_handler.flush()  # A stale bpy.Text would raise ReferenceError here.
    print("AVATARFORGE_SOURCE_LOG_CLEANUP_PASS: stale upstream Text stream detached; unrelated logger retained")
finally:
    unrelated_handler.stream = sys.stdout
    unrelated_handler.close()
    bpy.data.texts.remove(unrelated_text)

# The maintained XPS route must import geometry and bone weights, not just register.
with tempfile.TemporaryDirectory(prefix="AvatarForgeXPSRegression-") as temporary:
    folder = Path(temporary)
    source = folder / "fixture.ascii"
    lines = ["2", "Hips", "-1", "0 0 0", "Breast.L", "0", "0 1 0", "1", "1_FixtureMesh", "1", "0", "3"]
    for coordinates, uv, bone in [("0 0 0", "0 0", 0), ("1 0 0", "1 0", 1), ("0 1 0", "0 1", 0)]:
        lines.extend([coordinates, "0 0 1", "255 255 255 255", uv, f"{bone} 0 0 0", "1 0 0 0"])
    lines.extend(["1", "0 1 2"])
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = run({
        "source": str(source), "output": str(folder / "conversion"), "preset": "preserve",
        "addon_paths": [str(root / ".runtime" / "addons")], "options": {"preview": False},
    })
    assert result["status"] != "blocked", result
    assert result["summary"]["triangles"] == 1 and result["summary"]["bones"] == 2, result
    assert not result["integrity"]["missing_bones"] and not result["integrity"]["missing_weighted_bones"], result
    rig = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    assert rig.data.bones["Breast.L"].parent.name == "Hips"
    print("AVATARFORGE_XPS_ROUTE_PASS: ASCII geometry, weighted secondary bone and FBX hierarchy retained")

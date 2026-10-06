"""Blender-only worker. Run in a disposable background process, never live editors."""
from pathlib import Path
from array import array
import importlib
import json
import re
import sys
import traceback
import hashlib
import math
import atexit
import logging
import tempfile
import zipfile

import bpy
import addon_utils
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bone_aliases import map_humanoid

PRESETS = {
    "preserve": {"texture_size": 0, "triangles": None},
    "balanced": {"texture_size": 2048, "triangles": 70000},
    "mobile": {"texture_size": 1024, "triangles": 15000},
}
CONTROLLER = re.compile(r"^(?:MCH|ORG|CTRL|IK|FK|DSP|POLE|LINE|SCALE|STRETCH|TGT|IK2|IK-M|PROPERTIES)(?:[-_]|$)", re.I)
PHYSICS = {
    "breasts": re.compile(r"breast|boob|bust|胸", re.I),
    "butt": re.compile(r"(?:^|[-_])ass(?:[._-]|$)|butt|glute|尻", re.I),
    "hair": re.compile(r"hair|髪", re.I),
    "tail": re.compile(r"tail|しっぽ", re.I),
    "cloth": re.compile(r"skirt|cloth|coat|dress|ribbon|スカート", re.I),
    "ears": re.compile(r"(?:^|[-_])ear(?:[._-]|$)|耳", re.I),
    "secondary": re.compile(r"jiggle|bounce|spring|physics", re.I),
}


class CapabilityError(RuntimeError):
    pass


_ADDON_DEPENDENCIES = []


def mmd_dependencies(package):
    """Load the pinned bundled pure wheel privately; OpenCC needs real data files."""
    wheel = package / "wheels" / "opencc_python_reimplemented-0.1.7-py2.py3-none-any.whl"
    if not wheel.is_file():
        return
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != "41b3b92943c7bed291f448e9c7fad4b577c8c2eae30fcfe5a74edf8818493aa6":
        raise CapabilityError("MMD Tools bundled OpenCC wheel checksum differs from pinned upstream.")
    temporary = tempfile.TemporaryDirectory(prefix="avatarforge-mmd-")
    destination = Path(temporary.name)
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            name = item.filename.replace("\\", "/")
            if name.startswith("/") or any(part in {".", ".."} or ":" in part for part in name.split("/")):
                temporary.cleanup()
                raise CapabilityError("MMD dependency wheel has an unsafe path.")
        archive.extractall(destination)
    sys.path.insert(0, str(destination))
    _ADDON_DEPENDENCIES.append(temporary)


def detach_source_tools_handlers():
    """Pinned Source Tools leaves callbacks behind when its Scene props unload."""
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.depsgraph_update_post):
        for handler in list(handlers):
            if handler.__module__.startswith("io_scene_valvesource."):
                handlers.remove(handler)


def detach_source_logging_handlers(text_type=bpy.types.Text):
    """SourceIO's Text streams die on reset and Blender shutdown; keep stdout."""
    if "SourceIO.logger" not in sys.modules:
        return
    source_format = '[%(levelname)s]--[%(name)s:%(function)s]: %(message)s'
    # A SourceIO reload discards its singleton registry but leaves old logging
    # handlers in Python's weak registry. Match its exact formatter and Text
    # stream, including those orphaned handlers, without creating a new logger.
    for reference in list(logging._handlerList):
        handler = reference()
        if handler is not None and isinstance(getattr(handler, "stream", None), text_type) and getattr(getattr(handler, "formatter", None), "_fmt", None) == source_format:
            handler.stream = sys.stdout


atexit.register(detach_source_logging_handlers)


def issue(report, severity, code, message):
    report["issues"].append({"severity": severity, "code": code, "message": message})


def operator(identifier):
    namespace, name = identifier.split(".")
    op = getattr(getattr(bpy.ops, namespace), name)
    try:
        op.get_rna_type()
    except Exception:
        return None
    return op


def call(identifier, **kwargs):
    op = operator(identifier)
    if op is None:
        raise CapabilityError("Blender importer operator is unavailable: " + identifier)
    available = {p.identifier for p in op.get_rna_type().properties}
    result = op(**{key: value for key, value in kwargs.items() if key in available})
    if "FINISHED" not in result:
        raise CapabilityError(identifier + " cancelled import/export: " + str(result))


def enable_addons(paths, report, suffix=None):
    """Import only explicitly configured packages; never alter installed preferences."""
    enabled = []
    required = {".mdl": "SourceIO", ".vmdl_c": "SourceIO", ".smd": "io_scene_valvesource", ".dmx": "io_scene_valvesource",
                ".pmx": "mmd_tools", ".pmd": "mmd_tools", ".vrm": "io_scene_vrm", ".xps": "XNALaraMesh", ".mesh": "XNALaraMesh", ".ascii": "XNALaraMesh"}
    for supplied in paths:
        base = Path(supplied).resolve()
        if not base.is_dir():
            issue(report, "warning", "addon_path_missing", str(base))
            continue
        packages = [base] if (base / "__init__.py").is_file() else [
            p for p in base.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
        ]
        for package in packages:
            canonical = "SourceIO" if package.name.startswith("SourceIO-") else package.name
            if canonical not in {"SourceIO", "XNALaraMesh", "io_scene_xps", "io_xnalara", "mmd_tools", "io_scene_vrm", "io_scene_valvesource"}:
                continue
            if suffix is not None and canonical != required.get(suffix) and not (required.get(suffix) == "XNALaraMesh" and canonical in {"io_scene_xps", "io_xnalara"}):
                continue
            sys.path.insert(0, str(package.parent))
            try:
                if canonical == "mmd_tools":
                    mmd_dependencies(package)
                if canonical != package.name:
                    spec = importlib.util.spec_from_file_location(canonical, package / "__init__.py", submodule_search_locations=[str(package)])
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[canonical] = module
                    spec.loader.exec_module(module)
                else:
                    module = importlib.import_module(package.name)
                # Preference records are required by VRM's load handlers. The worker's
                # disposable profile isolates these records from the user's editor.
                module = addon_utils.enable(canonical, default_set=True, persistent=False)
                if module is None:
                    raise CapabilityError("addon registration failed; inspect Blender log")
                enabled.append(canonical)
            except Exception as exc:
                issue(report, "warning", "addon_load_failed", package.name + ": " + str(exc))
    report["enabled_addons"] = enabled


def import_vta_sidecar(source, options, report):
    explicit = options.get("vta_filepath")
    if explicit:
        sidecar = Path(explicit).expanduser()
        sidecar = sidecar if sidecar.is_absolute() else source.parent / sidecar
        sidecar = sidecar.resolve()
        if not sidecar.is_file() or sidecar.suffix.lower() != ".vta":
            raise CapabilityError("vta_filepath must select an existing .vta morph sidecar.")
    else:
        matches = [path for path in source.parent.iterdir() if path.is_file() and path.suffix.lower() == ".vta" and path.stem.casefold() == source.stem.casefold()]
        if len(matches) != 1:
            others = [path.name for path in source.parent.iterdir() if path.is_file() and path.suffix.lower() == ".vta"]
            if others:
                issue(report, "warning", "vta_selection_needed", "VTA morph sidecars were found, but none uniquely matches this SMD. Set options.vta_filepath: " + ", ".join(others[:20]))
            return
        sidecar = matches[0]
    meshes = [mesh for mesh in bpy.context.scene.objects if mesh.type == "MESH" and linked_rig(mesh) and mesh.data.polygons]
    wanted = options.get("vta_mesh")
    target = next((mesh for mesh in meshes if mesh.name == wanted), None) if wanted else None
    if not wanted:
        named = [mesh for mesh in meshes if mesh.name.casefold() == source.stem.casefold()]
        target = named[0] if len(named) == 1 else meshes[0] if len(meshes) == 1 else None
    manifest = report["vta_sidecar"] = {"path": str(sidecar), "status": "needs_review", "available_meshes": [mesh.name for mesh in meshes]}
    if target is None:
        issue(report, "warning", "vta_target_needed", "VTA sidecar requires one reference mesh. Set options.vta_mesh to its imported name.")
        return
    expected = []
    section = None
    with sidecar.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            stripped = line.strip()
            if stripped in {"skeleton", "vertexanimation"}:
                section = stripped
            elif stripped == "end":
                section = None
            elif section == "skeleton":
                match = re.match(r"time\s+([1-9]\d*)(?:\s*#\s*(.+))?$", stripped)
                if match:
                    expected.append((match.group(2) or match.group(1)).strip())
    manifest["mesh"], manifest["expected_shape_keys"] = target.name, expected
    before = set(bpy.context.scene.objects)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        target.hide_set(False)
        target.hide_viewport = False
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        call("import_scene.smd", filepath=str(sidecar), append="VALIDATE", doAnim=False)
        keys = target.data.shape_keys
        actual = {key.name: max(((point.co - base.co).length for point, base in zip(key.data, keys.reference_key.data)), default=0)
                  for key in keys.key_blocks if key != keys.reference_key} if keys else {}
        missing = sorted(set(expected) - set(actual))
        unmatched = [obj.name for obj in set(bpy.context.scene.objects) - before if obj.type == "MESH" and not obj.data.polygons]
        manifest.update(imported_shape_keys=list(actual), missing_shape_keys=missing, max_shape_deformation=actual, unmatched_helpers=unmatched)
        if not actual or missing or unmatched or any(amount <= 1e-8 for amount in actual.values()):
            issue(report, "warning", "vta_import_review", "VTA morph import is incomplete or has zero-deformation frames. Inspect sidecar/reference correspondence before using the facial rig.")
        else:
            manifest["status"] = "imported"
            issue(report, "info", "vta_imported", f"Imported {len(actual)} VTA morphs from {sidecar.name}; FBX preservation is checked below.")
        target.show_only_shape_key = False
        target.active_shape_key_index = 0
    except Exception as exc:
        issue(report, "warning", "vta_import_review", "VTA sidecar could not be verified: " + str(exc))


def import_source(source, report, options=None):
    suffix = source.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    else:
        native = {".fbx": "import_scene.fbx", ".glb": "import_scene.gltf", ".gltf": "import_scene.gltf", ".obj": "wm.obj_import",
                  ".stl": "wm.stl_import", ".ply": "wm.ply_import", ".dae": "wm.collada_import"}
        addons = {".mdl": ("sourceio.mdl", "SourceIO"), ".vmdl_c": ("sourceio.vmdl", "SourceIO"),
                  ".xps": ("xps_tools.import_model", "XNALaraMesh"), ".mesh": ("xps_tools.import_model", "XNALaraMesh"),
                  ".ascii": ("xps_tools.import_model", "XNALaraMesh"), ".pmx": ("mmd_tools.import_model", "mmd_tools"),
                  ".pmd": ("mmd_tools.import_model", "mmd_tools"), ".vrm": ("import_scene.vrm", "io_scene_vrm"),
                  ".smd": ("import_scene.smd", "io_scene_valvesource"), ".dmx": ("import_scene.smd", "io_scene_valvesource")}
        if suffix in native:
            if suffix == ".dae" and operator(native[suffix]) is None:
                raise CapabilityError("This Blender build has no Collada importer (.dae). Convert the source to FBX/glTF, or choose Blender 4.5 LTS with its Collada importer.")
            call(native[suffix], filepath=str(source))
        elif suffix in addons:
            identifier, required = addons[suffix]
            if operator(identifier) is None:
                raise CapabilityError(f"{suffix} needs the compatible {required} Blender addon. Run the dependency installer or configure addon_paths.")
            kwargs = {"filepath": str(source)}
            if suffix in {".mdl", ".vmdl_c"}:
                kwargs.update(directory=str(source.parent), files=[{"name": source.name}],
                              discover_resources=True, import_materials=True, use_bvlg=False)
            if suffix in {".pmx", ".pmd"}:
                kwargs["save_log"] = False
            if suffix in {".xps", ".mesh", ".ascii"}:
                kwargs.update(connectBones=False, autoIk=False, joinMeshParts=False, joinMeshRips=False)
            if suffix in {".smd", ".dmx"}:
                kwargs["doAnim"] = False
            call(identifier, **kwargs)
            if suffix == ".smd":
                import_vta_sidecar(source, options or {}, report)
        else:
            raise CapabilityError("Unsupported model extension: " + suffix)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def linked_rig(mesh):
    rigs = {m.object for m in mesh.modifiers if m.type == "ARMATURE" and m.object}
    if mesh.parent and mesh.parent.type == "ARMATURE":
        rigs.add(mesh.parent)
    return rigs


def choose_objects(options, report):
    all_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    rigs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    render_visible = set()
    def visit_collection(collection, allowed=True):
        allowed = allowed and not collection.hide_render
        if allowed:
            render_visible.update(collection.objects)
        for child in collection.children:
            visit_collection(child, allowed)
    visit_collection(bpy.context.scene.collection)
    visibility = {o: not o.hide_render and o in render_visible and o.visible_get() for o in all_meshes}
    requested = options.get("armature")
    counts = {rig: sum(len(mesh.data.vertices) for mesh in all_meshes if rig in linked_rig(mesh) and visibility[mesh]) for rig in rigs}
    candidates = sorted(rigs, key=lambda r: (counts[r], len(r.data.bones)), reverse=True)
    if requested:
        rig = next((r for r in rigs if r.name == requested), None)
        if rig is None:
            raise CapabilityError("Requested armature was not found: " + str(requested))
    else:
        rig = candidates[0] if candidates else None
        if len(candidates) > 1 and counts[candidates[1]] > 0:
            issue(report, "warning", "multiple_character_rigs", "Multiple mesh-linked armatures exist; selected " + rig.name + ". Choose armature explicitly to override.")
    requested_meshes = options.get("selected_meshes", options.get("meshes"))
    if requested_meshes:
        meshes = [o for o in all_meshes if o.name in requested_meshes]
        missing = sorted(set(requested_meshes) - {o.name for o in meshes})
        if missing:
            raise CapabilityError("Requested meshes were not found: " + ", ".join(missing))
        if rig and any(linked_rig(mesh) - {rig} for mesh in meshes):
            raise CapabilityError("Selected meshes bind different armatures; convert each character separately.")
    else:
        meshes = [o for o in all_meshes if (not rig or rig in linked_rig(o)) and
                  (options.get("include_hidden", False) or visibility[o])]
    if not meshes:
        raise CapabilityError("No character meshes selected. Pick meshes or enable include_hidden.")
    report["selection"] = {"armature": rig.name if rig else None,
                           "meshes": [m.name for m in meshes],
                           "available_armatures": [{"name": r.name, "bound_vertices": counts[r]} for r in candidates],
                           "available_meshes": [{"name": o.name, "collections": [c.name for c in o.users_collection],
                                                  "source_visible": visibility[o], "selected": o in meshes,
                                                  "vertices": len(o.data.vertices), "has_shape_keys": bool(o.data.shape_keys)}
                                                 for o in all_meshes if not rig or rig in linked_rig(o)],
                           "excluded_meshes": [o.name for o in all_meshes if o not in meshes]}
    if not rig:
        issue(report, "warning", "no_armature", "Static mesh has no skeleton. Humanoid rigging and skin weights require a rigged source or manual rigging.")
    # Explicitly selected hidden meshes must be present in the view layer for FBX operators.
    chosen = set(meshes + ([rig] if rig else []))
    def reveal(layer):
        needed = any(obj in chosen for obj in layer.collection.all_objects)
        if needed:
            layer.exclude = False
            layer.hide_viewport = False
            layer.collection.hide_viewport = False
            layer.collection.hide_render = False
            for child in layer.children:
                reveal(child)
    reveal(bpy.context.view_layer.layer_collection)
    for obj in chosen:
        obj.hide_viewport = obj.hide_render = False
        obj.hide_set(False)
    return rig, meshes


def shape_manifest(meshes):
    return {m.name: [k.name for k in m.data.shape_keys.key_blocks if k != m.data.shape_keys.reference_key]
            for m in meshes if m.data.shape_keys}


def freeze_shape_defaults(meshes, report):
    """Snapshot authored fitting state before rest conversion changes its drivers."""
    defaults = []
    for mesh in meshes:
        keys = mesh.data.shape_keys
        if not keys:
            continue
        blocks = [key for key in keys.key_blocks if key != keys.reference_key]
        values = [float(key.value) for key in blocks]
        paths = {key.path_from_id("value") for key in blocks}
        removed = []
        if keys.animation_data:
            for curve in list(keys.animation_data.drivers):
                if curve.data_path in paths:
                    removed.append(curve.data_path)
                    keys.driver_remove(curve.data_path)
        for key, value in zip(blocks, values):
            key.value = value
        defaults.append({"object": mesh.name, "names": [key.name for key in blocks], "values": values,
                         "source_value_drivers_frozen": len(removed)})
    report["shape_keys"] = defaults
    if any(item["source_value_drivers_frozen"] for item in defaults):
        issue(report, "info", "shape_defaults_frozen", "Authored shape-key fitting/hiding values were frozen before rest conversion; source Blender value drivers do not run in Unity.")


def freeze_source_inputs(meshes, report):
    """Retain evaluated appearance inputs whose Blender controls cannot export."""
    import ast
    selected = set(meshes)
    blocks = [(obj, obj not in selected) for obj in bpy.context.scene.objects if obj.type == "MESH"]
    trees = set()
    def visit(tree):
        if not tree or tree in trees:
            return
        trees.add(tree)
        for node in tree.nodes:
            if node.type == "GROUP":
                visit(node.node_tree)
    for material in {mat for mesh in meshes for mat in mesh.data.materials if mat}:
        visit(material.node_tree)
    blocks.extend((tree, False) for tree in trees)
    receipt = []
    for block, visibility_only in blocks:
        animation = block.animation_data
        if not animation:
            continue
        paths = {curve.data_path for curve in animation.drivers if not visibility_only or curve.data_path in {"hide_render", "hide_viewport"}}
        for path in sorted(paths):
            try:
                value = block.path_resolve(path)
                if isinstance(value, bpy.types.bpy_prop_array):
                    value = tuple(value)
                if not isinstance(value, (int, float, bool, tuple)) or isinstance(value, tuple) and not all(isinstance(v, (int, float, bool)) for v in value):
                    raise ValueError("driver value is not a portable numeric input")
                if path.startswith('["') and path.endswith('"]') and "." not in path:
                    key = ast.literal_eval(path[1:-1])
                    block[key] = value
                    block.driver_remove(path)
                    block[key] = value
                else:
                    owner_path, _, attribute = path.rpartition(".")
                    owner = block.path_resolve(owner_path) if owner_path else block
                    setattr(owner, attribute, value)
                    block.driver_remove(path)
                    setattr(owner, attribute, value)
                receipt.append({"datablock": block.name, "path": path, "value": value})
            except Exception as exc:
                issue(report, "warning", "source_input_driver_unresolved", block.name + ": could not freeze " + path + ": " + str(exc))
    report["source_inputs_frozen"] = receipt
    if receipt:
        issue(report, "info", "source_inputs_frozen", f"Froze {len(receipt)} authored visibility, modifier and material inputs before removing source controls. Outfit selection and shading retain their source values.")


def materialize_visibility_masks(meshes, options, report):
    """Subset an authored deletion mask across Basis, every key and skin data."""
    if not options.get("apply_visibility_masks", True):
        return
    import bmesh
    def deletion_tree(tree, seen=None):
        seen = seen or set()
        if not tree or tree.as_pointer() in seen:
            return False
        seen.add(tree.as_pointer())
        deletion = False
        for node in tree.nodes:
            if node.type == "GROUP":
                if not deletion_tree(node.node_tree, seen):
                    return False
                deletion = True
            elif node.bl_idname == "GeometryNodeDeleteGeometry":
                deletion = True
            elif node.bl_idname.startswith("GeometryNode") and node.bl_idname not in {
                "GeometryNodeInputNamedAttribute", "GeometryNodeInputIndex", "GeometryNodeInputPosition"}:
                return False
        return deletion
    for mesh in meshes:
        masks = [mod for mod in mesh.modifiers if mod.show_render and
                 (mod.type == "MASK" or mod.type == "NODES" and deletion_tree(mod.node_group))]
        if not masks:
            continue
        if mesh.data.users > 1:
            mesh.data = mesh.data.copy()
        original = mesh.data.copy()
        states = [(mod, mod.show_viewport, mod.show_render) for mod in mesh.modifiers]
        identifier = "AF_SourceVertex"
        while mesh.data.attributes.get(identifier):
            identifier += "_"
        marker = mesh.data.attributes.new(name=identifier, type="INT", domain="POINT")
        marker.data.foreach_set("value", range(len(mesh.data.vertices)))
        evaluated = None
        try:
            for mod, _, _ in states:
                mod.show_viewport = mod.show_render = mod in masks
            bpy.context.view_layer.update()
            evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
            result = evaluated.to_mesh()
            indices = result.attributes.get(identifier)
            if not indices or indices.domain != "POINT":
                raise ValueError("source vertex identity was not retained")
            vertex_ids = [point.value for point in indices.data]
            if len(set(vertex_ids)) != len(vertex_ids) or any(i < 0 or i >= len(original.vertices) for i in vertex_ids):
                raise ValueError("mask creates or duplicates vertices")
            face_keys = {tuple(sorted(polygon.vertices)) for polygon in original.polygons}
            retained = {tuple(sorted(vertex_ids[index] for index in polygon.vertices)) for polygon in result.polygons}
            if not retained <= face_keys:
                raise ValueError("mask changes face connectivity")
            removed = [polygon.index for polygon in original.polygons if tuple(sorted(polygon.vertices)) not in retained]
            removed_vertices = set(range(len(original.vertices))) - set(vertex_ids)
            if removed or removed_vertices:
                bpy.ops.object.select_all(action="DESELECT")
                mesh.hide_set(False)
                mesh.hide_viewport = False
                mesh.select_set(True)
                bpy.context.view_layer.objects.active = mesh
                mesh.active_shape_key_index = 0
                bpy.ops.object.mode_set(mode="EDIT")
                try:
                    editable = bmesh.from_edit_mesh(mesh.data)
                    editable.faces.ensure_lookup_table()
                    bmesh.ops.delete(editable, geom=[editable.faces[index] for index in removed], context="FACES_ONLY")
                    editable.verts.ensure_lookup_table()
                    bmesh.ops.delete(editable, geom=[vertex for vertex in editable.verts if vertex.index in removed_vertices], context="VERTS")
                    bmesh.update_edit_mesh(mesh.data, loop_triangles=True, destructive=True)
                finally:
                    bpy.ops.object.mode_set(mode="OBJECT")
                retained_ids = [point.value for point in mesh.data.attributes[identifier].data]
                if set(retained_ids) != set(vertex_ids):
                    raise ValueError("mask did not retain the evaluated vertex subset")
                if original.shape_keys:
                    if len(original.shape_keys.key_blocks) != len(mesh.data.shape_keys.key_blocks):
                        raise ValueError("mask removed a source shape channel")
                    for before, after in zip(original.shape_keys.key_blocks, mesh.data.shape_keys.key_blocks):
                        if before.name != after.name or any((before.data[old_index].co - after.data[new_index].co).length > 1e-7 for new_index, old_index in enumerate(retained_ids)):
                            raise ValueError("mask changed a source shape frame")
                original.name = "AF_Unmasked_Source_" + mesh.name
                original.use_fake_user = True
                report.setdefault("visibility_masks", []).append({"mesh": mesh.name, "modifiers": [mod.name for mod in masks],
                    "faces_before": len(original.polygons), "faces_after": len(mesh.data.polygons),
                    "vertices_before": len(original.vertices), "vertices_after": len(mesh.data.vertices),
                    "shape_frames_retained": len(mesh.data.shape_keys.key_blocks) if mesh.data.shape_keys else 0,
                    "unmasked_source_mesh_backup": original.name})
                issue(report, "info", "visibility_mask_applied", mesh.name + ": authored hidden geometry omitted using the same vertex subset on Basis and every shape frame. The unmasked source mesh is retained as a Blender datablock backup.")
            for mod in masks:
                mod.show_viewport = mod.show_render = False
        except Exception as exc:
            mesh.data = original
            issue(report, "warning", "visibility_mask_unresolved", mesh.name + ": source occlusion mask could not be carried safely: " + str(exc) + ". Inspect clipping before Unity export.")
        finally:
            if evaluated:
                evaluated.to_mesh_clear()
            if mesh.data.attributes.get(identifier):
                mesh.data.attributes.remove(mesh.data.attributes[identifier])
            for mod, viewport, render in states:
                if mod not in masks or mesh.data == original:
                    mod.show_viewport, mod.show_render = viewport, render
            if original.users == 0 and not original.use_fake_user:
                bpy.data.meshes.remove(original)


def weighted_bones(meshes, rig):
    names = set()
    if rig:
        for mesh in meshes:
            indices = {g.index: g.name for g in mesh.vertex_groups if g.name in rig.data.bones}
            for vertex in mesh.data.vertices:
                names.update(indices[g.group] for g in vertex.groups if g.group in indices and g.weight > 1e-8)
    return names


def physics_category(name):
    for category, pattern in PHYSICS.items():
        if pattern.search(name):
            return category
    return None


def humanoid_parents(present):
    torso = next((name for name in ("UpperChest", "Chest", "Spine", "Hips") if name in present), "Hips")
    parents = {"Spine": "Hips", "Chest": "Spine", "UpperChest": "Chest", "Neck": torso,
               "Head": "Neck" if "Neck" in present else torso, "Jaw": "Head", "LeftEye": "Head", "RightEye": "Head"}
    for side in ("Left", "Right"):
        parents.update({side + "UpperLeg": "Hips", side + "LowerLeg": side + "UpperLeg", side + "Foot": side + "LowerLeg",
                        side + "Toes": side + "Foot", side + "Shoulder": torso,
                        side + "UpperArm": side + "Shoulder" if side + "Shoulder" in present else torso,
                        side + "LowerArm": side + "UpperArm", side + "Hand": side + "LowerArm"})
        for finger in ("Thumb", "Index", "Middle", "Ring", "Little"):
            parents.update({side + finger + "Proximal": side + "Hand", side + finger + "Intermediate": side + finger + "Proximal",
                            side + finger + "Distal": side + finger + "Intermediate"})
    return parents


def repair_generated_hierarchy(rig, weighted, relationships, options, report):
    """Make an explicit generated deform tree portable without moving rest bones."""
    names = [bone.name for bone in rig.data.bones]
    preferred = weighted | {bone.name for bone in rig.data.bones if bone.use_deform}
    mapped, missing, _ = map_humanoid(names, options.get("humanoid_overrides", options.get("humanoid")), preferred)
    by_human = {item["humanName"]: item["boneName"] for item in mapped}
    core = [by_human.get(name, "") for name in ("Hips", "LeftUpperLeg", "RightUpperLeg", "LeftUpperArm", "RightUpperArm")]
    if missing or not (all(name.startswith("DEF-") for name in core) or all(name.startswith("GAME_") and name in weighted for name in core)):
        return
    controls, _, _ = map_humanoid(names)
    proxies = {item["boneName"]: by_human[item["humanName"]] for item in controls
               if item["humanName"] in by_human and item["boneName"] != by_human[item["humanName"]] and item["boneName"] not in preferred}
    parents = humanoid_parents(by_human)
    changes = []
    bpy.ops.object.select_all(action="DESELECT")
    rig.hide_set(False)
    rig.hide_viewport = False
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bones = rig.data.edit_bones
    snapshots = {bone.name: (bone.matrix.copy(), bone.length) for bone in bones}

    def attach(name, parent, reason):
        bone, target = bones[name], bones[parent]
        if target == bone or bone in target.parent_recursive or target in bone.parent_recursive:
            return
        old = bone.parent.name if bone.parent else None
        bone.use_connect = False
        bone.parent = target
        matrix, length = snapshots[name]
        bone.matrix, bone.length = matrix, length
        changes.append({"bone": name, "old_parent": old, "parent": parent, "reason": reason})

    try:
        for human, parent in parents.items():
            if human in by_human and parent in by_human:
                attach(by_human[human], by_human[parent], "humanoid_deformer_chain")
        # Existing control descendants include facial/accessory deformers; make
        # those proxies follow their corresponding animated deformer too.
        for control, deformer in proxies.items():
            attach(control, deformer, "control_proxy_follow")
        blended = []
        grouped = {}
        for relation in relationships:
            if relation["relationship"] == "ARMATURE" and relation.get("weight", 0) > 0:
                grouped.setdefault(relation["deformer"], []).append(relation)
        for name, candidates in grouped.items():
            if name not in bones or bones[name].parent is not None or not name.startswith("P-"):
                continue
            targets = [(item["weight"], proxies.get(item["controller"], item["controller"])) for item in candidates]
            targets = [(weight, target) for weight, target in targets if target in bones and target != name and bones[name] not in bones[target].parent_recursive]
            if not targets:
                continue
            targets.sort(key=lambda item: (item[0], len(bones[item[1]].parent_recursive)), reverse=True)
            attach(name, targets[0][1], "generated_parent_constraint")
            if len({target for _, target in targets}) > 1:
                blended.append(name)
        # Reparenting must preserve the actual bind/rest world positions and rolls.
        delta = max((abs(bone.matrix[row][column] - snapshots[bone.name][0][row][column])
                     for bone in bones for row in range(4) for column in range(4)), default=0)
        if delta > 1e-5:
            raise CapabilityError("Generated hierarchy repair changed bind/rest matrices.")
        report["generated_hierarchy_repair"] = {"changes": changes, "rest_matrix_max_delta": delta, "blended_parent_approximations": blended}
        if changes:
            issue(report, "warning", "generated_hierarchy_repaired", f"Reattached {len(changes)} generated rest-pose branches without moving bind bones. Multi-parent Blender simulation is approximated by one parent; inspect articulation and secondary motion in Unity.")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def prepare_rig(rig, meshes, options, report):
    if not rig:
        return [], [], set()
    source_names = [b.name for b in rig.data.bones]
    weighted = weighted_bones(meshes, rig)
    relationships = []
    for bone in rig.data.bones:
        if bone.name.startswith("DEF-") and bone.name[4:] in rig.data.bones:
            relationships.append({"deformer": bone.name, "controller": bone.name[4:], "relationship": "generated_name_pair"})
    for bone in rig.pose.bones:
        for constraint in bone.constraints:
            target = getattr(constraint, "target", None)
            subtarget = getattr(constraint, "subtarget", "")
            if target == rig and subtarget:
                relationships.append({"deformer": bone.name, "controller": subtarget, "relationship": constraint.type})
            for target in getattr(constraint, "targets", []):
                if target.target == rig and target.subtarget:
                    relationships.append({"deformer": bone.name, "controller": target.subtarget, "relationship": constraint.type, "weight": target.weight})
    report["generated_deformer_relationships"] = relationships
    if relationships and options.get("repair_generated_hierarchy", True):
        repair_generated_hierarchy(rig, weighted, relationships, options, report)
    keep = {b.name for b in rig.data.bones if b.name in weighted or not CONTROLLER.match(b.name) or physics_category(b.name)}
    for name in list(keep):
        parent = rig.data.bones[name].parent
        while parent:
            keep.add(parent.name)
            parent = parent.parent
    excluded = set(source_names) - keep if relationships and options.get("strip_controllers", True) else set()
    # Only recognized, unweighted, non-ancestor controls may be excluded. All other bones survive.
    rig.hide_set(False)
    rig.hide_viewport = False
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    rig.data.pose_position = "REST"
    if excluded:
        # This is a rest-pose export copy. Remove nonportable links before their control targets.
        constraints = sum(len(b.constraints) for b in rig.pose.bones)
        for bone in rig.pose.bones:
            for constraint in list(bone.constraints):
                bone.constraints.remove(constraint)
        rig.animation_data_clear()
        report["nonportable_rig_constraints_removed"] = constraints
        bpy.ops.object.mode_set(mode="EDIT")
        for name in sorted(excluded):
            rig.data.edit_bones.remove(rig.data.edit_bones[name])
        bpy.ops.object.mode_set(mode="OBJECT")
        issue(report, "info", "controllers_pruned", f"Excluded {len(excluded)} recognized unweighted leaf controls; weighted bones, unknown secondary bones and required parents are retained.")
    if relationships:
        issue(report, "warning", "control_rig_rest_export", "Generated/control rig exported in bind/rest pose. Blender constraints and scripted controls do not run in Unity; inspect Humanoid mapping and secondary deformation.")
    report["weighted_bones"] = sorted(weighted)
    report["intentionally_excluded_controller_bones"] = sorted(excluded)
    return source_names, sorted(excluded), weighted


def bones_report(rig, options, report):
    names = [b.name for b in rig.data.bones] if rig else []
    preferred = set(report.get("weighted_bones", [])) | {bone.name for bone in rig.data.bones if bone.use_deform} if rig else set()
    humanoid, missing, ambiguous = map_humanoid(names, options.get("humanoid_overrides", options.get("humanoid")), preferred)
    report["humanoid"], report["missing_required_humanoid"] = humanoid, missing
    report["humanoid_ambiguities"] = ambiguous
    if missing:
        issue(report, "warning", "humanoid_incomplete", "Missing required humanoid mapping: " + ", ".join(missing))
    if ambiguous:
        issue(report, "warning", "humanoid_ambiguous", "Some names match multiple bones; supply options.humanoid overrides.")
    physics = []
    if rig:
        by_human = {item["humanName"]: rig.data.bones[item["boneName"]] for item in humanoid}
        # A generated DEF bone may be a sibling rather than the semantic parent. Never certify that mapping blindly.
        parents = humanoid_parents(by_human)
        for human, parent in parents.items():
            if human in by_human and parent in by_human and by_human[parent] not in by_human[human].parent_recursive:
                issue(report, "warning", "humanoid_hierarchy", human + " is not below " + parent + "; verify the armature's Humanoid mapping in Unity.")
        scale = sum(abs(v) for v in rig.matrix_world.to_scale()) / 3 * bpy.context.scene.unit_settings.scale_length
        requested_roots = options.get("physics_roots")
        if requested_roots is not None and (not isinstance(requested_roots, list) or any(name not in rig.data.bones for name in requested_roots)):
            raise ValueError("physics_roots must list existing armature bone names")
        parent_bridges = {entry["bone"] for entry in report.get("generated_hierarchy_repair", {}).get("changes", [])
                          if entry["reason"] == "generated_parent_constraint" and entry["bone"] not in report.get("weighted_bones", [])}
        for bone in rig.data.bones:
            if requested_roots is not None and bone.name not in requested_roots:
                continue
            category = physics_category(bone.name)
            if requested_roots is not None:
                category = category or "secondary"
            if not category:
                continue
            if requested_roots is None and bone.name in parent_bridges:
                continue
            if requested_roots is None and any(physics_category(p.name) == category and p.name not in parent_bridges for p in bone.parent_recursive):
                continue
            path = "/".join([rig.name] + [p.name for p in reversed(bone.parent_recursive)] + [bone.name])
            approved = requested_roots is not None
            physics.append({"bone": bone.name, "path": path, "category": category, "confidence": 1.0 if approved else 0.75,
                            "endpoint_length": round(bone.length * scale, 6), "requires_review": not approved, "approved": approved})
    report["physics"] = physics
    if physics:
        issue(report, "info", "physics_candidates", "Secondary bones are retained. Candidate PhysBone roots require preview/tuning; names cannot reconstruct source simulation parameters.")


def triangles(meshes):
    return sum(sum(max(0, len(p.vertices) - 2) for p in mesh.data.polygons) for mesh in meshes)


def optimize(meshes, rig, preset, options, report):
    target = None if preset == "preserve" else options.get("target_triangles", PRESETS[preset]["triangles"])
    if preset == "preserve" and "target_triangles" in options:
        issue(report, "warning", "preserve_target_ignored", "Preserve keeps the original geometry; choose PC balanced or Mobile candidate for a triangle target.")
    current = triangles(meshes)
    report["optimization"] = {"target_triangles": target, "triangles_before": current, "decimated_meshes": [], "skin_weight_meshes_protected": [], "shape_key_meshes_protected": []}
    if target and current > target:
        safe = [m for m in meshes if not m.data.shape_keys and all(mod.type in {"ARMATURE", "SUBSURF"} for mod in m.modifiers)]
        protected = current - triangles(safe)
        report["optimization"]["shape_key_meshes_protected"] = [m.name for m in meshes if m.data.shape_keys]
        ratio = max(0.05, min(1.0, (target - protected) / max(1, triangles(safe))))
        if options.get("decimate", True) and ratio < 1:
            for mesh in safe:
                if len(mesh.data.polygons) < 20:
                    continue
                bpy.ops.object.select_all(action="DESELECT")
                mesh.hide_set(False)
                mesh.hide_viewport = False
                mesh.select_set(True)
                bpy.context.view_layer.objects.active = mesh
                modifier = mesh.modifiers.new("AvatarForge_Budget", "DECIMATE")
                modifier.ratio = ratio
                modifier.use_collapse_triangulate = True
                while mesh.modifiers[0] != modifier:
                    bpy.ops.object.modifier_move_up(modifier=modifier.name)
                expected_weights = weighted_bones([mesh], rig)
                original = mesh.data.copy()
                try:
                    bpy.ops.object.modifier_apply(modifier=modifier.name)
                    missing = expected_weights - weighted_bones([mesh], rig)
                    if missing:
                        rejected = mesh.data
                        mesh.data = original
                        if rejected.users == 0:
                            bpy.data.meshes.remove(rejected)
                        report["optimization"]["skin_weight_meshes_protected"].append(mesh.name)
                        issue(report, "warning", "decimation_skin_preserved", mesh.name + ": kept its original mesh because reduction removed all influence from " + ", ".join(sorted(missing)) + ". Unity optimization can review this mesh.")
                    else:
                        report["optimization"]["decimated_meshes"].append(mesh.name)
                except Exception:
                    mesh.data = original
                    raise
                finally:
                    if original.users == 0:
                        bpy.data.meshes.remove(original)
        if triangles(meshes) > target:
            issue(report, "warning", "triangle_budget", f"{triangles(meshes):,} triangles exceed target {target:,}. Shape-key meshes are protected; no unverified shape-key reprojection was attempted.")
    report["optimization"]["triangles_after"] = triangles(meshes)
    nonportable = []
    for mesh in meshes:
        modifiers = [m.type for m in mesh.modifiers if m.type != "ARMATURE" and (m.show_viewport or m.show_render)]
        if modifiers:
            nonportable.append({"mesh": mesh.name, "modifiers": modifiers})
        if not mesh.data.uv_layers:
            issue(report, "warning", "missing_uv", mesh.name + " has no UV map; texture baking/assignment requires UV unwrapping.")
        if rig:
            valid = {g.index for g in mesh.vertex_groups if g.name in rig.data.bones}
            unweighted = sum(not any(g.group in valid and g.weight > 1e-8 for g in v.groups) for v in mesh.data.vertices)
            if unweighted:
                issue(report, "warning", "unweighted_vertices", f"{mesh.name}: {unweighted} vertices have no weight on selected armature bones.")
    report["nonportable_modifiers"] = nonportable
    if nonportable:
        issue(report, "warning", "nonportable_modifiers", "Cloth, surface deform, procedural masks or corrective modifiers do not transfer through FBX. Source .blend retains them; geometry/shape-key-preserving conversion requires review.")
    if options.get("height"):
        height = float(options["height"])
        if not 0.1 <= height <= 10:
            raise ValueError("height must be between 0.1 and 10 metres")
        coords = [mesh.matrix_world @ Vector(corner) for mesh in meshes for corner in mesh.bound_box]
        actual = max(c.z for c in coords) - min(c.z for c in coords)
        if actual <= 1e-8:
            raise ValueError("Cannot normalize a zero-height model")
        factor = height / actual / bpy.context.scene.unit_settings.scale_length
        chosen = set(meshes + ([rig] if rig else []))
        for obj in chosen:
            if obj.parent not in chosen:
                obj.scale *= factor
                obj.location *= factor
        report["optimization"]["height_metres"] = height


def material_images(material, visited=None):
    visited = visited or set()
    tree = getattr(material, "node_tree", None)
    if not tree or tree.as_pointer() in visited:
        return set()
    visited.add(tree.as_pointer())
    images = set()
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            images.add(node.image)
        if node.type == "GROUP" and node.node_tree:
            images.update(material_images(node, visited))
    return images


def upstream_images(socket, visited=None):
    visited = visited or set()
    found = set()
    for link in socket.links:
        node = link.from_node
        if node.as_pointer() in visited:
            continue
        visited.add(node.as_pointer())
        if node.type == "TEX_IMAGE" and node.image:
            found.add(node.image)
        else:
            for input_socket in node.inputs:
                found.update(upstream_images(input_socket, visited))
    return found


def image_nodes(tree, image, visited=None):
    visited = visited or set()
    if not tree or tree.as_pointer() in visited:
        return []
    visited.add(tree.as_pointer())
    nodes = []
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and node.image == image:
            nodes.append((tree, node))
        elif node.type == "GROUP":
            nodes.extend(image_nodes(node.node_tree, image, visited))
    return nodes


def flatten_udims(meshes, materials, source, output, limit, report):
    """Create real image atlases and separate remapped UVs; never change shape topology."""
    images = set().union(*(material_images(m) for m in materials)) if materials else set()
    atlases, by_uv = [], {}
    for image in sorted(images, key=lambda i: i.name):
        if image.source != "TILED":
            continue
        try:
            tile_numbers = [tile.number for tile in image.tiles]
            columns = max((number - 1001) % 10 for number in tile_numbers) + 1
            rows = max((number - 1001) // 10 for number in tile_numbers) + 1
            files = []
            for number in tile_numbers:
                path = Path(bpy.path.abspath(image.filepath).replace("<UDIM>", str(number)))
                if not path.is_file():
                    matches = list(source.parent.rglob(path.name))
                    if len(matches) != 1:
                        raise ValueError(f"tile {number} missing or ambiguous")
                    path = matches[0]
                files.append((number, path))
            first = bpy.data.images.load(str(files[0][1]), check_existing=False)
            tile_width, tile_height = first.size
            bpy.data.images.remove(first)
            ratio = min(1.0, limit / max(tile_width * columns, tile_height * rows)) if limit else 1.0
            tile_width, tile_height = max(1, int(tile_width * ratio)), max(1, int(tile_height * ratio))
            width, height = tile_width * columns, tile_height * rows
            # ponytail: bound atlas allocation; sparse giant grids need an external baker.
            if width * height > 67108864:
                raise ValueError("atlas exceeds 64 million pixels; choose a texture cap or bake a compact atlas")
            pixels = array("f", [0.0]) * (width * height * 4)
            for number, path in files:
                tile = bpy.data.images.load(str(path), check_existing=False)
                tile.colorspace_settings.name = image.colorspace_settings.name
                tile.scale(tile_width, tile_height)
                values = array("f", [0.0]) * (tile_width * tile_height * 4)
                tile.pixels.foreach_get(values)
                tile_x, tile_y = (number - 1001) % 10, (number - 1001) // 10
                stride = tile_width * 4
                for y in range(tile_height):
                    start = ((tile_y * tile_height + y) * width + tile_x * tile_width) * 4
                    pixels[start:start + stride] = values[y * stride:(y + 1) * stride]
                bpy.data.images.remove(tile)
            atlas = bpy.data.images.new(image.name.replace("<UDIM>", "Atlas"), width=width, height=height, alpha=True)
            atlas.colorspace_settings.name = image.colorspace_settings.name
            atlas.pixels.foreach_set(pixels)
            del pixels
            atlas["avatarforge_owned"] = True
            atlas["avatarforge_uv_scale"] = [1 / columns, 1 / rows]
            for material in materials:
                pairs = image_nodes(material.node_tree, image)
                if not pairs:
                    continue
                for tree, node in pairs:
                    uv_source_name = None
                    if node.inputs["Vector"].is_linked:
                        upstream = node.inputs["Vector"].links[0].from_node
                        if upstream.type != "UVMAP":
                            issue(report, "warning", "udim_vector_processing", material.name + ": transformed UDIM coordinates need shader baking/review.")
                            continue
                        uv_source_name = upstream.uv_map
                    uv_name = f"AF_Atlas_{columns}x{rows}_" + (uv_source_name or "Default")
                    for mesh in meshes:
                        if material not in mesh.data.materials[:]:
                            continue
                        original_uv = mesh.data.uv_layers.get(uv_source_name) if uv_source_name else mesh.data.uv_layers.active
                        if not original_uv:
                            raise ValueError(mesh.name + " has no UDIM UV map")
                        key = (mesh.name, uv_name)
                        if key not in by_uv:
                            uv = mesh.data.uv_layers.get(uv_name) or mesh.data.uv_layers.new(name=uv_name)
                            for old, new in zip(original_uv.data, uv.data):
                                new.uv = (old.uv.x / columns, old.uv.y / rows)
                            by_uv[key] = uv.name
                    # Per-node UV selectors keep ordinary texture layers intact in .blend.
                    selector = tree.nodes.new("ShaderNodeUVMap")
                    selector.uv_map = uv_name
                    tree.links.new(selector.outputs["UV"], node.inputs["Vector"])
                    node.image = atlas
            atlases.append({"source": image.name, "image": atlas.name, "tiles": tile_numbers, "size": [width, height],
                            "uv_maps": sorted({uv for (_, uv) in by_uv})})
        except Exception as exc:
            issue(report, "warning", "udim_atlas_failed", image.name + ": " + str(exc))
    report["udim_atlases"] = atlases
    if atlases:
        issue(report, "warning", "udim_atlas_uv", "UDIM textures were flattened into portable PNG atlases with additional UV maps. Complex/material-specific UV selections require Unity shader/bake review.")


def material_uv_has_area(mesh, material):
    # Blender's edit-active UV can be an unused authoring/bake layout. Shader
    # implicit coordinates use the render UV, while Unity reads exported UV0.
    uv = mesh.data.uv_layers[0] if mesh.data.uv_layers else None
    if not uv:
        return False
    for polygon in mesh.data.polygons:
        if mesh.data.materials[polygon.material_index] != material:
            continue
        coords = [uv.data[index].uv for index in polygon.loop_indices]
        if abs(sum(a.x * b.y - b.x * a.y for a, b in zip(coords, coords[1:] + coords[:1]))) > 1e-10:
            return True
    return False


def bake_materials(meshes, materials, size, report):
    """Native CPU baking of Principled inputs, with real tangent normals and packed maps."""
    scene = bpy.context.scene
    size = size or 2048
    if not 64 <= size <= 4096:
        raise ValueError("bake_size must be 64..4096 pixels")
    source_uv_name = "AF_SourceUV"
    while any(mesh.data.uv_layers.get(source_uv_name) for mesh in meshes):
        source_uv_name += "_"
    unavailable_source_uv = set()
    for mesh in meshes:
        layers = mesh.data.uv_layers
        source_uv = next((uv for uv in layers if uv.active_render), layers[0] if layers else None)
        if source_uv:
            copied = layers.new(name=source_uv_name)
            if not copied:
                unavailable_source_uv.add(mesh)
                continue
            for old, new in zip(source_uv.data, copied.data):
                new.uv = old.uv
    visited_trees = set()
    def pin_implicit_uvs(tree):
        if not tree or tree.as_pointer() in visited_trees:
            return
        visited_trees.add(tree.as_pointer())
        for node in list(tree.nodes):
            if node.type == "GROUP":
                pin_implicit_uvs(node.node_tree)
            if node.type == "TEX_IMAGE" and not node.inputs["Vector"].is_linked:
                uv_node = tree.nodes.new("ShaderNodeUVMap")
                uv_node.uv_map = source_uv_name
                tree.links.new(uv_node.outputs["UV"], node.inputs["Vector"])
            elif node.type == "TEX_COORD":
                links = list(node.outputs["UV"].links)
                if links:
                    uv_node = tree.nodes.new("ShaderNodeUVMap")
                    uv_node.uv_map = source_uv_name
                    for link in links:
                        tree.links.new(uv_node.outputs["UV"], link.to_socket)
    for material in materials:
        if not any(mesh in unavailable_source_uv for mesh in meshes if material in mesh.data.materials[:]):
            pin_implicit_uvs(material.node_tree)
    eligible, target_nodes, baked = {}, {}, {}
    for material in materials:
        tree = material.node_tree
        principals = [node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"] if tree else []
        outputs = [node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output] if tree else []
        if len(principals) != 1 or len(outputs) != 1:
            issue(report, "warning", "material_bake_skipped", material.name + ": bake needs one Principled shader and one active output.")
            continue
        bound = [mesh for mesh in meshes if material in mesh.data.materials[:]]
        if any(mesh in unavailable_source_uv for mesh in bound):
            issue(report, "warning", "material_bake_skipped", material.name + ": all eight UV channels are already occupied; source graph retained to preserve named UV dependencies.")
            continue
        if not bound or any(not mesh.data.uv_layers for mesh in bound):
            issue(report, "warning", "material_bake_skipped", material.name + ": no usable UV map.")
            continue
        if any(not material_uv_has_area(mesh, material) for mesh in bound):
            issue(report, "warning", "material_bake_skipped", material.name + ": UVs are degenerate or its material has no UV-covered faces; source shader retained.")
            continue
        target = tree.nodes.new("ShaderNodeTexImage")
        target.label = "AvatarForge bake target"
        target_nodes[material] = target
        eligible[material] = (principals[0], outputs[0])
        alpha = principals[0].inputs["Alpha"]
        material["avatarforge_baked_alpha_mode"] = "BLEND" if alpha.is_linked or alpha.default_value < .999 else "OPAQUE"
        baked[material] = {}
        if any(node.type in {"MIX_SHADER", "ADD_SHADER"} for node in tree.nodes):
            issue(report, "warning", "layered_shader_bake", material.name + ": Principled input bake omits extra shader layers; inspect transparency/refraction.")
    if not eligible:
        return
    bake_meshes = []
    for mesh in meshes:
        if not any(material in eligible for material in mesh.data.materials if material):
            continue
        if any(material and material not in eligible for material in mesh.data.materials):
            issue(report, "warning", "material_bake_slots", mesh.name + ": unsupported material slot; source material retained.")
            for material in mesh.data.materials:
                if material in baked:
                    baked[material]["incomplete"] = True
            continue
        bake_meshes.append(mesh)
    if not bake_meshes:
        return
    # Ordinary repeating textures can use positive tiles outside 0..1 even
    # without a UDIM image. Bake their full coordinates, rather than silently
    # exporting an empty map. Meshes sharing a material need one common layout.
    destinations_by_mesh = {}
    destination_scales = {}
    pending = set(bake_meshes)
    while pending:
        first = min(pending, key=lambda mesh: mesh.name)
        pending.remove(first)
        component = {first}
        component_materials = {material for mesh in component for material in mesh.data.materials if material}
        while True:
            linked = {mesh for mesh in pending if component_materials.intersection(mesh.data.materials[:])}
            if not linked:
                break
            component.update(linked)
            pending.difference_update(linked)
            component_materials.update(material for mesh in linked for material in mesh.data.materials if material)
        coordinates = [point.uv for mesh in component for point in mesh.data.uv_layers[0].data]
        if any(min(point) < -1e-5 for point in coordinates):
            for material in component_materials:
                eligible.pop(material, None)
                baked.pop(material, None)
            bake_meshes = [mesh for mesh in bake_meshes if mesh not in component]
            issue(report, "warning", "material_bake_skipped", "Negative bake destination UVs require an explicit atlas layout; source graphs retained for: " + ", ".join(sorted(mesh.name for mesh in component)))
            continue
        columns = max(1, math.ceil(max(point.x for point in coordinates) - 1e-6))
        rows = max(1, math.ceil(max(point.y for point in coordinates) - 1e-6))
        for mesh in component:
            for uv in mesh.data.uv_layers:
                atlas = re.match(r"AF_Atlas_(\d+)x(\d+)_", uv.name)
                if atlas:
                    columns, rows = max(columns, int(atlas.group(1))), max(rows, int(atlas.group(2)))
        if columns > 1 or rows > 1:
            existing = {mesh: next((uv for uv in mesh.data.uv_layers if re.match(rf"AF_Atlas_{columns}x{rows}_", uv.name) and
                                   all(abs(old.uv.x / columns - new.uv.x) < 1e-6 and abs(old.uv.y / rows - new.uv.y) < 1e-6
                                       for old, new in zip(mesh.data.uv_layers[0].data, uv.data))), None) for mesh in component}
            if any(not existing[mesh] and len(mesh.data.uv_layers) >= 8 for mesh in component):
                for material in component_materials:
                    eligible.pop(material, None)
                    baked.pop(material, None)
                bake_meshes = [mesh for mesh in bake_meshes if mesh not in component]
                issue(report, "warning", "material_bake_skipped", "Positive-tile baking needs a free UV channel or matching atlas; named source UVs were preserved for: " + ", ".join(sorted(mesh.name for mesh in component)))
                continue
            name = f"AF_Atlas_{columns}x{rows}_Bake"
            while any(mesh.data.uv_layers.get(name) for mesh in component):
                name += "_"
            for mesh in component:
                layer = existing[mesh]
                if layer:
                    destinations_by_mesh[mesh] = layer.name
                    continue
                layer = mesh.data.uv_layers.new(name=name)
                if not layer:
                    raise CapabilityError(mesh.name + ": positive-tile bake needs a free UV channel or a matching existing atlas; preserve named UVs and prepare an explicit atlas layout.")
                for old, new in zip(mesh.data.uv_layers[0].data, layer.data):
                    new.uv = (old.uv.x / columns, old.uv.y / rows)
                destinations_by_mesh[mesh] = name
            report.setdefault("bake_uv_atlases", []).append({"meshes": sorted(mesh.name for mesh in component), "columns": columns, "rows": rows, "uv_scale": [1 / columns, 1 / rows], "source_uv": "UV0"})
        for mesh in component:
            destination_scales[mesh] = [1 / columns, 1 / rows]
    if not bake_meshes:
        return
    engine, samples = scene.render.engine, scene.cycles.samples
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples = "CPU", 1
    visibility = [(modifier, modifier.show_render, modifier.show_viewport)
                  for mesh in meshes for modifier in mesh.modifiers if modifier.type != "ARMATURE"]
    uvs = [(mesh, mesh.data.uv_layers.active_index, [(uv, uv.active_render) for uv in mesh.data.uv_layers]) for mesh in meshes]
    for modifier, _, _ in visibility:
        modifier.show_render = modifier.show_viewport = False
    for mesh in meshes:
        mesh.data.uv_layers.active_index = 0
        mesh.data.uv_layers[0].active_render = True
        destination = destinations_by_mesh.get(mesh)
        if destination:
            uv = mesh.data.uv_layers[destination]
            mesh.data.uv_layers.active_index = list(mesh.data.uv_layers).index(uv)
            uv.active_render = True
    channels = (("base_color", "Base Color"), ("normal", None), ("roughness", "Roughness"),
                ("metallic", "Metallic"), ("alpha", "Alpha"), ("emission", None))
    in_progress = None
    try:
        for channel, input_name in channels:
            saved_links, emissions = {}, {}
            try:
                for material, (shader, output_node) in eligible.items():
                    tree = material.node_tree
                    emission_strength = constant_socket_value(shader.inputs["Emission Strength"])
                    emission_color = constant_socket_value(shader.inputs["Emission Color"])
                    constant = (channel == "normal" and not shader.inputs["Normal"].is_linked) or (
                        channel == "emission" and isinstance(emission_strength, float) and (
                            emission_strength == 0 or isinstance(emission_color, tuple))) or (
                        channel in {"metallic", "roughness"} and isinstance(constant_socket_value(shader.inputs["Metallic"]), float)
                        and isinstance(constant_socket_value(shader.inputs["Roughness"]), float))
                    channel_size = 1 if constant else size
                    image = bpy.data.images.new("AF_Baked_" + material.name + "_" + channel, width=channel_size, height=channel_size, alpha=True)
                    image.generated_color = (0, 0, 0, 0)
                    image.colorspace_settings.name = "sRGB" if channel in {"base_color", "emission"} else "Non-Color"
                    image["avatarforge_owned"], image["avatarforge_channel"] = True, channel
                    target_nodes[material].image = image
                    tree.nodes.active = target_nodes[material]
                    baked[material][channel] = image
                    if input_name:
                        saved_links[material] = [link.from_socket for link in output_node.inputs["Surface"].links]
                        emit = tree.nodes.new("ShaderNodeEmission")
                        socket = shader.inputs[input_name]
                        if socket.links:
                            tree.links.new(socket.links[0].from_socket, emit.inputs["Color"])
                        else:
                            value = socket.default_value
                            emit.inputs["Color"].default_value = tuple(value) if hasattr(value, "__len__") else (value, value, value, 1)
                        tree.links.new(emit.outputs["Emission"], output_node.inputs["Surface"])
                        emissions[material] = emit
                bpy.ops.object.select_all(action="DESELECT")
                for mesh in bake_meshes:
                    mesh.hide_set(False)
                    mesh.hide_viewport = mesh.hide_render = False
                    mesh.select_set(True)
                bpy.context.view_layer.objects.active = bake_meshes[0]
                result = bpy.ops.object.bake(type="NORMAL" if channel == "normal" else "EMIT", normal_space="TANGENT", use_clear=False, margin=8)
                if "FINISHED" not in result:
                    raise RuntimeError("Blender cancelled " + channel + " bake")
                print("AVATARFORGE_BAKE " + channel, flush=True)
            finally:
                for material, emit in emissions.items():
                    tree = material.node_tree
                    output_node = eligible[material][1]
                    tree.nodes.remove(emit)
                    for socket in saved_links[material]:
                        tree.links.new(socket, output_node.inputs["Surface"])
        for material, images in baked.items():
            if images.get("incomplete"):
                continue
            bound = [mesh for mesh in meshes if material in mesh.data.materials[:]]
            destinations = {mesh.data.uv_layers.active.name for mesh in bound}
            destination = next(iter(destinations)) if len(destinations) == 1 else source_uv_name
            if len(destinations) > 1:
                # All ordinary bake destinations are UV0. Alias their name for
                # the saved Blender material without changing FBX UV ordering.
                destination = "AF_BakedUV"
                while any(mesh.data.uv_layers.get(destination) for mesh in bound):
                    destination += "_"
                for mesh in bound:
                    source_destination = mesh.data.uv_layers.active
                    alias = mesh.data.uv_layers.new(name=destination)
                    for old, new in zip(source_destination.data, alias.data):
                        new.uv = old.uv
            uv_scale = destination_scales[bound[0]]
            for channel_image in images.values():
                if isinstance(channel_image, bpy.types.Image):
                    channel_image["avatarforge_uv_scale"] = uv_scale
            # Unity Standard reads smoothness from metallic-map alpha and alpha from albedo.
            color, alpha = images["base_color"], images["alpha"]
            values, opacity = array("f", [0]) * (size * size * 4), array("f", [0]) * (size * size * 4)
            color.pixels.foreach_get(values)
            alpha.pixels.foreach_get(opacity)
            values[3::4] = opacity[0::4]
            color.pixels.foreach_set(values)
            metallic, roughness = images["metallic"], images["roughness"]
            packed_size = int(metallic.size[0]) * int(metallic.size[1])
            values, opacity = array("f", [0]) * (packed_size * 4), array("f", [0]) * (packed_size * 4)
            metallic.pixels.foreach_get(values)
            roughness.pixels.foreach_get(opacity)
            values[3::4] = array("f", (1 - value for value in opacity[0::4]))
            metallic.pixels.foreach_set(values)
            metallic["avatarforge_channel"] = "metallic_smoothness"
            saved = material.copy()
            saved.name = "AF_Source_" + material.name
            saved.use_fake_user = True
            in_progress = (material, saved, material.name)
            tree = material.node_tree
            tree.nodes.clear()
            shader, output_node = tree.nodes.new("ShaderNodeBsdfPrincipled"), tree.nodes.new("ShaderNodeOutputMaterial")
            tree.links.new(shader.outputs["BSDF"], output_node.inputs["Surface"])
            uv_node = tree.nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = destination
            for channel, socket_name in (("base_color", "Base Color"), ("roughness", "Roughness"), ("metallic", "Metallic"), ("emission", "Emission Color")):
                texture = tree.nodes.new("ShaderNodeTexImage")
                texture.image = images[channel]
                tree.links.new(uv_node.outputs["UV"], texture.inputs["Vector"])
                tree.links.new(texture.outputs["Color"], shader.inputs[socket_name])
                if channel == "base_color":
                    tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
            texture = tree.nodes.new("ShaderNodeTexImage")
            texture.image = images["normal"]
            tree.links.new(uv_node.outputs["UV"], texture.inputs["Vector"])
            normal = tree.nodes.new("ShaderNodeNormalMap")
            normal.uv_map = destination
            tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
            tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
            shader.inputs["Emission Strength"].default_value = 1
            report.setdefault("baked_materials", []).append({"name": material.name, "size": size, "destination_uv": "UV0", "source_material_backup": saved.name,
                                                            "channels": ["base_color_alpha", "normal", "roughness", "metallic_smoothness", "emission"]})
            in_progress = None
        issue(report, "info", "materials_baked", "Native CPU baking generated albedo/alpha, tangent normals, roughness, metallic/smoothness and emission maps; source material graphs remain in model.blend backups.")
    except Exception as exc:
        if in_progress:
            damaged, saved, original_name = in_progress
            damaged.user_remap(saved)
            damaged.name = "AF_Failed_" + original_name
            saved.name = original_name
            materials[materials.index(damaged)] = saved
        issue(report, "warning", "material_bake_failed", "Baking stopped: " + str(exc) + ". Inspect partial baked results; incomplete material graphs use their source fallback.")
    finally:
        scene.render.engine, scene.cycles.samples = engine, samples
        for modifier, render, viewport in visibility:
            modifier.show_render, modifier.show_viewport = render, viewport
        for mesh, active, flags in uvs:
            mesh.data.uv_layers.active_index = active
            for uv, render in flags:
                uv.active_render = render


def constant_socket_value(socket, group_context=None, visited=None):
    """Resolve only literal sockets and transparent node/group connections."""
    visited = set() if visited is None else visited
    marker = (socket.as_pointer(), group_context[0].as_pointer() if group_context else 0)
    if marker in visited:
        return None
    visited = visited | {marker}
    if not socket.links:
        value = getattr(socket, "default_value", None)
        if isinstance(value, (int, float)):
            return float(value)
        if hasattr(value, "__len__") and all(isinstance(item, (int, float)) for item in value):
            return tuple(float(item) for item in value)
        return None
    output = socket.links[0].from_socket
    node = output.node
    if node.mute or not socket.links[0].is_valid:
        return None
    if node.type in {"VALUE", "RGB"}:
        value = output.default_value
        return tuple(value) if hasattr(value, "__len__") else float(value)
    if node.type == "REROUTE":
        return constant_socket_value(node.inputs[0], group_context, visited)
    if node.type == "GROUP_INPUT" and group_context:
        instance, outer_context = group_context
        index = list(node.outputs).index(output)
        if index < len(instance.inputs):
            return constant_socket_value(instance.inputs[index], outer_context, visited)
    if node.type == "GROUP" and node.node_tree:
        active = [item for item in node.node_tree.nodes if item.type == "GROUP_OUTPUT" and item.is_active_output]
        index = list(node.outputs).index(output)
        if len(active) == 1 and index < len(active[0].inputs):
            return constant_socket_value(active[0].inputs[index], (node, group_context), visited)
    return None


def covered_uniform_pixels(image, coverage=None, include_alpha=True):
    """Inspect every covered pixel; bake alpha marks geometry, not surface opacity.

    Metallic-map alpha contains smoothness after packing, so its unchanged
    roughness bake supplies the coverage mask. No sampling or color tolerance
    can turn a varying texture into a scalar.
    """
    import numpy as np  # Part of the supported native Blender runtime.
    size = int(image.size[0]) * int(image.size[1])
    if not size or (coverage and tuple(coverage.size) != tuple(image.size)):
        return None
    values = np.empty(size * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    values = values.reshape((-1, 4))
    if not np.isfinite(values).all():
        return None
    if coverage:
        mask_values = np.empty(size * 4, dtype=np.float32)
        coverage.pixels.foreach_get(mask_values)
        mask_values = mask_values.reshape((-1, 4))
        if not np.isfinite(mask_values).all():
            return None
        mask = mask_values[:, 3] > 0
    else:
        mask = values[:, 3] > 0
    used = int(np.count_nonzero(mask))
    if not used:
        return None
    selected = values[mask]
    low, high = selected.min(axis=0), selected.max(axis=0)
    components = 4 if include_alpha else 3
    if not np.array_equal(low[:components], high[:components]):
        return None
    return {"rgba": [float(value) for value in low], "pixels_checked": size,
            "covered_pixels": used}


def optimize_baked_channels(meshes, report):
    """Eliminate proven constant baked channels, retaining varying map detail."""
    backups = {entry["name"]: entry.get("source_material_backup", "AF_Source_" + entry["name"])
               for entry in report.get("baked_materials", [])}
    materials = sorted({mat for mesh in meshes for mat in mesh.data.materials
                        if mat and mat.name in backups}, key=lambda mat: mat.name)
    reductions = report.setdefault("baked_channel_optimizations", [])
    for material in materials:
        tree = material.node_tree
        shaders = [node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"]
        source = bpy.data.materials.get(backups[material.name])
        source_shaders = [node for node in source.node_tree.nodes if node.type == "BSDF_PRINCIPLED"] if source and source.node_tree else []
        if len(shaders) != 1 or len(source_shaders) != 1:
            continue
        shader, original = shaders[0], source_shaders[0]
        channels = {str(node.image.get("avatarforge_channel")): node.image
                    for node in tree.nodes if node.type == "TEX_IMAGE" and node.image
                    and node.image.get("avatarforge_owned")}

        def remove_channel(channel, socket, value, proof, inspection=None):
            image = channels.get(channel)
            if image is None:
                return
            for link in list(shader.inputs[socket].links):
                tree.links.remove(link)
            if value is not None:
                shader.inputs[socket].default_value = value
            for node in list(tree.nodes):
                if node.type == "TEX_IMAGE" and node.image == image:
                    tree.nodes.remove(node)
            for node in list(tree.nodes):
                if node.type == "NORMAL_MAP" and not any(output.links for output in node.outputs):
                    tree.nodes.remove(node)
            reductions.append({"material": material.name, "channel": channel,
                               "action": "scalar" if value is not None else "flat_normal",
                               "source_size": list(image.size), "proof": proof,
                               **(inspection or {})})

        # An unlinked normal uses the imported mesh normals. Baking a flat
        # tangent normal map adds memory without adding any source detail.
        if not original.inputs["Normal"].is_linked:
            remove_channel("normal", "Normal", None, "unlinked_source_normal")

        emission = channels.get("emission")
        source_color = constant_socket_value(original.inputs["Emission Color"])
        source_strength = constant_socket_value(original.inputs["Emission Strength"])
        if isinstance(source_strength, float) and source_strength == 0:
            remove_channel("emission", "Emission Color", (0, 0, 0, 1), "zero_source_emission_strength")
            shader.inputs["Emission Strength"].default_value = 1
        elif isinstance(source_color, tuple) and len(source_color) == 4 and isinstance(source_strength, float):
            remove_channel("emission", "Emission Color", source_color, "constant_source_emission")
            shader.inputs["Emission Strength"].default_value = source_strength
        elif emission:
            uniform = covered_uniform_pixels(emission, include_alpha=False)
            if uniform and uniform["rgba"][:3] == [0.0, 0.0, 0.0]:
                remove_channel("emission", "Emission Color", (0, 0, 0, 1), "exact_covered_black_pixels", uniform)
                shader.inputs["Emission Strength"].default_value = 1

        metallic, roughness = channels.get("metallic_smoothness"), channels.get("roughness")
        if metallic and roughness:
            source_metallic = constant_socket_value(original.inputs["Metallic"])
            source_roughness = constant_socket_value(original.inputs["Roughness"])
            scalar = None
            if isinstance(source_metallic, float) and isinstance(source_roughness, float):
                scalar = (source_metallic, source_roughness)
                proof, uniform = "constant_source_metallic_roughness", None
            else:
                uniform = covered_uniform_pixels(metallic, roughness)
                rough_uniform = covered_uniform_pixels(roughness, include_alpha=False) if uniform else None
                if uniform and rough_uniform and len(set(uniform["rgba"][:3])) == 1 and len(set(rough_uniform["rgba"][:3])) == 1:
                    scalar = (uniform["rgba"][0], rough_uniform["rgba"][0])
                    proof = "exact_covered_metallic_smoothness_pixels"
            if scalar:
                remove_channel("metallic_smoothness", "Metallic", scalar[0], proof, uniform)
                remove_channel("roughness", "Roughness", scalar[1], proof, uniform)

        # Bake target nodes are temporary work state, not part of the source
        # graph. Clear them from the preserved copy so discarded maps stay
        # dormant and cannot be mistaken for original source textures.
        for node in list(source.node_tree.nodes):
            if node.type == "TEX_IMAGE" and node.label == "AvatarForge bake target":
                source.node_tree.nodes.remove(node)
    if reductions:
        issue(report, "info", "constant_baked_channels_removed",
              "Removed " + str(len(reductions)) + " redundant baked channel maps using source constants or every covered pixel. Varying color, alpha, normal and surface detail remains at the chosen texture resolution.")


def texture_manifest(meshes, source, output, preset, options, report):
    materials = sorted({m for mesh in meshes for m in mesh.data.materials if m}, key=lambda m: m.name)
    folder = output / "textures"
    folder.mkdir(exist_ok=True)
    manifest, paths, candidates = [], {}, None
    limit = int(options.get("texture_size", PRESETS[preset]["texture_size"]))
    if limit and not 64 <= limit <= 16384:
        raise ValueError("texture_size must be 0 or 64..16384 pixels")
    for material in materials:
        outputs = [node for node in material.node_tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output] if material.node_tree else []
        if material.use_nodes and (len(outputs) != 1 or not outputs[0].inputs["Surface"].is_linked):
            issue(report, "warning", "material_surface_unconnected", material.name + ": no active linked material Surface. Texture extraction is an approximation; inspect the source shader before using Unity materials.")
    flatten_udims(meshes, materials, source, output, limit, report)
    mode = options.get("bake_materials", "auto")
    if mode not in (True, False, "auto"):
        raise ValueError("bake_materials must be true, false or 'auto'")
    auto_candidates = []
    if mode == "auto":
        simple = {"BSDF_PRINCIPLED", "OUTPUT_MATERIAL", "TEX_IMAGE", "NORMAL_MAP", "REROUTE", "TEX_COORD", "FRAME"}
        for material in materials:
            nodes = list(material.node_tree.nodes) if material.node_tree else []
            shaders = [n for n in nodes if n.type == "BSDF_PRINCIPLED"]
            outputs = [n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
            if len(shaders) != 1 or len(outputs) != 1:
                continue
            surface = outputs[0].inputs["Surface"]
            if not surface.links or surface.links[0].from_node != shaders[0]:
                continue
            shader = shaders[0]
            uv_mismatch = any(mesh.data.uv_layers and not mesh.data.uv_layers[0].active_render
                              for mesh in meshes if material in mesh.data.materials[:])
            if uv_mismatch or any(n.type not in simple for n in nodes) or any(shader.inputs[name].is_linked for name in ("Roughness", "Metallic", "Alpha")):
                auto_candidates.append(material)
    if mode is True or auto_candidates:
        selected = materials if mode is True else auto_candidates
        bake_materials(meshes, selected, int(options.get("bake_size", limit or 2048)), report)
        optimize_baked_channels(meshes, report)
    report["material_recipe"] = {"mode": "bake" if mode is True else "textures" if mode is False else "auto",
                                 "bake_size": int(options.get("bake_size", limit or 2048)),
                                 "auto_bake_candidates": [m.name for m in auto_candidates]}
    images = set().union(*(material_images(m) for m in materials)) if materials else set()
    for index, image in enumerate(sorted(images, key=lambda i: i.name)):
        if image.source not in {"FILE", "GENERATED"}:
            issue(report, "warning", "texture_type", image.name + ": tiled/movie/sequence texture requires review.")
            continue
        original = Path(bpy.path.abspath(image.filepath)) if image.filepath else None
        if image.source == "FILE" and not image.packed_file and (not original or not original.is_file()):
            if candidates is None:
                candidates = {}
                complete_search = True
                for examined, candidate in enumerate(source.parent.rglob("*")):
                    if examined >= 50000:
                        complete_search = False
                        break
                    if candidate.is_file() and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".tga", ".dds", ".bmp", ".webp", ".exr", ".tif", ".tiff"}:
                        candidates.setdefault(candidate.name.casefold(), []).append(candidate)
            basename = re.split(r"[/\\]", image.filepath)[-1].casefold()
            matches = candidates.get(basename, [])
            if complete_search and len(matches) == 1:
                image.filepath = str(matches[0])
                image.reload()
            else:
                issue(report, "warning", "missing_texture", image.name + ": texture missing or basename ambiguous; no guessed replacement.")
                continue
        try:
            # Blender decodes packed/generated pixels lazily; has_data alone is not a load attempt.
            image.pixels[:4]
            if not image.has_data:
                if image.source == "FILE" and not image.packed_file:
                    image.reload()
                    image.pixels[:4]
            if not image.has_data or min(image.size) <= 0:
                raise ValueError("image has no decoded pixels")
            copy = image if image.get("avatarforge_owned") else image.copy()
            width, height = copy.size
            if limit and max(width, height) > limit:
                ratio = limit / max(width, height)
                copy.scale(max(1, round(width * ratio)), max(1, round(height * ratio)))
            stem = re.sub(r"[^\w.-]", "_", Path(image.name).stem)[:100] or "texture"
            relative = "textures/" + f"{index:03d}_{stem}.png"
            copy.filepath_raw = str(output / relative)
            copy.file_format = "PNG"
            copy.save()
            if copy.packed_file:
                # Keep the portable resized PNG authoritative rather than stale packed bytes.
                copy.unpack(method="REMOVE")
            # Remap the working copy so .blend and FBX resolve the same portable textures.
            for material in materials:
                replace_image(material.node_tree, image, copy, set())
            paths[image] = relative
            paths[copy] = relative
            manifest.append({"name": image.name, "path": relative, "size": list(copy.size), "colorspace": image.colorspace_settings.name})
        except Exception as exc:
            issue(report, "warning", "texture_export_failed", image.name + ": " + str(exc))
    converted = []
    for material in materials:
        entry = {"name": material.name, "base_color": list(material.diffuse_color), "base_color_texture": None,
                 "normal_texture": None, "metallic": material.metallic, "roughness": material.roughness,
                 "alpha_mode": "OPAQUE", "alpha_cutoff": 0.5}
        nodes = list(material.node_tree.nodes) if material.node_tree else []
        principled = [n for n in nodes if n.type == "BSDF_PRINCIPLED"]
        if len(principled) == 1:
            shader = principled[0]
            for field, socket_name in (("base_color_texture", "Base Color"), ("normal_texture", "Normal")):
                sockets = upstream_images(shader.inputs[socket_name])
                if len(sockets) == 1:
                    image = next(iter(sockets))
                    entry[field] = paths.get(image)
                    entry[field.replace("_texture", "_scale")] = list(image.get("avatarforge_uv_scale", [1.0, 1.0]))
                elif len(sockets) > 1:
                    issue(report, "warning", "material_texture_mix", material.name + ": " + socket_name + " blends multiple textures; bake or reconstruct in Unity.")
            for field, socket_name in (("metallic_smoothness_texture", "Metallic"), ("roughness_texture", "Roughness"), ("emission_texture", "Emission Color")):
                sockets = upstream_images(shader.inputs[socket_name])
                if len(sockets) == 1:
                    image = next(iter(sockets))
                    target_field = "metallic_texture" if field == "metallic_smoothness_texture" and image.get("avatarforge_channel") != "metallic_smoothness" else field
                    entry[target_field] = paths.get(image)
                    entry[target_field.replace("_texture", "_scale")] = list(image.get("avatarforge_uv_scale", [1.0, 1.0]))
            # A linked texture replaces this default; Unity's color multiplies its albedo map.
            entry["base_color"] = [1.0, 1.0, 1.0, 1.0] if shader.inputs["Base Color"].is_linked else list(shader.inputs["Base Color"].default_value)
            entry["metallic"] = float(shader.inputs["Metallic"].default_value)
            entry["roughness"] = float(shader.inputs["Roughness"].default_value)
            emission = shader.inputs.get("Emission Color")
            strength = shader.inputs.get("Emission Strength")
            entry["emission_color"] = [1.0, 1.0, 1.0, 1.0] if emission.is_linked else list(emission.default_value)
            entry["emission_strength"] = float(strength.default_value) if strength and not strength.is_linked else 1.0
            alpha = shader.inputs.get("Alpha")
            if alpha and not alpha.is_linked:
                entry["base_color"][3] = float(alpha.default_value)
            if alpha and (alpha.is_linked or alpha.default_value < 0.999):
                entry["alpha_mode"] = "BLEND"
            if material.get("avatarforge_baked_alpha_mode"):
                entry["alpha_mode"] = material["avatarforge_baked_alpha_mode"]
            complex_nodes = {n.type for n in nodes} - {"BSDF_PRINCIPLED", "OUTPUT_MATERIAL", "TEX_IMAGE", "NORMAL_MAP", "REROUTE", "TEX_COORD", "UVMAP", "FRAME"}
            if complex_nodes:
                issue(report, "warning", "custom_material", material.name + ": shader processing needs baking/review; copied texture extraction is an approximation.")
        else:
            issue(report, "warning", "custom_material", material.name + ": no single Principled shader; textures copied but material requires baking/review.")
        converted.append(entry)
    report["textures"], report["materials"] = manifest, converted
    report["objects"] = [{"name": m.name, "materials": [mat.name if mat else "" for mat in m.data.materials]} for m in meshes]


def replace_image(tree, original, replacement, visited):
    if not tree or tree.as_pointer() in visited:
        return
    visited.add(tree.as_pointer())
    for node in tree.nodes:
        if node.type == "TEX_IMAGE" and node.image == original:
            node.image = replacement
        elif node.type == "GROUP":
            replace_image(node.node_tree, original, replacement, visited)


def preview(meshes, output, report):
    """Render locally on the CPU, including hosts without a graphics context."""
    scene = bpy.context.scene
    previous = (scene.render.engine, scene.cycles.device, scene.cycles.samples,
                scene.cycles.use_denoising, scene.camera, scene.world,
                scene.render.resolution_x, scene.render.resolution_y,
                scene.render.resolution_percentage, scene.render.filepath,
                scene.render.image_settings.file_format)
    visibility = [(obj, obj.hide_render) for obj in scene.objects
                  if obj.type in {"MESH", "LIGHT"}]
    modifiers = [(modifier, modifier.show_render) for mesh in meshes
                 for modifier in mesh.modifiers if modifier.type != "ARMATURE"]
    media_type = getattr(scene.render.image_settings, "media_type", None)
    camera = light = world = None
    try:
        if media_type is not None:
            scene.render.image_settings.media_type = "IMAGE"
        scene.render.engine = "CYCLES"
        scene.cycles.device, scene.cycles.samples = "CPU", 8
        scene.cycles.use_denoising = False
        scene.render.resolution_x = scene.render.resolution_y = 640
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        selected = set(meshes)
        for obj, _ in visibility:
            if obj.type == "MESH":
                obj.hide_render = obj not in selected
            else:
                obj.hide_render = True
        coords = [mesh.matrix_world @ Vector(corner) for mesh in meshes for corner in mesh.bound_box]
        minimum = Vector(tuple(min(c[i] for c in coords) for i in range(3)))
        maximum = Vector(tuple(max(c[i] for c in coords) for i in range(3)))
        center, extent = (maximum + minimum) / 2, max(maximum - minimum)
        data = bpy.data.cameras.new("AvatarForge_Preview")
        camera = bpy.data.objects.new("AvatarForge_Preview", data)
        scene.collection.objects.link(camera)
        camera.location = center + Vector((0, -max(extent, 0.1) * 2.5, extent * 0.08))
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        data.type = "ORTHO"
        data.ortho_scale = max(extent, 0.1) * 1.15
        scene.camera = camera
        world = bpy.data.worlds.new("AvatarForge_Preview")
        world.use_nodes = True
        world.node_tree.nodes.get("Background").inputs["Color"].default_value = (.15, .15, .15, 1)
        scene.world = world
        light_data = bpy.data.lights.new("AvatarForge_Preview", "AREA")
        light = bpy.data.objects.new("AvatarForge_Preview_Light", light_data)
        scene.collection.objects.link(light)
        light.location = center + Vector((extent * .6, -extent * 1.5, extent * 1.5))
        light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
        light_data.energy, light_data.size = 500 * max(extent, .1) ** 2, max(extent, .1)
        # Preview is raw export geometry: modifiers omitted by FBX must not misrepresent it.
        for modifier, _ in modifiers:
            modifier.show_render = False
        scene.render.filepath = str(output / "preview.png")
        bpy.ops.render.render(write_still=True)
        report["preview"] = "preview.png"
        report["preview_renderer"] = "cycles_cpu"
    except Exception as exc:
        issue(report, "info", "preview_unavailable", str(exc))
    finally:
        if media_type is not None:
            scene.render.image_settings.media_type = media_type
        (scene.render.engine, scene.cycles.device, scene.cycles.samples,
         scene.cycles.use_denoising, scene.camera, scene.world,
         scene.render.resolution_x, scene.render.resolution_y,
         scene.render.resolution_percentage, scene.render.filepath,
         scene.render.image_settings.file_format) = previous
        for obj, hidden in visibility:
            obj.hide_render = hidden
        for modifier, enabled in modifiers:
            modifier.show_render = enabled
        for obj, collection in ((camera, bpy.data.cameras), (light, bpy.data.lights)):
            if obj is not None:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                collection.remove(data)
        if world is not None:
            bpy.data.worlds.remove(world)


def export_and_verify(rig, meshes, output, source_bones, excluded, source_shapes, report):
    expected_meshes = {mesh.name: {"vertices": len(mesh.data.vertices), "triangles": triangles([mesh])} for mesh in meshes}
    chosen = meshes + ([rig] if rig else [])
    bpy.ops.object.select_all(action="DESELECT")
    for obj in chosen:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = rig or meshes[0]
    # Applying modifiers through FBX destroys shape keys. Export original topology explicitly.
    call("export_scene.fbx", filepath=str(output / "model.fbx"), use_selection=True,
         object_types={"ARMATURE", "MESH"}, use_mesh_modifiers=False,
         use_armature_deform_only=False, add_leaf_bones=False, bake_anim=False,
         path_mode="COPY", embed_textures=False, axis_forward="-Z", axis_up="Y",
         apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS", use_custom_props=True)
    # Saved conversions must survive moving the app or copying the output folder.
    # FBX has already resolved the absolute PNGs above. The blend stores only
    # images inside this conversion relative to its own directory.
    # Source auto-pack must not resolve output-relative images against the old source directory while saving.
    bpy.data.use_autopack = False
    for image in bpy.data.images:
        if not image.filepath or image.packed_file:
            continue
        path = Path(bpy.path.abspath(image.filepath)).resolve()
        if path.is_relative_to(output):
            image.filepath = "//" + path.relative_to(output).as_posix()
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "model.blend"), check_existing=False, relative_remap=False)
    # Read actual FBX bytes back: in-memory counts alone are not export proof.
    detach_source_logging_handlers()
    # Source Tools' unregister tests a different callback than it registered;
    # detach its stale Scene.vs callbacks without disturbing other addon unloads.
    detach_source_tools_handlers()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    call("import_scene.fbx", filepath=str(output / "model.fbx"), use_anim=False)
    detach_source_logging_handlers()
    exported_rigs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    export_bones = sorted({b.name for r in exported_rigs for b in r.data.bones})
    exported_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    actual_meshes = {mesh.name: {"vertices": len(mesh.data.vertices), "triangles": triangles([mesh])} for mesh in exported_meshes}
    geometry_errors = {name: {"expected": counts, "actual": actual_meshes.get(name)} for name, counts in expected_meshes.items() if actual_meshes.get(name) != counts}
    export_shapes = shape_manifest(exported_meshes)
    export_weighted = set().union(*(weighted_bones(exported_meshes, r) for r in exported_rigs)) if exported_rigs else set()
    missing_weighted = sorted(set(report.get("weighted_bones", [])) - export_weighted)
    missing_bones = sorted(set(source_bones) - set(excluded) - set(export_bones))
    missing_shapes = {mesh: sorted(set(keys) - set(export_shapes.get(mesh, []))) for mesh, keys in source_shapes.items()}
    missing_shapes = {mesh: keys for mesh, keys in missing_shapes.items() if keys}
    report["integrity"] = {"source_bones": source_bones, "export_bones": export_bones,
                           "missing_bones": missing_bones, "intentionally_excluded_controllers": excluded,
                           "source_shape_keys": source_shapes, "export_shape_keys": export_shapes,
                           "missing_shape_keys": missing_shapes, "fbx_roundtrip_verified": True,
                           "source_meshes": expected_meshes, "export_meshes": actual_meshes, "geometry_errors": geometry_errors,
                           "source_weighted_bones": report.get("weighted_bones", []),
                           "export_weighted_bones": sorted(export_weighted), "missing_weighted_bones": missing_weighted}
    report["export_bones"] = export_bones
    if geometry_errors:
        issue(report, "error", "export_mesh_integrity", "FBX round-trip changed or lost selected mesh geometry: " + ", ".join(geometry_errors))
    if missing_bones or missing_shapes:
        issue(report, "error", "export_integrity", "FBX round-trip lost required bones or shape keys. Do not use this export until repaired.")
    else:
        issue(report, "info", "export_integrity_verified", "Actual FBX reimport retained every required bone and shape key.")
    if missing_weighted:
        issue(report, "error", "export_weight_integrity", "Some source bone influences disappeared after optimization/export: " + ", ".join(missing_weighted) + ". Use Preserve or repair skin weights before proceeding.")


def run(job):
    source, output = Path(job["source"]), Path(job["output"])
    if not source.is_absolute() or not output.is_absolute():
        raise ValueError("source and output must be absolute paths")
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source in {output / "model.blend", output / "model.fbx"}:
        raise ValueError("Output would overwrite the source model")
    output.mkdir(parents=True, exist_ok=True)
    preset = job.get("preset", "preserve")
    report = {"schema_version": 1, "source": str(source), "preset": preset,
              "status": "blocked", "issues": [], "summary": {}, "humanoid": [], "physics": [],
              "missing_required_humanoid": [], "blender_version": bpy.app.version_string,
              "animation": {"mode": "avatar_bind_pose", "embedded_clips": False, "note": "VRChat drives the prepared rig. Source Blender actions, drivers and simulation are not exported as animation clips."}}
    try:
        if preset not in PRESETS:
            raise ValueError("Unknown preset: " + str(preset))
        options = job.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("options must be a JSON object")
        detach_source_tools_handlers()
        if source.suffix.lower() != ".blend":
            detach_source_logging_handlers()
            bpy.ops.wm.read_factory_settings(use_empty=True)
        enable_addons(job.get("addon_paths", []), report, source.suffix.lower())
        import_source(source, report, options)
        rig, meshes = choose_objects(options, report)
        renames = []
        for mesh in meshes:
            keys = mesh.data.shape_keys
            if keys:
                for key in keys.key_blocks:
                    if key != keys.reference_key and key.name == "Basis":
                        key.name = "AF_Basis_Morph"
                        renames.append({"mesh": mesh.name, "source": "Basis", "export": key.name})
        if renames:
            report["shape_key_renames"] = renames
            issue(report, "info", "reserved_shape_name", "Renamed real morphs called Basis in the conversion copy because Blender FBX reserves that name. Shape geometry and fitting values are retained.")
        source_shapes = shape_manifest(meshes)
        freeze_source_inputs(meshes, report)
        freeze_shape_defaults(meshes, report)
        source_weighted = weighted_bones(meshes, rig)
        materialize_visibility_masks(meshes, options, report)
        report["intentionally_masked_weighted_bones"] = sorted(source_weighted - weighted_bones(meshes, rig))
        if report["intentionally_masked_weighted_bones"]:
            issue(report, "info", "masked_skin_influences", "Some bone influences belonged entirely to authored hidden geometry; their bones remain in the skeleton: " + ", ".join(report["intentionally_masked_weighted_bones"]))
        source_bones, excluded, weighted = prepare_rig(rig, meshes, options, report)
        optimize(meshes, rig, preset, options, report)
        bones_report(rig, options, report)
        texture_manifest(meshes, source, output, preset, options, report)
        report["summary"] = {"triangles": triangles(meshes), "vertices": sum(len(m.data.vertices) for m in meshes),
                             "meshes": len(meshes), "materials": len({mat for m in meshes for mat in m.data.materials if mat}),
                             "bones": len(rig.data.bones) if rig else 0, "weighted_bones": len(weighted),
                             "shape_keys": sum(len(keys) for keys in source_shapes.values()), "textures": len(report["textures"])}
        if options.get("preview", True):
            preview(meshes, output, report)
        export_and_verify(rig, meshes, output, source_bones, excluded, source_shapes, report)
        report["status"] = "blocked" if any(i["severity"] == "error" for i in report["issues"]) else (
            "needs_review" if any(i["severity"] == "warning" for i in report["issues"]) else "ready")
        report["artifacts"] = {"blend": "model.blend", "fbx": "model.fbx", "report": "report.json"}
    except Exception as exc:
        issue(report, "error", "capability_blocker" if isinstance(exc, CapabilityError) else "conversion_failed", str(exc))
        report["error_type"] = type(exc).__name__
        traceback.print_exc()
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("AVATARFORGE_REPORT " + str(output / "report.json"), flush=True)
    return report


if __name__ == "__main__":
    try:
        args = sys.argv[sys.argv.index("--") + 1:]
        if len(args) != 1:
            raise ValueError("Expected one absolute job.json after --")
        report = run(json.loads(Path(args[0]).read_text(encoding="utf-8-sig")))
        if report["status"] == "blocked":
            sys.exit(2)
    except Exception:
        traceback.print_exc()
        sys.exit(2)

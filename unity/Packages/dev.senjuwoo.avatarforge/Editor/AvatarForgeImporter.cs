using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

namespace AvatarForge.Editor
{
    [Serializable] public sealed class Mapping { public string humanName, boneName; public float confidence; }
    [Serializable] public sealed class PhysicsSuggestion { public string bone, path, category; public float confidence, endpoint_length; public bool approved; }
    [Serializable] public sealed class ConversionIssue { public string severity, code, message; }
    [Serializable] public sealed class MaterialManifest
    {
        public string name, base_color_texture, normal_texture, alpha_mode, metallic_smoothness_texture, emission_texture;
        public float[] base_color, base_color_scale, normal_scale, metallic_smoothness_scale, emission_scale, emission_color;
        public float metallic, roughness = 0.5f, alpha_cutoff = 0.5f;
        public float emission_strength = 1;
    }
    [Serializable] public sealed class ObjectManifest { public string name; public string[] materials; }
    [Serializable] public sealed class ShapeManifest { public string @object; public string[] names; public float[] values; }
    [Serializable] public sealed class IntegrityManifest { public string[] export_bones, export_weighted_bones; }
    [Serializable] public sealed class OptionalPart { public string mesh, armature, bone; public bool start_hidden; }
    [Serializable] public sealed class ConversionOptimization { public int target_triangles; }
    [Serializable] public sealed class ConversionReport
    {
        public int schema_version;
        public string preset;
        public Mapping[] humanoid;
        public PhysicsSuggestion[] physics;
        public MaterialManifest[] materials;
        public ObjectManifest[] objects;
        public ShapeManifest[] shape_keys;
        public string[] export_bones, export_weighted_bones, missing_required_humanoid;
        public IntegrityManifest integrity;
        public ConversionOptimization optimization;
        public OptionalPart[] optional_parts;
        public ConversionIssue[] issues;
    }
    [Serializable] public sealed class PhysicsApproval { public string[] approved_physics = Array.Empty<string>(); }
    [Serializable] public sealed class PhysicsResult { public string bone, category, status, reason; }
    [Serializable] public sealed class UnityReport
    {
        public int schema_version = 1;
        public string status = "blocked", unity_version, asset_folder, prefab, scene;
        public bool humanoid_valid, humanoid_human, sdk_available, descriptor_added, bone_integrity_verified;
        public bool humanoid_pose_verified, humanoid_pose_calibrated;
        public float humanoid_pose_error = -1, humanoid_pose_error_before = -1;
        public bool pipeline_manager_added;
        public bool uploaded = false, sdk_build_validated = false;
        public string[] missing_bones = Array.Empty<string>(), missing_required_humanoid = Array.Empty<string>();
        public string[] material_texture_references = Array.Empty<string>();
        public bool blendshape_integrity_verified;
        public bool blendshape_defaults_verified;
        public int blendshape_default_count;
        public bool skin_weight_integrity_verified;
        public int max_skin_influences;
        public string[] missing_weighted_bones = Array.Empty<string>();
        public string[] missing_blendshapes = Array.Empty<string>();
        public PhysicsResult[] physbones = Array.Empty<PhysicsResult>();
        public ConversionIssue[] issues = Array.Empty<ConversionIssue>();
        public int triangles, skinned_meshes, mesh_renderers, material_slots, bones, blendshapes, physbone_components, physbone_transforms;
        public string original_prefab;
        public OptimizationReport optimization;
        public int referenced_texture_count;
        public long texture_memory_bytes = -1;
        public bool texture_storage_estimate_available;
        public string texture_memory_note;
    }

    public static class AvatarForgeImporter
    {
        const string LastInput = "AvatarForge.LastInput";
        const string DescriptorName = "VRC.SDK3.Avatars.Components.VRCAvatarDescriptor";
        const string PhysBoneName = "VRC.SDK3.Dynamics.PhysBone.Components.VRCPhysBone";
        static readonly string[] Visemes = { "sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "ih", "oh", "ou" };

        [MenuItem("AvatarForge/Import conversion folder...")]
        public static void ImportMenu()
        {
            string folder = EditorUtility.OpenFolderPanel("Select AvatarForge conversion (model.fbx + report.json)", SessionState.GetString(LastInput, ""), "");
            if (string.IsNullOrEmpty(folder)) return;
            try
            {
                UnityReport report = Import(folder);
                EditorUtility.DisplayDialog("AvatarForge: " + report.status, "Prefab: " + report.prefab + "\nReport: " + Path.Combine(folder, "unity-report.json") + "\n" + string.Join("\n", report.issues.Select(x => x.message).Take(8)), "OK");
                if (report.prefab != null) Selection.activeObject = AssetDatabase.LoadAssetAtPath<GameObject>(report.prefab);
            }
            catch (Exception exception) { Debug.LogException(exception); EditorUtility.DisplayDialog("AvatarForge import failed", exception.Message, "OK"); }
        }

        // Unity.exe -batchmode -projectPath <owned-project> -executeMethod AvatarForge.Editor.AvatarForgeImporter.Batch -avatarForgeInput <conversion> -quit
        public static void Batch()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "-avatarForgeInput");
            try
            {
                if (index < 0 || index + 1 >= args.Length) throw new ArgumentException("Missing -avatarForgeInput <conversion folder>.");
                UnityReport report = Import(args[index + 1]);
                Debug.Log("AvatarForge result: " + report.status + " at " + report.prefab);
                if (Application.isBatchMode) EditorApplication.Exit(report.status == "blocked" ? 2 : 0);
            }
            catch (Exception exception) { Debug.LogException(exception); if (Application.isBatchMode) EditorApplication.Exit(1); else throw; }
        }

        // Used when the desktop app opens the prepared, dedicated Unity project for review.
        public static void OpenPreparedScene()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "-avatarForgeInput");
            if (index < 0 || index + 1 >= args.Length) throw new ArgumentException("Missing -avatarForgeInput <conversion folder>.");
            string input = Path.GetFullPath(args[index + 1]);
            var report = JsonUtility.FromJson<UnityReport>(File.ReadAllText(ResolveContainedFile(input, "unity-report.json")));
            string scene = (report?.scene ?? "").Replace('\\', '/');
            if (!scene.StartsWith("Assets/AvatarForge/", StringComparison.Ordinal) || scene.Split('/').Any(x => x == "..")) throw new InvalidDataException("The Unity report does not reference a prepared AvatarForge scene.");
            ResolveContainedFile(Application.dataPath, scene.Substring("Assets/".Length));
            if (Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).Any(s => s.isDirty))
            { Debug.LogWarning("AvatarForge preserved unsaved scene work. Open the prepared scene manually: " + scene); return; }
            EditorSceneManager.OpenScene(scene, OpenSceneMode.Single);
            var avatar = AssetDatabase.LoadAssetAtPath<GameObject>(report.prefab);
            if (avatar) Selection.activeObject = avatar;
            SceneView.RepaintAll();
            Debug.Log("AvatarForge opened prepared scene: " + scene);
        }

        public static UnityReport Import(string input)
        {
            input = Path.GetFullPath(input);
            var output = new UnityReport { unity_version = Application.unityVersion };
            var issues = new List<ConversionIssue>();
            GameObject instance = null;
            Scene workingScene = default;
            try
            {
                ConversionReport source = LoadReport(input);
                issues.AddRange(source.issues ?? Array.Empty<ConversionIssue>());
                var approvals = LoadApprovals(input);
                if (!AssetDatabase.IsValidFolder("Assets/AvatarForge")) AssetDatabase.CreateFolder("Assets", "AvatarForge");
                string destination = AssetDatabase.GenerateUniqueAssetPath("Assets/AvatarForge/" + SafeName(new DirectoryInfo(input).Name));
                if (string.IsNullOrEmpty(destination)) throw new InvalidOperationException("Unity could not allocate the AvatarForge asset folder.");
                Directory.CreateDirectory(destination);
                File.Copy(ResolveContainedFile(input, "model.fbx"), destination + "/model.fbx", false);
                CopyTextures(input, destination);
                File.Copy(ResolveContainedFile(input, "report.json"), destination + "/conversion-report.json", false);
                output.asset_folder = destination;
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                string modelPath = destination + "/model.fbx";
                var importer = (ModelImporter)AssetImporter.GetAtPath(modelPath);
                importer.preserveHierarchy = true;
                importer.optimizeBones = false;
                importer.optimizeGameObjects = false;
                importer.isReadable = true;
                importer.importBlendShapes = true;
                ConfigureBlendShapeNormals(importer);
                importer.importAnimation = false;
                importer.materialImportMode = ModelImporterMaterialImportMode.None;
                importer.skinWeights = ModelImporterSkinWeights.Custom;
                importer.maxBonesPerVertex = 255;
                importer.minBoneWeight = 0;
                importer.animationType = ModelImporterAnimationType.Generic;
                importer.SaveAndReimport();
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
                if (!model) throw new InvalidDataException("Unity could not import model.fbx.");
                var transforms = model.GetComponentsInChildren<Transform>(true);
                var mapping = BuildHumanMap(source, transforms, issues, out string[] missing, out bool hierarchyCompatible);
                output.missing_required_humanoid = missing;
                if (missing.Length == 0 && hierarchyCompatible)
                {
                    var description = importer.humanDescription;
                    description.human = mapping;
                    try
                    {
                        description.skeleton = AvatarForgeHumanoidPose.Calibrate(model, mapping, out output.humanoid_pose_error_before, out output.humanoid_pose_error);
                        output.humanoid_pose_calibrated = true;
                    }
                    catch (Exception exception)
                    {
                        description.skeleton = transforms.Select(t => new SkeletonBone { name = t.name, position = t.localPosition, rotation = t.localRotation, scale = t.localScale }).ToArray();
                        AddIssue(issues, "error", "HUMANOID_TPOSE_FAILED", "Humanoid Configure T-Pose calibration failed: " + (exception is TargetInvocationException invocation ? invocation.InnerException?.Message : exception.Message));
                    }
                    description.upperArmTwist = description.lowerArmTwist = description.upperLegTwist = description.lowerLegTwist = 0.5f;
                    description.armStretch = description.legStretch = 0.05f;
                    description.hasTranslationDoF = false;
                    importer.humanDescription = description;
                    importer.animationType = ModelImporterAnimationType.Human;
                    importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                    importer.SaveAndReimport();
                    model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
                    if (output.humanoid_pose_calibrated)
                    {
                        output.humanoid_pose_error = AvatarForgeHumanoidPose.VerifyMetadata(importer, model);
                        output.humanoid_pose_verified = output.humanoid_pose_error == 0;
                        if (!output.humanoid_pose_verified) AddIssue(issues, "error", "HUMANOID_TPOSE_INVALID", "Saved Humanoid calibration has pose alignment error " + output.humanoid_pose_error + ".");
                    }
                }
                workingScene = EditorSceneManager.NewPreviewScene();
                instance = (GameObject)PrefabUtility.InstantiatePrefab(model, workingScene);
                instance.name = SafeName(new DirectoryInfo(input).Name);
                if (output.humanoid_pose_verified)
                    output.humanoid_pose_error = AvatarForgeHumanoidPose.ApplyVerifiedPose(importer, instance, model.name);
                var animator = instance.GetComponent<Animator>();
                if (!animator) animator = instance.AddComponent<Animator>();
                var avatar = AssetDatabase.LoadAllAssetsAtPath(modelPath).OfType<Avatar>().FirstOrDefault();
                animator.avatar = avatar;
                animator.applyRootMotion = false;
                output.humanoid_valid = avatar && avatar.isValid;
                output.humanoid_human = avatar && avatar.isHuman;
                if (!output.humanoid_valid || !output.humanoid_human)
                    AddIssue(issues, missing.Length > 0 || !hierarchyCompatible ? "review" : "error", "HUMANOID_INVALID", "The imported Avatar is not a valid Humanoid. Generic preview retains all bones; tracked Humanoid motion requires a compatible mapping and rest pose.");
                VerifyBones(source, instance, output, issues);
                VerifyShapeKeys(source, instance, output, issues);
                VerifySkinWeights(source, instance, output, issues);
                ApplyShapeDefaults(source, instance, output, issues);
                BuildMaterials(source, input, destination, instance, output, issues);
                ApplyOptionalParts(source, instance, issues);
                CompensateRendererUnitScale(instance, issues);
                SetupSdk(source, approvals, instance, output, issues);
                output.prefab = destination + "/Avatar.prefab";
                PrefabUtility.SaveAsPrefabAsset(instance, output.prefab);
                output.original_prefab = output.prefab;
                bool optimize = source.preset == "balanced" || IsMobile(source.preset);
                int target = TriangleTarget(source, optimize);
                int triangleCount = CountTriangles(instance);
                output.optimization = new OptimizationReport
                {
                    status = optimize ? "not_needed" : "disabled", engine = "not_run",
                    target_triangles = target, before_triangles = triangleCount, after_triangles = triangleCount,
                    source_prefab = output.original_prefab, prefab = output.prefab,
                    bone_integrity_verified = output.bone_integrity_verified,
                    blendshape_integrity_verified = output.blendshape_integrity_verified,
                    weighted_bone_integrity_verified = output.skin_weight_integrity_verified
                };
                if (optimize && triangleCount > target)
                {
                    if (!AvatarForgeOptimizer.Available)
                    {
                        output.optimization.status = "unavailable";
                        AddIssue(issues, "review", "INSTALL_OPTIMIZATION", "Install the optimization tools to reduce this mesh while retaining its shape keys and bone influences, then import again.");
                    }
                    else
                    {
                        try
                        {
                            output.optimization = AvatarForgeOptimizer.Optimize(AssetDatabase.LoadAssetAtPath<GameObject>(output.prefab), target);
                            if (output.optimization.status == "blocked") throw new InvalidDataException("Optimization did not save a verified clone.");
                            output.prefab = output.optimization.prefab;
                            foreach (string message in output.optimization.issues ?? Array.Empty<string>())
                                AddIssue(issues, "review", "OPTIMIZATION_REVIEW", message);
                            AddIssue(issues, "info", "MESH_OPTIMIZED", "Verified optimized clone: " + output.optimization.before_triangles + " to " + output.optimization.after_triangles + " triangles; original prefab retained.");
                        }
                        catch (Exception exception)
                        {
                            output.optimization.status = "failed";
                            output.prefab = output.original_prefab;
                            AddIssue(issues, "review", "OPTIMIZATION_FAILED", exception.Message + " The original imported prefab is retained.");
                        }
                    }
                }
                var savedPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(output.prefab);
                if (output.humanoid_pose_calibrated)
                {
                    output.humanoid_pose_error = AvatarForgeHumanoidPose.VerifyPose(savedPrefab, mapping);
                    output.humanoid_pose_verified = output.humanoid_pose_error == 0;
                    if (!output.humanoid_pose_verified) AddIssue(issues, "error", "HUMANOID_PREFAB_TPOSE_INVALID", "The saved prepared prefab has pose alignment error " + output.humanoid_pose_error + ".");
                }
                VerifyShapeDefaults(source, savedPrefab, output, issues);
                CollectStatistics(source, savedPrefab, output, issues);
                CreatePreviewScene(destination, output, issues);
                AddIssue(issues, "review", "SDK_UPLOAD_REVIEW", "Open the prefab and use VRChat SDK Builder validation and Build & Test. Viewpoint, expressions, motion and appearance need a visual check before upload.");
                output.status = issues.Any(x => IsError(x.severity)) ? "blocked" : issues.Any(x => x.severity != "info") ? "needs_review" : "ready";
                SessionState.SetString(LastInput, input);
                AssetDatabase.SaveAssets();
            }
            catch (Exception exception)
            {
                AddIssue(issues, "error", "UNITY_IMPORT_FAILED", exception.Message);
                output.status = "blocked";
                throw;
            }
            finally
            {
                if (instance) UnityEngine.Object.DestroyImmediate(instance);
                if (workingScene.IsValid()) EditorSceneManager.ClosePreviewScene(workingScene);
                output.issues = issues.ToArray();
                if (Directory.Exists(input)) File.WriteAllText(Path.Combine(input, "unity-report.json"), JsonUtility.ToJson(output, true));
            }
            return output;
        }

        public static ConversionReport LoadReport(string input)
        {
            var report = JsonUtility.FromJson<ConversionReport>(File.ReadAllText(ResolveContainedFile(input, "report.json")));
            if (report == null || report.schema_version != 1) throw new InvalidDataException("Unsupported report.json schema; expected schema_version 1.");
            return report;
        }

        static int TriangleTarget(ConversionReport source, bool optimize)
        {
            int fallback = IsMobile(source.preset) ? 15000 : 70000;
            if (!optimize) return fallback;
            int requested = source.optimization?.target_triangles ?? 0;
            if (requested < 0 || requested > 10000000) throw new InvalidDataException("Conversion triangle target must be between 1 and 10,000,000, or absent.");
            return requested > 0 ? requested : fallback;
        }

        static PhysicsApproval LoadApprovals(string input)
        {
            string path = Path.Combine(input, "unity-overrides.json");
            return File.Exists(path) ? JsonUtility.FromJson<PhysicsApproval>(File.ReadAllText(ResolveContainedFile(input, "unity-overrides.json"))) ?? new PhysicsApproval() : null;
        }

        static HumanBone[] BuildHumanMap(ConversionReport source, Transform[] transforms, List<ConversionIssue> issues, out string[] missing, out bool hierarchyCompatible)
        {
            var names = HumanTrait.BoneName.ToDictionary(n => n.Replace(" ", ""), n => n, StringComparer.Ordinal);
            var transformNames = transforms.GroupBy(x => x.name).ToDictionary(g => g.Key, g => g.Count());
            var mapped = new List<HumanBone>();
            var assignedHumans = new HashSet<string>();
            var assignedBones = new HashSet<string>();
            foreach (var entry in source.humanoid ?? Array.Empty<Mapping>())
            {
                if (entry == null || !names.TryGetValue((entry.humanName ?? "").Replace(" ", ""), out string human))
                { AddIssue(issues, "review", "UNKNOWN_HUMANOID_BONE", "Unknown Humanoid mapping: " + entry?.humanName); continue; }
                if (!transformNames.TryGetValue(entry.boneName ?? "", out int count) || count != 1)
                { AddIssue(issues, "error", "AMBIGUOUS_HUMANOID_BONE", "Mapped bone must exist exactly once: " + entry.boneName); continue; }
                if (!assignedHumans.Add(human) || !assignedBones.Add(entry.boneName))
                { AddIssue(issues, "error", "DUPLICATE_HUMANOID_MAPPING", "Humanoid mapping repeats a Human or source bone: " + entry.boneName); continue; }
                if (entry.confidence < 0.9f) AddIssue(issues, "review", "HUMANOID_MAPPING_REVIEW", "Check inferred Humanoid mapping " + human + " = " + entry.boneName);
                mapped.Add(new HumanBone { humanName = human, boneName = entry.boneName, limit = new HumanLimit { useDefaultValues = true } });
            }
            string[] required = HumanTrait.BoneName.Where((n, i) => HumanTrait.RequiredBone(i)).ToArray();
            missing = required.Where(n => !assignedHumans.Contains(n)).ToArray();
            if (missing.Length > 0) AddIssue(issues, "review", "MISSING_REQUIRED_HUMANOID", "Generic rig retained; missing required Humanoid bones: " + string.Join(", ", missing));
            hierarchyCompatible = true;
            var byHuman = mapped.ToDictionary(bone => bone.humanName, bone => transforms.Single(transform => transform.name == bone.boneName));
            foreach (var bone in mapped)
            {
                int parent = HumanTrait.GetParentBone(Array.IndexOf(HumanTrait.BoneName, bone.humanName));
                while (parent >= 0 && !byHuman.ContainsKey(HumanTrait.BoneName[parent])) parent = HumanTrait.GetParentBone(parent);
                if (parent < 0 || byHuman[bone.humanName].IsChildOf(byHuman[HumanTrait.BoneName[parent]])) continue;
                hierarchyCompatible = false;
                AddIssue(issues, "review", "HUMANOID_HIERARCHY", "Generic rig retained: mapped " + bone.humanName + " (" + bone.boneName + ") is not below " + HumanTrait.BoneName[parent] + ". Bone names alone cannot make a disconnected chain track as Humanoid; repair the mapping or hierarchy, then reimport.");
            }
            return mapped.ToArray();
        }

        static void VerifyBones(ConversionReport source, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            string[] expected = source.export_bones ?? source.integrity?.export_bones;
            var names = new HashSet<string>(instance.GetComponentsInChildren<Transform>(true).Select(t => t.name));
            if (expected == null || expected.Length == 0)
            { AddIssue(issues, "review", "BONE_MANIFEST_MISSING", "No export_bones manifest: full bone retention cannot be verified."); return; }
            output.missing_bones = expected.Where(n => !names.Contains(n)).Distinct().ToArray();
            output.bone_integrity_verified = output.missing_bones.Length == 0;
            if (!output.bone_integrity_verified) AddIssue(issues, "error", "BONE_LOSS", "Bones missing after Unity import: " + string.Join(", ", output.missing_bones));
        }

        static void VerifyShapeKeys(ConversionReport source, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            if (source.shape_keys == null)
            {
                AddIssue(issues, "review", "BLENDSHAPE_MANIFEST_MISSING", "No shape_keys manifest: facial-key retention is not verified by name.");
                return;
            }
            var renderers = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            var missing = new List<string>();
            foreach (var entry in source.shape_keys)
            {
                var matching = renderers.Where(r => r.name == entry.@object && r.sharedMesh).ToArray();
                foreach (string name in entry.names ?? Array.Empty<string>())
                    if (matching.Length != 1 || matching[0].sharedMesh.GetBlendShapeIndex(name) < 0) missing.Add(entry.@object + "/" + name);
            }
            output.missing_blendshapes = missing.ToArray();
            output.blendshape_integrity_verified = missing.Count == 0;
            if (missing.Count > 0) AddIssue(issues, "error", "BLENDSHAPE_LOSS", "Shape keys missing after Unity import: " + string.Join(", ", missing));
        }

        static void VerifySkinWeights(ConversionReport source, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            var weighted = new HashSet<string>();
            foreach (var renderer in instance.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                if (!renderer.sharedMesh) continue;
                using (var weights = renderer.sharedMesh.GetAllBoneWeights())
                    foreach (var weight in weights)
                        if (weight.weight > 0 && weight.boneIndex >= 0 && weight.boneIndex < renderer.bones.Length && renderer.bones[weight.boneIndex]) weighted.Add(renderer.bones[weight.boneIndex].name);
                using (var counts = renderer.sharedMesh.GetBonesPerVertex())
                    foreach (var count in counts) output.max_skin_influences = Math.Max(output.max_skin_influences, count);
            }
            string[] expected = source.export_weighted_bones ?? source.integrity?.export_weighted_bones;
            if (expected == null) AddIssue(issues, "review", "SKIN_WEIGHT_MANIFEST_MISSING", "No export_weighted_bones manifest: bone Transform retention is verified separately from retained skin influences.");
            else
            {
                output.missing_weighted_bones = expected.Where(n => !weighted.Contains(n)).Distinct().ToArray();
                output.skin_weight_integrity_verified = output.missing_weighted_bones.Length == 0;
                if (!output.skin_weight_integrity_verified) AddIssue(issues, "error", "SKIN_WEIGHT_LOSS", "Weighted bones lost every influence during Unity import: " + string.Join(", ", output.missing_weighted_bones));
            }
            if (output.max_skin_influences > 4) AddIssue(issues, "review", "SKINNING_QUALITY_REVIEW", "Mesh vertices use up to " + output.max_skin_influences + " bone influences. Verify the target runtime's skinning quality and appearance before upload.");
        }

        static void ApplyOptionalParts(ConversionReport source, GameObject instance, List<ConversionIssue> issues)
        {
            foreach (var part in source.optional_parts ?? Array.Empty<OptionalPart>())
            {
                if (part == null || !part.start_hidden || string.IsNullOrEmpty(part.mesh)) continue;
                var matches = instance.GetComponentsInChildren<Renderer>(true).Where(renderer => renderer.gameObject.name == part.mesh).ToArray();
                if (matches.Length != 1)
                {
                    AddIssue(issues, "review", "OPTIONAL_PART_MATCH", "Optional part " + part.mesh + " must match exactly one renderer; found " + matches.Length + ".");
                    continue;
                }
                matches[0].enabled = false;
            }
        }

        // lilToon extrudes outlines in object space before the model matrix. FBX_SCALE_NONE
        // leaves a centimeter unit factor on the armature and on skinned renderers. Dividing
        // only those renderer scales keeps world size (skinning rewrites object-space vertices)
        // and stops the outline from being multiplied. This is safe only for fully weighted
        // leaf renderers with a separate root bone; blendshape-only geometry and child bones
        // would move with the renderer transform, so keep their original scale.
        static void CompensateRendererUnitScale(GameObject instance, List<ConversionIssue> issues)
        {
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            var rendererTransforms = new HashSet<Transform>(renderers.Select(renderer => renderer.transform));
            var boneTransforms = new HashSet<Transform>(renderers.OfType<SkinnedMeshRenderer>().SelectMany(renderer => renderer.bones.Concat(new[] { renderer.rootBone })).Where(bone => bone));
            Transform armature = null;
            int bestChildren = 0;
            foreach (var transform in instance.GetComponentsInChildren<Transform>(true))
            {
                if (rendererTransforms.Contains(transform)) continue;
                Vector3 scale = transform.localScale;
                float axis = Mathf.Abs(scale.x);
                if (axis < 50f || Mathf.Abs(axis - Mathf.Abs(scale.y)) > 0.01f || Mathf.Abs(axis - Mathf.Abs(scale.z)) > 0.01f) continue;
                if (transform.childCount <= bestChildren) continue;
                bestChildren = transform.childCount;
                armature = transform;
            }
            if (!armature) return;
            float unit = Mathf.Abs(armature.localScale.x);
            int changed = 0;
            foreach (var renderer in renderers)
            {
                if (!(renderer is SkinnedMeshRenderer skinned) || !skinned.sharedMesh) continue;
                Transform transform = renderer.transform;
                Vector3 scale = transform.localScale;
                Vector3 next = scale;
                bool any = false;
                if (Mathf.Abs(scale.x) >= unit * 0.5f) { next.x = scale.x / unit; any = true; }
                if (Mathf.Abs(scale.y) >= unit * 0.5f) { next.y = scale.y / unit; any = true; }
                if (Mathf.Abs(scale.z) >= unit * 0.5f) { next.z = scale.z / unit; any = true; }
                if (!any || next == scale) continue;
                bool fullyWeighted;
                using (var counts = skinned.sharedMesh.GetBonesPerVertex())
                    fullyWeighted = counts.Length == skinned.sharedMesh.vertexCount && counts.All(count => count > 0);
                if (!skinned.rootBone || boneTransforms.Contains(transform) || transform.childCount > 0 || skinned.bones.Length == 0 || skinned.bones.Any(bone => !bone) || !fullyWeighted)
                {
                    AddIssue(issues, "review", "RENDERER_UNIT_SCALE_REVIEW", "Preserved " + renderer.name + " renderer scale because it has unweighted geometry, no separate root bone, or child transforms. Changing it could shrink geometry or move bones; review object-space toon outline width manually.");
                    continue;
                }
                transform.localScale = next;
                changed++;
            }
            if (changed > 0)
                AddIssue(issues, "info", "RENDERER_UNIT_SCALE", "Divided " + changed + " skinned renderer scales by the armature unit scale " + unit + " so object-space toon outlines stay in metres. Armature and bone scales are unchanged. A toon shader on the raw FBX still sees the imported mesh scale.");
        }

        static void ApplyShapeDefaults(ConversionReport source, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            if (source.shape_keys == null) return;
            bool complete = true;
            var renderers = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            foreach (var entry in source.shape_keys)
            {
                string[] names = entry.names ?? Array.Empty<string>();
                if (names.Length == 0) continue;
                if (entry.values == null)
                {
                    complete = false;
                    AddIssue(issues, "review", "BLENDSHAPE_DEFAULTS_MISSING", "Authored shape-key values are absent for " + entry.@object + "; regenerate the conversion to restore its fitting/hiding morph state.");
                    continue;
                }
                if (entry.values.Length != names.Length || entry.values.Any(v => float.IsNaN(v) || float.IsInfinity(v)))
                    throw new InvalidDataException("Shape-key names and finite values must have matching lengths for " + entry.@object + ".");
                var matching = renderers.Where(r => r.name == entry.@object && r.sharedMesh).ToArray();
                if (matching.Length != 1) { complete = false; continue; }
                for (int i = 0; i < names.Length; i++)
                {
                    int index = matching[0].sharedMesh.GetBlendShapeIndex(names[i]);
                    if (index < 0) { complete = false; continue; }
                    float weight = entry.values[i] * 100f;
                    if (float.IsNaN(weight) || float.IsInfinity(weight)) throw new InvalidDataException("Shape-key value is outside the supported floating-point range: " + names[i]);
                    matching[0].SetBlendShapeWeight(index, weight);
                    if (Math.Abs(matching[0].GetBlendShapeWeight(index) - weight) > 0.0001f) throw new InvalidDataException("Authored shape-key value did not persist: " + entry.@object + "/" + names[i]);
                    output.blendshape_default_count++;
                }
            }
            output.blendshape_defaults_verified = complete;
        }

        static void VerifyShapeDefaults(ConversionReport source, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            if (source.shape_keys == null) { output.blendshape_defaults_verified = false; return; }
            var renderers = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            int verified = 0;
            bool complete = true;
            foreach (var entry in source.shape_keys)
            {
                string[] names = entry.names ?? Array.Empty<string>();
                if (names.Length == 0) continue;
                if (entry.values == null) { complete = false; continue; }
                var matching = renderers.Where(r => r.name == entry.@object && r.sharedMesh).ToArray();
                if (matching.Length != 1 || entry.values.Length != names.Length) { complete = false; continue; }
                for (int i = 0; i < names.Length; i++)
                {
                    int index = matching[0].sharedMesh.GetBlendShapeIndex(names[i]);
                    if (index < 0 || Math.Abs(matching[0].GetBlendShapeWeight(index) - entry.values[i] * 100f) > 0.0001f)
                    {
                        complete = false;
                        AddIssue(issues, "error", "BLENDSHAPE_DEFAULT_LOSS", "Saved prefab changed authored shape-key state: " + entry.@object + "/" + names[i]);
                    }
                    else verified++;
                }
            }
            output.blendshape_default_count = verified;
            output.blendshape_defaults_verified = complete;
        }

        static void BuildMaterials(ConversionReport source, string input, string destination, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            bool mobile = IsMobile(source.preset);
            Shader shader = Shader.Find(mobile ? "VRChat/Mobile/Toon Lit" : "Standard");
            if (!shader) { shader = Shader.Find("Standard"); AddIssue(issues, "review", "MOBILE_SHADER_MISSING", "Install VRChat SDK and choose a supported mobile shader before building for mobile."); }
            if (!shader) throw new InvalidOperationException("Standard shader unavailable. AvatarForge expects the VRChat Built-in Render Pipeline template.");
            var materials = new Dictionary<string, Material>(StringComparer.Ordinal);
            var references = new HashSet<string>();
            Directory.CreateDirectory(destination + "/Materials");
            foreach (var entry in source.materials ?? Array.Empty<MaterialManifest>())
            {
                if (entry == null || string.IsNullOrWhiteSpace(entry.name) || materials.ContainsKey(entry.name))
                { AddIssue(issues, "review", "MATERIAL_NAME_AMBIGUOUS", "Missing or repeated material name in conversion manifest."); continue; }
                var material = new Material(shader) { name = entry.name };
                if (entry.base_color != null && entry.base_color.Length >= 3)
                    // Color.gamma converts RGB while preserving raw opacity.
                    material.color = new Color(entry.base_color[0], entry.base_color[1], entry.base_color[2], entry.base_color.Length > 3 ? entry.base_color[3] : 1).gamma;
                if (material.HasProperty("_Metallic")) material.SetFloat("_Metallic", Mathf.Clamp01(entry.metallic));
                if (material.HasProperty("_Glossiness")) material.SetFloat("_Glossiness", 1 - Mathf.Clamp01(entry.roughness));
                Texture2D color = LoadTexture(entry.base_color_texture, false, input, destination, references, issues);
                if (color)
                {
                    material.mainTexture = color;
                    if (entry.base_color_scale?.Length >= 2) material.mainTextureScale = new Vector2(entry.base_color_scale[0], entry.base_color_scale[1]);
                }
                Texture2D normal = LoadTexture(entry.normal_texture, true, input, destination, references, issues);
                if (normal && material.HasProperty("_BumpMap"))
                {
                    material.SetTexture("_BumpMap", normal); material.EnableKeyword("_NORMALMAP");
                    if (entry.normal_scale?.Length >= 2) material.SetTextureScale("_BumpMap", new Vector2(entry.normal_scale[0], entry.normal_scale[1]));
                }
                Texture2D metallic = LoadTexture(entry.metallic_smoothness_texture, false, input, destination, references, issues, true);
                if (metallic && material.HasProperty("_MetallicGlossMap"))
                {
                    material.SetTexture("_MetallicGlossMap", metallic); material.EnableKeyword("_METALLICGLOSSMAP");
                    if (entry.metallic_smoothness_scale?.Length >= 2) material.SetTextureScale("_MetallicGlossMap", new Vector2(entry.metallic_smoothness_scale[0], entry.metallic_smoothness_scale[1]));
                    if (material.HasProperty("_GlossMapScale")) material.SetFloat("_GlossMapScale", 1);
                }
                Texture2D emission = LoadTexture(entry.emission_texture, false, input, destination, references, issues);
                Color emissionColor = emission ? Color.white : Color.black;
                if (entry.emission_color != null)
                {
                    if (entry.emission_color.Length < 3 || entry.emission_color.Any(v => float.IsNaN(v) || float.IsInfinity(v)))
                        throw new InvalidDataException("Emission color must contain at least three finite values: " + entry.name);
                    emissionColor = new Color(entry.emission_color[0], entry.emission_color[1], entry.emission_color[2], 1);
                }
                if (float.IsNaN(entry.emission_strength) || float.IsInfinity(entry.emission_strength) || entry.emission_strength < 0)
                    throw new InvalidDataException("Emission strength must be a finite nonnegative value: " + entry.name);
                emissionColor *= entry.emission_strength;
                if ((emission || emissionColor.maxColorComponent > 0) && material.HasProperty("_EmissionColor"))
                {
                    if (emission && material.HasProperty("_EmissionMap")) material.SetTexture("_EmissionMap", emission);
                    // Shader Color properties use gamma-space material values. The
                    // Blender manifest stores scene-linear RGB, including HDR strength.
                    material.SetColor("_EmissionColor", emissionColor.gamma);
                    if (emission && material.HasProperty("_EmissionMap") && entry.emission_scale?.Length >= 2) material.SetTextureScale("_EmissionMap", new Vector2(entry.emission_scale[0], entry.emission_scale[1]));
                    material.EnableKeyword("_EMISSION"); material.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
                }
                else if (mobile && (emission || emissionColor.maxColorComponent > 0))
                    AddIssue(issues, "review", "MOBILE_EMISSION", "Mobile material " + entry.name + " has emission that the selected supported shader cannot represent; review its appearance.");
                if (!mobile)
                {
                    // Built-in Standard samples its main channels with _MainTex_ST, even
                    // though Material stores individual texture-scale properties.
                    foreach (var channel in new[] { ("normal", normal, entry.normal_scale), ("metallic/smoothness", metallic, entry.metallic_smoothness_scale), ("emission", emission, entry.emission_scale) })
                    {
                        if (!channel.Item2 || channel.Item3 == null) continue;
                        if (channel.Item3.Length != 2 || channel.Item3.Any(v => float.IsNaN(v) || float.IsInfinity(v)))
                            throw new InvalidDataException("Texture UV scale must contain two finite values: " + entry.name + "/" + channel.Item1);
                        if ((material.mainTextureScale - new Vector2(channel.Item3[0], channel.Item3[1])).sqrMagnitude > 0.00000001f)
                            AddIssue(issues, "review", "SHARED_UV_SCALE_REVIEW", "Material " + entry.name + " has a " + channel.Item1 + " UV scale different from its albedo. Unity Standard uses one main UV transform; rebake the channels into a matching atlas or choose a shader supporting separate transforms.");
                    }
                }
                if (!mobile) SetAlpha(material, entry);
                else if (!string.IsNullOrEmpty(entry.alpha_mode) && entry.alpha_mode != "OPAQUE")
                    AddIssue(issues, "review", "MOBILE_TRANSPARENCY", "Mobile material " + entry.name + " uses alpha; verify an appropriate supported shader.");
                string path = AssetDatabase.GenerateUniqueAssetPath(destination + "/Materials/" + SafeName(entry.name) + ".mat");
                AssetDatabase.CreateAsset(material, path);
                materials.Add(entry.name, material);
            }
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            foreach (var renderer in renderers)
            {
                var entries = (source.objects ?? Array.Empty<ObjectManifest>()).Where(x => x.name == renderer.gameObject.name).ToArray();
                if (entries.Length != 1 || entries[0].materials == null)
                { AddIssue(issues, "review", "RENDERER_MATERIAL_MAPPING", "No unique material-slot mapping for mesh " + renderer.gameObject.name); continue; }
                Material[] slots = entries[0].materials.Select(name => materials.TryGetValue(name ?? "", out Material value) ? value : null).ToArray();
                if (slots.Any(m => !m)) AddIssue(issues, "review", "MATERIAL_MISSING", "Unresolved material on mesh " + renderer.gameObject.name);
                renderer.sharedMaterials = slots;
            }
            output.material_texture_references = references.OrderBy(x => x).ToArray();
        }

        static void ConfigureBlendShapeNormals(ModelImporter importer)
        {
            // Blender exports authored shape normals. Legacy processing can weld
            // vertices and discard tiny shape deltas even when normals are imported.
            importer.importBlendShapeNormals = ModelImporterNormals.Import;
            var legacy = typeof(ModelImporter).GetProperty("legacyComputeAllNormalsFromSmoothingGroupsWhenMeshHasBlendShapes", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (legacy != null && legacy.CanWrite) legacy.SetValue(importer, false);
        }

        static Texture2D LoadTexture(string relative, bool normal, string input, string destination, HashSet<string> references, List<ConversionIssue> issues, bool linear = false)
        {
            if (string.IsNullOrEmpty(relative)) return null;
            ResolveContainedFile(input, relative);
            string path = destination + "/" + relative.Replace('\\', '/');
            references.Add(path);
            var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            if (!texture) { AddIssue(issues, "review", "TEXTURE_UNSUPPORTED", "Unity could not import texture " + relative); return null; }
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            bool changed = false;
            if (normal && importer.textureType != TextureImporterType.NormalMap) { importer.textureType = TextureImporterType.NormalMap; changed = true; }
            if ((normal || linear) && importer.sRGBTexture) { importer.sRGBTexture = false; changed = true; }
            if (importer.mipmapEnabled && !importer.streamingMipmaps)
            {
                importer.streamingMipmaps = true;
                importer.streamingMipmapsPriority = 0;
                changed = true;
            }
            if (changed) importer.SaveAndReimport();
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }

        static void SetAlpha(Material material, MaterialManifest entry)
        {
            if (entry.alpha_mode == "MASK")
            {
                material.SetFloat("_Mode", 1); material.SetFloat("_Cutoff", Mathf.Clamp01(entry.alpha_cutoff));
                material.SetOverrideTag("RenderType", "TransparentCutout"); material.EnableKeyword("_ALPHATEST_ON");
                material.renderQueue = (int)RenderQueue.AlphaTest;
            }
            else if (entry.alpha_mode == "BLEND")
            {
                material.SetFloat("_Mode", 2); material.SetOverrideTag("RenderType", "Transparent");
                material.SetInt("_SrcBlend", (int)BlendMode.SrcAlpha); material.SetInt("_DstBlend", (int)BlendMode.OneMinusSrcAlpha);
                material.SetInt("_ZWrite", 0); material.EnableKeyword("_ALPHABLEND_ON"); material.renderQueue = (int)RenderQueue.Transparent;
            }
        }

        static void SetupSdk(ConversionReport source, PhysicsApproval approvals, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            Type descriptorType = FindType(DescriptorName), physicsType = FindType(PhysBoneName);
            output.sdk_available = descriptorType != null && physicsType != null;
            if (descriptorType != null && typeof(Component).IsAssignableFrom(descriptorType))
            {
                Component descriptor = instance.GetComponent(descriptorType);
                if (!descriptor) descriptor = instance.AddComponent(descriptorType);
                output.descriptor_added = true;
                Type pipelineType = FindType("VRC.Core.PipelineManager");
                if (pipelineType != null && typeof(Component).IsAssignableFrom(pipelineType))
                {
                    if (!instance.GetComponent(pipelineType)) instance.AddComponent(pipelineType);
                    output.pipeline_manager_added = true;
                }
                else AddIssue(issues, "error", "SDK_PIPELINE_MISSING", "The official SDK PipelineManager is unavailable; resolve the VRChat Base package before building.");
                var animator = instance.GetComponent<Animator>();
                Transform head = animator && animator.avatar && animator.avatar.isValid && animator.avatar.isHuman ? animator.GetBoneTransform(HumanBodyBones.Head) : null;
                if (!head) AddIssue(issues, "review", "GENERIC_VIEWPOINT_REVIEW", "Set the Avatar Descriptor viewpoint for this non-Humanoid model before SDK testing.");
                if (head) SetField(descriptor, "ViewPosition", instance.transform.InverseTransformPoint(head.position) + new Vector3(0, 0, 0.06f));
                ConfigureVisemes(instance, descriptor, issues);
            }
            else AddIssue(issues, "review", "VRCHAT_SDK_MISSING", "Install the official VRChat Avatars SDK with Creator Companion, then reimport to create the Avatar Descriptor and PhysBones.");
            output.physbones = ConfigurePhysics(source, approvals, instance, physicsType, issues).ToArray();
        }

        static void ConfigureVisemes(GameObject instance, Component descriptor, List<ConversionIssue> issues)
        {
            string[] shapes = Visemes.Select(x => "vrc.v_" + x).ToArray();
            var candidates = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).Where(r => r.sharedMesh && shapes.All(s => r.sharedMesh.GetBlendShapeIndex(s) >= 0)).ToArray();
            if (candidates.Length != 1) { AddIssue(issues, "review", "VISEMES_REVIEW", "Automatic lip sync needs all 15 vrc.v_* blendshapes on one mesh. Facial shape keys are preserved for manual mapping."); return; }
            bool ok = SetField(descriptor, "VisemeSkinnedMesh", candidates[0]) && SetField(descriptor, "VisemeBlendShapes", shapes) && SetEnum(descriptor, "lipSync", "VisemeBlendShape");
            if (!ok) AddIssue(issues, "review", "SDK_VISEME_API", "SDK public viseme fields differ; configure lip sync in the Avatar Descriptor.");
        }

        static List<PhysicsResult> ConfigurePhysics(ConversionReport source, PhysicsApproval approvals, GameObject instance, Type type, List<ConversionIssue> issues)
        {
            var results = new List<PhysicsResult>();
            var human = new HashSet<string>((source.humanoid ?? Array.Empty<Mapping>()).Select(x => x.boneName));
            var approved = new HashSet<string>(approvals?.approved_physics ?? Array.Empty<string>());
            var claimed = new HashSet<Transform>();
            var candidates = (source.physics ?? Array.Empty<PhysicsSuggestion>()).OrderBy(x => (x.path ?? "").Count(c => c == '/'));
            foreach (var suggestion in candidates)
            {
                var result = new PhysicsResult { bone = suggestion.bone, category = suggestion.category, status = "suggested" };
                results.Add(result);
                var matching = instance.GetComponentsInChildren<Transform>(true).Where(t => t.name == suggestion.bone).ToArray();
                if (matching.Length != 1) { result.reason = "Bone name is missing or ambiguous."; continue; }
                Transform root = matching[0];
                Transform[] chain = root.GetComponentsInChildren<Transform>(true);
                if (chain.Any(t => human.Contains(t.name))) { result.reason = "Branch includes a Humanoid bone; physics is not applied."; continue; }
                if (chain.Any(claimed.Contains)) { result.reason = "Already covered by a parent physics branch."; continue; }
                if (approvals != null ? !approved.Contains(suggestion.bone) : suggestion.confidence < 0.9f && !suggestion.approved) { result.reason = "Review and approve this inferred secondary branch in AvatarForge/Review physics suggestions."; continue; }
                if (type == null || !typeof(Component).IsAssignableFrom(type)) { result.reason = "VRChat PhysBone SDK unavailable."; continue; }
                bool needsEndpoint = root.childCount == 0 || !chain.Any(t => t != root && t.childCount > 0);
                if (needsEndpoint && (!(suggestion.endpoint_length > 0) || float.IsInfinity(suggestion.endpoint_length)))
                { result.reason = "A verified endpoint length is required for this short branch."; continue; }
                Component component = root.GetComponent(type);
                if (!component) component = root.gameObject.AddComponent(type);
                if (!SetField(component, "rootTransform", root)) { UnityEngine.Object.DestroyImmediate(component); result.reason = "SDK rootTransform public API changed."; continue; }
                SetField(component, "pull", 0.5f); SetField(component, "spring", 0.2f); SetField(component, "gravity", 0f); SetField(component, "radius", 0f);
                SetEnum(component, "limitType", "Angle"); SetField(component, "maxAngle", 30f);
                if (needsEndpoint)
                {
                    Vector3 direction = root.childCount > 0 ? root.GetChild(0).localPosition.normalized : Vector3.up;
                    if (direction == Vector3.zero) direction = Vector3.up;
                    if (!SetField(component, "endpointPosition", direction * suggestion.endpoint_length))
                    { UnityEngine.Object.DestroyImmediate(component); result.reason = "SDK endpointPosition public API changed."; continue; }
                    AddIssue(issues, "review", "PHYSBONE_ENDPOINT_REVIEW", "Check local endpoint direction on " + suggestion.bone + "; imported bone axes can differ.");
                }
                foreach (var bone in chain) claimed.Add(bone);
                result.status = "applied"; result.reason = "Conservative spring settings; tune motion and clipping in SDK Build & Test.";
                outputPhysicsIssue(issues, suggestion.bone);
            }
            if (results.Any(x => x.status != "applied")) AddIssue(issues, "review", "PHYSBONE_SUGGESTIONS", "Secondary bones are retained. Use AvatarForge/Review physics suggestions to approve inferred branches; rejected Humanoid or overlapping roots remain unchanged.");
            return results;
        }

        static void outputPhysicsIssue(List<ConversionIssue> issues, string bone) => AddIssue(issues, "review", "PHYSBONE_MOTION_REVIEW", "Verify spring motion and clipping for " + bone + ".");

        static void CollectStatistics(ConversionReport source, GameObject instance, UnityReport output, List<ConversionIssue> issues)
        {
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            output.skinned_meshes = renderers.OfType<SkinnedMeshRenderer>().Count();
            output.mesh_renderers = renderers.OfType<MeshRenderer>().Count();
            output.material_slots = renderers.Sum(x => x.sharedMaterials.Length);
            output.bones = (source.export_bones ?? source.integrity?.export_bones ?? Array.Empty<string>()).Length;
            output.triangles = 0;
            output.blendshapes = 0;
            foreach (var renderer in renderers)
            {
                Mesh mesh = renderer is SkinnedMeshRenderer skinned ? skinned.sharedMesh : renderer.GetComponent<MeshFilter>()?.sharedMesh;
                if (mesh) { output.triangles += mesh.triangles.Length / 3; output.blendshapes += mesh.blendShapeCount; }
            }
            Type physicsType = FindType(PhysBoneName);
            if (physicsType != null)
            {
                var components = instance.GetComponentsInChildren(physicsType, true);
                output.physbone_components = components.Length;
                output.physbone_transforms = components.Sum(c => c.GetComponentsInChildren<Transform>(true).Length);
            }
            bool mobile = IsMobile(source.preset);
            if (output.triangles > (mobile ? 15000 : 70000)) AddIssue(issues, "review", "TRIANGLE_BUDGET", "Triangle count exceeds " + (mobile ? "mobile Medium (15,000)" : "PC Good (70,000)") + ".");
            if (output.material_slots > (mobile ? 2 : 8)) AddIssue(issues, "review", "MATERIAL_BUDGET", "Material slots exceed " + (mobile ? "mobile Medium (2)" : "PC Good (8)") + ". Texture atlasing or selected mesh simplification can help.");
            if (mobile && (output.physbone_components > 8 || output.physbone_transforms > 64)) AddIssue(issues, "error", "MOBILE_PHYSBONE_LIMIT", "Mobile hard cap exceeded: 8 PhysBone components / 64 affected transforms. Select fewer branches; bones have been retained.");
            if (mobile && output.bones > 150) AddIssue(issues, "review", "MOBILE_BONE_BUDGET", "More than 150 bones: mobile ranks Very Poor. Bone preservation is enabled; choose a separate mobile rig if needed.");
            CollectTextureStorage(instance, output);
            AddIssue(issues, "info", "PERFORMANCE_SCOPE", "These are measured mesh/rig counts and selected documented budgets, not a complete SDK performance rank or build verdict. PhysBone transform counts conservatively include branch roots.");
        }

        static void CollectTextureStorage(GameObject instance, UnityReport output)
        {
            var textures = instance.GetComponentsInChildren<Renderer>(true).SelectMany(r => r.sharedMaterials).Where(m => m)
                .SelectMany(m => m.GetTexturePropertyNames().Select(m.GetTexture)).Where(t => t).Distinct().ToArray();
            output.referenced_texture_count = textures.Length;
            output.texture_memory_bytes = -1;
            output.texture_storage_estimate_available = false;
            output.texture_memory_note = "Imported texture storage estimate unavailable in this Unity version; this is not a VRAM measurement.";
            try
            {
                MethodInfo measure = FindType("UnityEditor.TextureUtil")?.GetMethod("GetStorageMemorySizeLong", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic, null, new[] { typeof(Texture) }, null);
                if (measure == null) return;
                output.texture_memory_bytes = textures.Sum(t => Math.Max(0L, Convert.ToInt64(measure.Invoke(null, new object[] { t }))));
                output.texture_storage_estimate_available = true;
                output.texture_memory_note = "Distinct referenced textures: Unity imported storage estimate for the active build target. Excludes meshes, render targets and driver/runtime allocations; not measured VRAM or FPS.";
            }
            catch (Exception) { /* Optional editor metric; retained model data is unaffected. */ }
        }

        static int CountTriangles(GameObject instance) => instance.GetComponentsInChildren<Renderer>(true).Sum(renderer =>
        {
            Mesh mesh = renderer is SkinnedMeshRenderer skinned ? skinned.sharedMesh : renderer.GetComponent<MeshFilter>()?.sharedMesh;
            return mesh ? Enumerable.Range(0, mesh.subMeshCount).Sum(i => mesh.GetTopology(i) == MeshTopology.Triangles ? (int)mesh.GetIndexCount(i) / 3 : 0) : 0;
        });

        public static string ResolveContainedFile(string root, string relative)
        {
            if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative)) throw new InvalidDataException("Manifest file path must be relative: " + relative);
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string full = Path.GetFullPath(Path.Combine(fullRoot, relative));
            if (!full.StartsWith(fullRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Manifest file escapes conversion folder: " + relative);
            string current = full;
            while (current != null && current.Length >= fullRoot.Length)
            {
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("Symlink/junction paths are not accepted in conversion assets: " + relative);
                if (string.Equals(current, fullRoot, StringComparison.OrdinalIgnoreCase)) break;
                current = Path.GetDirectoryName(current);
            }
            if (!File.Exists(full)) throw new FileNotFoundException("Conversion asset missing", full);
            return full;
        }

        static void CopyTextures(string input, string destination)
        {
            string textures = Path.Combine(input, "textures");
            if (!Directory.Exists(textures)) return;
            CopyDirectory(textures);
            void CopyDirectory(string directory)
            {
                if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("Symlink texture folders are not accepted.");
                foreach (string source in Directory.GetFiles(directory))
                {
                    if (source.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)) continue;
                    string relative = source.Substring(input.TrimEnd(Path.DirectorySeparatorChar).Length + 1);
                    ResolveContainedFile(input, relative);
                    string target = Path.Combine(destination, relative);
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    File.Copy(source, target, false);
                }
                foreach (string child in Directory.GetDirectories(directory)) CopyDirectory(child);
            }
        }

        internal static string SafeName(string value) { string result = Regex.Replace(value ?? "", "[^A-Za-z0-9_.-]", "_").Trim('.'); return string.IsNullOrEmpty(result) ? "Conversion" : result.Substring(0, Math.Min(result.Length, 80)); }
        static bool IsMobile(string preset) => (preset ?? "").IndexOf("mobile", StringComparison.OrdinalIgnoreCase) >= 0 || (preset ?? "").IndexOf("quest", StringComparison.OrdinalIgnoreCase) >= 0;
        static bool IsError(string severity) => severity == "error" || severity == "blocked" || severity == "fatal";
        static void AddIssue(List<ConversionIssue> issues, string severity, string code, string message) => issues.Add(new ConversionIssue { severity = severity, code = code, message = message });
        static Type FindType(string fullName) => AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType(fullName, false)).FirstOrDefault(t => t != null);
        static bool SetField(Component component, string name, object value)
        {
            FieldInfo field = component.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (field == null || value == null || !field.FieldType.IsInstanceOfType(value)) return false;
            field.SetValue(component, value); return true;
        }
        static bool SetEnum(Component component, string name, string value)
        {
            FieldInfo field = component.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (field == null || !field.FieldType.IsEnum || !Enum.IsDefined(field.FieldType, value)) return false;
            field.SetValue(component, Enum.Parse(field.FieldType, value)); return true;
        }
        static Bounds RendererBounds(GameObject instance)
        {
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            Bounds bounds = renderers.Length > 0 ? renderers[0].bounds : new Bounds(Vector3.up, Vector3.one);
            foreach (var renderer in renderers.Skip(1)) bounds.Encapsulate(renderer.bounds);
            return bounds;
        }

        static void CreatePreviewScene(string destination, UnityReport output, List<ConversionIssue> issues)
        {
            var untitled = Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).Where(s => string.IsNullOrEmpty(s.path)).ToArray();
            // Unity forbids additive scene creation while an untitled scene is open. Preserve actual unsaved work.
            bool emptyInitialScene = SceneManager.sceneCount == 1 && untitled.Length == 1 && !untitled[0].isDirty && untitled[0].GetRootGameObjects().Length == 0;
            if (untitled.Length > 0 && !emptyInitialScene)
            {
                AddIssue(issues, "review", "PREVIEW_SCENE_DEFERRED", "Prefab imported successfully. Save the currently open untitled scene, then reimport to generate a preview scene; existing unsaved work was preserved.");
                return;
            }
            var originalActive = SceneManager.GetActiveScene();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, emptyInitialScene ? NewSceneMode.Single : NewSceneMode.Additive);
            try
            {
                GameObject preview = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(output.prefab), scene);
                var light = new GameObject("Preview Light");
                SceneManager.MoveGameObjectToScene(light, scene);
                var lighting = light.AddComponent<Light>(); lighting.type = LightType.Directional;
                light.transform.rotation = Quaternion.Euler(35, 145, 0);
                var camera = new GameObject("Preview Camera");
                SceneManager.MoveGameObjectToScene(camera, scene);
                camera.AddComponent<Camera>();
                Bounds bounds = RendererBounds(preview);
                camera.transform.position = bounds.center + new Vector3(0, 0, Mathf.Max(bounds.size.x, bounds.size.y, 1) * 1.7f + bounds.extents.z);
                camera.transform.LookAt(bounds.center);
                string path = destination + "/Preview.unity";
                if (!EditorSceneManager.SaveScene(scene, path)) throw new IOException("Unity could not save the avatar preview scene.");
                output.scene = path;
            }
            finally
            {
                if (!emptyInitialScene)
                {
                    EditorSceneManager.CloseScene(scene, true);
                    if (originalActive.IsValid()) SceneManager.SetActiveScene(originalActive);
                }
            }
        }

        [MenuItem("AvatarForge/Review physics suggestions...")]
        public static void ReviewPhysics() => PhysicsReviewWindow.Open(SessionState.GetString(LastInput, ""));

        // A runnable editor check verifies manifest decoding and the file trust boundary without an SDK dependency.
        public static void SelfCheck()
        {
            string root = Path.Combine(Application.temporaryCachePath, "AvatarForgeSelfCheck-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                File.WriteAllText(Path.Combine(root, "report.json"), "{\"schema_version\":1,\"humanoid\":[{\"humanName\":\"Hips\",\"boneName\":\"pelvis\",\"confidence\":1}],\"export_bones\":[\"pelvis\",\"breast_L\"],\"optional_parts\":[{\"mesh\":\"HiddenOption\",\"start_hidden\":true,\"armature\":\"OptionRig\",\"bone\":\"Hips\"}]}");
                var report = LoadReport(root);
                if (report.humanoid.Length != 1 || report.export_bones.Length != 2) throw new Exception("Manifest decoding failed.");
                if (report.optional_parts == null || report.optional_parts.Length != 1 || !report.optional_parts[0].start_hidden || report.optional_parts[0].mesh != "HiddenOption") throw new Exception("Optional part decoding failed.");
                var avatar = new GameObject("model");
                try
                {
                    var armature = new GameObject("Armature");
                    armature.transform.SetParent(avatar.transform, false);
                    armature.transform.localScale = new Vector3(100f, 100f, 100f);
                    for (int index = 0; index < 3; index++)
                    {
                        var bone = new GameObject("bone" + index);
                        bone.transform.SetParent(armature.transform, false);
                    }
                    Transform skinBone = armature.transform.GetChild(0);
                    SkinnedMeshRenderer CheckMesh(string name, Vector3 scale, Transform parent, bool weighted = true)
                    {
                        var part = new GameObject(name);
                        part.transform.SetParent(parent, false);
                        part.transform.localScale = scale;
                        var mesh = new Mesh { name = name };
                        mesh.vertices = new[] { new Vector3(-0.01f, 0, 0), new Vector3(0.01f, 0, 0), new Vector3(0, 0.02f, 0) };
                        mesh.triangles = new[] { 0, 1, 2 };
                        mesh.RecalculateNormals();
                        mesh.RecalculateBounds();
                        mesh.AddBlendShapeFrame("Morph", 100, Enumerable.Repeat(new Vector3(0, 0, 0.004f), 3).ToArray(), new Vector3[3], new Vector3[3]);
                        var skinned = part.AddComponent<SkinnedMeshRenderer>();
                        skinned.sharedMesh = mesh;
                        skinned.localBounds = mesh.bounds;
                        skinned.SetBlendShapeWeight(0, 75);
                        if (weighted)
                        {
                            mesh.bindposes = new[] { skinBone.worldToLocalMatrix * part.transform.localToWorldMatrix };
                            mesh.boneWeights = Enumerable.Repeat(new BoneWeight { boneIndex0 = 0, weight0 = 1 }, 3).ToArray();
                            skinned.bones = new[] { skinBone };
                            skinned.rootBone = skinBone;
                        }
                        return skinned;
                    }
                    Vector3[] WorldVertices(SkinnedMeshRenderer renderer)
                    {
                        var baked = new Mesh();
                        try { renderer.BakeMesh(baked, true); return baked.vertices.Select(renderer.transform.TransformPoint).ToArray(); }
                        finally { UnityEngine.Object.DestroyImmediate(baked); }
                    }
                    var body = CheckMesh("Body", Vector3.one * 100, avatar.transform);
                    var mirror = CheckMesh("Mirror", new Vector3(-100, 100, 100), avatar.transform);
                    var partial = CheckMesh("Partial", new Vector3(100, 1, 100), avatar.transform);
                    var aqua = CheckMesh("Aqua", new Vector3(85.6f, 86, 100), avatar.transform);
                    var small = CheckMesh("Small", Vector3.one, avatar.transform);
                    var child = CheckMesh("ArmatureChild", Vector3.one * 100, armature.transform);
                    var blendOnly = CheckMesh("BlendOnly", Vector3.one * 100, avatar.transform, false);
                    var rootless = CheckMesh("Rootless", Vector3.one * 100, avatar.transform);
                    rootless.rootBone = null;
                    var nested = CheckMesh("NestedBones", Vector3.one * 100, avatar.transform);
                    var nestedBone = new GameObject("NestedBone");
                    nestedBone.transform.SetParent(nested.transform, false);
                    nested.bones = new[] { nestedBone.transform };
                    nested.rootBone = nestedBone.transform;
                    nested.sharedMesh.bindposes = new[] { nestedBone.transform.worldToLocalMatrix * nested.transform.localToWorldMatrix };
                    var staticMesh = new GameObject("Static");
                    staticMesh.transform.SetParent(avatar.transform, false);
                    staticMesh.transform.localScale = Vector3.one * 100;
                    staticMesh.AddComponent<MeshRenderer>();
                    var hidden = new GameObject("HiddenOption");
                    hidden.transform.SetParent(avatar.transform, false);
                    hidden.AddComponent<MeshRenderer>().enabled = true;
                    var issues = new List<ConversionIssue>();
                    ApplyOptionalParts(report, avatar, issues);
                    if (hidden.GetComponent<Renderer>().enabled) throw new Exception("Hidden optional part stayed enabled.");
                    skinBone.localRotation = Quaternion.Euler(0, 0, 20);
                    var checkedMeshes = new[] { body, mirror, partial, aqua, small, child, blendOnly, rootless, nested };
                    var before = checkedMeshes.Select(WorldVertices).ToArray();
                    var beforeBounds = checkedMeshes.Select(renderer => renderer.bounds).ToArray();
                    CompensateRendererUnitScale(avatar, issues);
                    for (int mesh = 0; mesh < checkedMeshes.Length; mesh++)
                    {
                        var renderer = checkedMeshes[mesh];
                        var after = WorldVertices(renderer);
                        for (int vertex = 0; vertex < after.Length; vertex++)
                            if (Vector3.Distance(before[mesh][vertex], after[vertex]) > 0.0001f) throw new Exception(renderer.name + " scaled its skinned/blendshape geometry.");
                        if (Vector3.Distance(beforeBounds[mesh].center, renderer.bounds.center) > 0.0001f || Vector3.Distance(beforeBounds[mesh].size, renderer.bounds.size) > 0.0001f) throw new Exception(renderer.name + " changed its culling bounds.");
                    }
                    if (armature.transform.localScale != Vector3.one * 100) throw new Exception("Armature scale changed.");
                    if (body.transform.localScale != Vector3.one || child.transform.localScale != Vector3.one) throw new Exception("Weighted renderer scale was not compensated.");
                    if (mirror.transform.localScale != new Vector3(-1, 1, 1)) throw new Exception("Mirrored scale lost its sign.");
                    if (partial.transform.localScale != Vector3.one) throw new Exception("Partial unit scale was wrong.");
                    if (!Mathf.Approximately(aqua.transform.localScale.x, 0.856f) || !Mathf.Approximately(aqua.transform.localScale.y, 0.86f) || !Mathf.Approximately(aqua.transform.localScale.z, 1)) throw new Exception("Non-uniform renderer scale was wrong.");
                    if (small.transform.localScale != Vector3.one || staticMesh.transform.localScale != Vector3.one * 100 || blendOnly.transform.localScale != Vector3.one * 100 || rootless.transform.localScale != Vector3.one * 100 || nested.transform.localScale != Vector3.one * 100) throw new Exception("Unsafe or static renderer scale changed.");
                    if (!issues.Any(issue => issue.code == "RENDERER_UNIT_SCALE" && issue.severity == "info") || issues.Count(issue => issue.code == "RENDERER_UNIT_SCALE_REVIEW") != 3) throw new Exception("Renderer scale checks were not reported.");
                    ApplyOptionalParts(new ConversionReport(), avatar, issues);
                    foreach (var renderer in checkedMeshes) UnityEngine.Object.DestroyImmediate(renderer.sharedMesh);

                    var humanNames = HumanTrait.BoneName.Where((name, index) => HumanTrait.RequiredBone(index)).ToHashSet();
                    var humanTransforms = humanNames.ToDictionary(name => name, name => new GameObject(name).transform);
                    foreach (var entry in humanTransforms)
                    {
                        int parent = HumanTrait.GetParentBone(Array.IndexOf(HumanTrait.BoneName, entry.Key));
                        while (parent >= 0 && !humanNames.Contains(HumanTrait.BoneName[parent])) parent = HumanTrait.GetParentBone(parent);
                        entry.Value.SetParent(parent >= 0 ? humanTransforms[HumanTrait.BoneName[parent]] : avatar.transform, false);
                    }
                    var mappedReport = new ConversionReport { humanoid = humanNames.Select(name => new Mapping { humanName = name.Replace(" ", ""), boneName = name, confidence = 1 }).ToArray() };
                    BuildHumanMap(mappedReport, avatar.GetComponentsInChildren<Transform>(true), issues, out string[] missing, out bool hierarchyCompatible);
                    if (missing.Length != 0 || !hierarchyCompatible) throw new Exception("Connected required Humanoid chain was rejected.");
                    string lowerLeg = HumanTrait.BoneName[(int)HumanBodyBones.LeftLowerLeg];
                    humanTransforms[lowerLeg].SetParent(humanTransforms["Hips"], false);
                    BuildHumanMap(mappedReport, avatar.GetComponentsInChildren<Transform>(true), issues, out missing, out hierarchyCompatible);
                    if (missing.Length != 0 || hierarchyCompatible || !issues.Any(issue => issue.code == "HUMANOID_HIERARCHY" && issue.severity == "review")) throw new Exception("Disconnected named Humanoid chain was accepted.");
                }
                finally { UnityEngine.Object.DestroyImmediate(avatar); }
                bool rejected = false;
                try { ResolveContainedFile(root, "../outside.fbx"); } catch (InvalidDataException) { rejected = true; }
                if (!rejected) throw new Exception("Path traversal was accepted.");
                rejected = false;
                try { ResolveContainedFile(root, Path.GetFullPath(Path.Combine(root, "report.json"))); } catch (InvalidDataException) { rejected = true; }
                if (!rejected) throw new Exception("Absolute manifest path was accepted.");
                if (SafeName("../a<>b") != "_a__b") throw new Exception("Asset-name sanitization failed.");
                Debug.Log("AVATARFORGE_SELF_CHECK_PASS");
            }
            finally { Directory.Delete(root, true); }
        }
    }

    public sealed class PhysicsReviewWindow : EditorWindow
    {
        string input;
        ConversionReport report;
        HashSet<string> selected = new HashSet<string>();
        Vector2 scroll;
        public static void Open(string input)
        {
            var window = GetWindow<PhysicsReviewWindow>("AvatarForge Physics");
            window.input = input;
            if (!string.IsNullOrEmpty(input) && File.Exists(Path.Combine(input, "report.json"))) window.Read();
            window.Show();
        }
        void Read()
        {
            report = AvatarForgeImporter.LoadReport(input);
            string path = Path.Combine(input, "unity-overrides.json");
            var approvals = File.Exists(path) ? JsonUtility.FromJson<PhysicsApproval>(File.ReadAllText(path)) : new PhysicsApproval();
            selected = new HashSet<string>(File.Exists(path) ? approvals?.approved_physics ?? Array.Empty<string>() : (report.physics ?? Array.Empty<PhysicsSuggestion>()).Where(p => p.approved || p.confidence >= 0.9f).Select(p => p.bone));
        }
        void OnGUI()
        {
            EditorGUILayout.HelpBox("Choose secondary branches to simulate. Humanoid and overlapping roots are rejected automatically. Original bones remain available. Import creates a new prefab; tune the motion in VRChat SDK Build & Test.", MessageType.Info);
            if (GUILayout.Button("Choose conversion folder..."))
            {
                string folder = EditorUtility.OpenFolderPanel("AvatarForge conversion", input ?? "", "");
                if (folder.Length > 0) { input = folder; try { Read(); } catch (Exception exception) { report = null; Debug.LogException(exception); } }
            }
            EditorGUILayout.LabelField(input ?? "No conversion selected", EditorStyles.wordWrappedLabel);
            if (report == null) return;
            scroll = EditorGUILayout.BeginScrollView(scroll);
            foreach (var suggestion in report.physics ?? Array.Empty<PhysicsSuggestion>())
            {
                bool value = EditorGUILayout.ToggleLeft(suggestion.bone + " (" + suggestion.category + ", confidence " + suggestion.confidence.ToString("P0") + ")", selected.Contains(suggestion.bone));
                if (value) selected.Add(suggestion.bone); else selected.Remove(suggestion.bone);
            }
            EditorGUILayout.EndScrollView();
            if (GUILayout.Button("Save approvals and import reviewed prefab"))
            {
                File.WriteAllText(Path.Combine(input, "unity-overrides.json"), JsonUtility.ToJson(new PhysicsApproval { approved_physics = selected.OrderBy(x => x).ToArray() }, true));
                try { var result = AvatarForgeImporter.Import(input); Selection.activeObject = AssetDatabase.LoadAssetAtPath<GameObject>(result.prefab); }
                catch (Exception exception) { Debug.LogException(exception); }
            }
        }
    }
}

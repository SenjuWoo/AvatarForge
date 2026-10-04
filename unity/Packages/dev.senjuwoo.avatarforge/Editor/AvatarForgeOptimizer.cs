using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AvatarForge.Editor
{
    [Serializable] public sealed class MeshOptimizationResult
    {
        public string mesh, status, reason;
        public int before_triangles, after_triangles;
    }
    [Serializable] public sealed class OptimizationReport
    {
        public int schema_version = 1, target_triangles, before_triangles, after_triangles;
        public string status = "blocked", engine, source_prefab, prefab;
        public bool bone_integrity_verified, blendshape_integrity_verified, weighted_bone_integrity_verified;
        public MeshOptimizationResult[] meshes;
        public string[] issues;
        public bool sdk_build_validated = false, uploaded = false;
    }

    public static class AvatarForgeOptimizer
    {
        const string SimplifierNamespace = "UnityMeshSimplifier.";
        static Type Find(string name) => AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType(SimplifierNamespace + name)).FirstOrDefault(t => t != null);
        public static bool Available => Find("MeshSimplifier") != null;

        [MenuItem("AvatarForge/Optimize selected avatar...")]
        public static void Show() => OptimizationWindow.Open();

        // Managed simplification runs only on eligible meshes in a cloned avatar. Original meshes/prefabs are read-only.
        public static OptimizationReport Optimize(GameObject source, int targetTriangles)
        {
            if (!source) throw new ArgumentException("Select an avatar prefab or its root object.");
            if (targetTriangles < 1 || targetTriangles > 10000000) throw new ArgumentOutOfRangeException(nameof(targetTriangles));
            Type simplifier = Find("MeshSimplifier"), optionsType = Find("SimplificationOptions");
            if (simplifier == null || optionsType == null) throw new InvalidOperationException("Install Unity Mesh Simplifier 3.1.1 using AvatarForge's tool installer, then reopen this window.");
            object options = optionsType.GetField("Default", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
            if (options == null) throw new NotSupportedException("Unity Mesh Simplifier's public default options are unavailable.");
            Set(options, "PreserveBorderEdges", true);
            Set(options, "PreserveUVSeamEdges", true);
            Set(options, "PreserveUVFoldoverEdges", true);
            Set(options, "EnableSmartLink", false);
            var output = new OptimizationReport
            {
                target_triangles = targetTriangles,
                engine = "Unity Mesh Simplifier " + (UnityEditor.PackageManager.PackageInfo.FindForAssembly(simplifier.Assembly)?.version ?? "version unknown"),
                source_prefab = AssetDatabase.GetAssetPath(source)
            };
            var issues = new List<string>();
            var meshReports = new List<MeshOptimizationResult>();
            var candidates = new List<(Renderer renderer, Mesh original, MeshOptimizationResult report)>();
            if (!AssetDatabase.IsValidFolder("Assets/AvatarForge")) AssetDatabase.CreateFolder("Assets", "AvatarForge");
            if (!AssetDatabase.IsValidFolder("Assets/AvatarForge/Optimized")) AssetDatabase.CreateFolder("Assets/AvatarForge", "Optimized");
            string destination = AssetDatabase.GenerateUniqueAssetPath("Assets/AvatarForge/Optimized/" + AvatarForgeImporter.SafeName(source.name));
            Directory.CreateDirectory(destination + "/Meshes");
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            var scene = EditorSceneManager.NewPreviewScene();
            GameObject clone = UnityEngine.Object.Instantiate(source);
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(clone, scene);
            clone.name = source.name;
            try
            {
                string[] sourceBones = HierarchyPaths(source);
                var renderers = clone.GetComponentsInChildren<Renderer>(true);
                output.before_triangles = renderers.Sum(r => TriangleCount(GetMesh(r)));
                double ratio = Math.Min(1.0, (double)targetTriangles / Math.Max(output.before_triangles, 1));
                int index = 0;
                foreach (Renderer renderer in renderers)
                {
                    Mesh original = GetMesh(renderer);
                    if (!original) continue;
                    var result = new MeshOptimizationResult { mesh = renderer.name, before_triangles = TriangleCount(original), after_triangles = TriangleCount(original), status = "unchanged" };
                    meshReports.Add(result);
                    int target = Math.Max(4, (int)Math.Floor(result.before_triangles * ratio));
                    if (target >= result.before_triangles) { result.reason = "Already within the selected triangle target."; continue; }
                    if (!original.isReadable) { result.reason = "Mesh is not readable; original retained. Enable Read/Write on a separate imported copy."; continue; }
                    if (MaximumPositiveInfluences(original) > 4)
                    {
                        result.status = "retained_original";
                        result.reason = "This mesh has more than four positive bone influences per vertex. The managed simplifier supports four; original geometry and all influences retained.";
                        issues.Add(renderer.name + ": " + result.reason);
                        continue;
                    }
                    var reduced = new Mesh { name = original.name + "_AvatarForge" };
                    try
                    {
                        Simplify(simplifier, options, original, target, reduced);
                        CheckMeshIntegrity(original, reduced);
                        string asset = destination + "/Meshes/" + index++ + "_" + AvatarForgeImporter.SafeName(renderer.name) + ".asset";
                        AssetDatabase.CreateAsset(reduced, asset);
                        if (renderer is SkinnedMeshRenderer skinned) skinned.sharedMesh = reduced;
                        else renderer.GetComponent<MeshFilter>().sharedMesh = reduced;
                        result.after_triangles = TriangleCount(reduced);
                        result.status = result.after_triangles < result.before_triangles ? "optimized" : "unchanged";
                        result.reason = result.status == "optimized" ? "Blend-shape frames, bind poses and every positively weighted bone index retained; border edges protected, smart linking disabled." : "The simplifier retained this geometry under the selected preservation constraints.";
                        if (result.status == "optimized") candidates.Add((renderer, original, result));
                    }
                    catch (Exception exception)
                    {
                        if (reduced && !AssetDatabase.Contains(reduced)) UnityEngine.Object.DestroyImmediate(reduced);
                        result.status = "retained_original";
                        result.reason = (exception is TargetInvocationException invocation ? invocation.InnerException?.Message : exception.Message) ?? "Mesh reduction failed.";
                        issues.Add(renderer.name + ": " + result.reason);
                    }
                }
                int currentTotal = renderers.Sum(r => TriangleCount(GetMesh(r)));
                int reducibleTotal = candidates.Sum(c => TriangleCount(GetMesh(c.renderer)));
                int allowed = targetTriangles - (currentTotal - reducibleTotal);
                if (currentTotal > targetTriangles && allowed >= candidates.Count * 4 && reducibleTotal > 0)
                {
                    // One further pass redistributes the budget only across previously verified reductions.
                    double remainingRatio = Math.Min(1.0, (double)allowed / reducibleTotal);
                    foreach (var candidate in candidates)
                    {
                        Mesh current = GetMesh(candidate.renderer);
                        int target = Math.Max(4, (int)Math.Floor(TriangleCount(current) * remainingRatio));
                        var reduced = new Mesh { name = current.name };
                        try
                        {
                            Simplify(simplifier, options, candidate.original, target, reduced);
                            CheckMeshIntegrity(candidate.original, reduced);
                            if (TriangleCount(reduced) >= TriangleCount(current)) continue;
                            string ownedPath = AssetDatabase.GetAssetPath(current);
                            if (!ownedPath.StartsWith(destination + "/Meshes/", StringComparison.Ordinal)) throw new InvalidDataException("Refusing to overwrite a mesh outside this new optimization folder.");
                            Mesh backup = UnityEngine.Object.Instantiate(current);
                            try
                            {
                                EditorUtility.CopySerialized(reduced, current);
                                CheckMeshIntegrity(candidate.original, current);
                            }
                            catch
                            {
                                EditorUtility.CopySerialized(backup, current);
                                CheckMeshIntegrity(candidate.original, current);
                                throw;
                            }
                            finally { UnityEngine.Object.DestroyImmediate(backup); }
                            EditorUtility.SetDirty(current);
                            candidate.report.after_triangles = TriangleCount(current);
                            candidate.report.reason += " Triangle budget redistributed once after retaining protected meshes.";
                        }
                        catch (Exception exception)
                        {
                            string message = (exception is TargetInvocationException invocation ? invocation.InnerException?.Message : exception.Message) ?? "Further reduction failed.";
                            issues.Add(candidate.renderer.name + ": further budget reduction retained the previous verified mesh: " + message);
                        }
                        finally { UnityEngine.Object.DestroyImmediate(reduced); }
                    }
                }
                foreach (var candidate in candidates) CheckMeshIntegrity(candidate.original, GetMesh(candidate.renderer));
                output.after_triangles = renderers.Sum(r => TriangleCount(GetMesh(r)));
                output.bone_integrity_verified = sourceBones.SequenceEqual(HierarchyPaths(clone));
                if (!output.bone_integrity_verified) throw new InvalidDataException("Avatar hierarchy changed during optimization; no optimized prefab was saved.");
                output.blendshape_integrity_verified = true;
                output.weighted_bone_integrity_verified = true;
                output.prefab = destination + "/Avatar.prefab";
                PrefabUtility.SaveAsPrefabAsset(clone, output.prefab);
                AssetDatabase.SaveAssets();
                output.status = "needs_review";
                if (output.after_triangles > targetTriangles) issues.Add("The requested triangle target was not reached with all preservation constraints. Original protected meshes were retained.");
                issues.Add("Check the optimized clone's face expressions, skin deformation and physics visually; then run VRChat SDK Builder validation and Build & Test. Geometry metrics do not certify appearance or an SDK performance rank.");
            }
            catch (Exception exception) { output.status = "blocked"; issues.Add(exception.Message); throw; }
            finally
            {
                EditorSceneManager.ClosePreviewScene(scene);
                output.meshes = meshReports.ToArray();
                output.issues = issues.ToArray();
                File.WriteAllText(destination + "/optimization-report.json", JsonUtility.ToJson(output, true));
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
            return output;
        }

        static void Set(object target, string name, object value)
        {
            FieldInfo field = target.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (field == null || !field.FieldType.IsInstanceOfType(value)) throw new NotSupportedException("Unity Mesh Simplifier public field differs: " + name);
            field.SetValue(target, value);
        }
        static void Simplify(Type type, object options, Mesh source, int targetTriangles, Mesh destination)
        {
            MethodInfo initialize = type.GetMethod("Initialize", new[] { typeof(Mesh) });
            MethodInfo simplify = type.GetMethod("SimplifyMesh", new[] { typeof(float) });
            MethodInfo toMesh = type.GetMethod("ToMesh", Type.EmptyTypes);
            PropertyInfo property = type.GetProperty("SimplificationOptions");
            if (initialize == null || simplify == null || toMesh == null || property == null)
                throw new NotSupportedException("Installed Unity Mesh Simplifier API differs from the verified 3.1.1 API.");
            object instance = Activator.CreateInstance(type);
            property.SetValue(instance, options);
            initialize.Invoke(instance, new object[] { source });
            simplify.Invoke(instance, new object[] { (float)targetTriangles / Math.Max(TriangleCount(source), 1) });
            Mesh generated = (Mesh)toMesh.Invoke(instance, null);
            if (!generated) throw new InvalidDataException("The simplifier returned no mesh.");
            try { EditorUtility.CopySerialized(generated, destination); }
            finally { UnityEngine.Object.DestroyImmediate(generated); }
        }
        static int MaximumPositiveInfluences(Mesh mesh)
        {
            int maximum = 0, index = 0;
            using (var counts = mesh.GetBonesPerVertex())
            using (var weights = mesh.GetAllBoneWeights())
                foreach (byte count in counts)
                {
                    int positive = 0;
                    for (int i = 0; i < count; i++) if (weights[index++].weight > 0) positive++;
                    maximum = Math.Max(maximum, positive);
                }
            return maximum;
        }
        static Mesh GetMesh(Renderer renderer) => renderer is SkinnedMeshRenderer skinned ? skinned.sharedMesh : renderer.GetComponent<MeshFilter>()?.sharedMesh;
        static int TriangleCount(Mesh mesh) => mesh ? Enumerable.Range(0, mesh.subMeshCount).Sum(i => mesh.GetTopology(i) == MeshTopology.Triangles ? (int)mesh.GetIndexCount(i) / 3 : 0) : 0;
        static string[] HierarchyPaths(GameObject root) => root.GetComponentsInChildren<Transform>(true).Select(t => AnimationUtility.CalculateTransformPath(t, root.transform)).OrderBy(x => x, StringComparer.Ordinal).ToArray();
        static HashSet<int> WeightedBones(Mesh mesh)
        {
            var indices = new HashSet<int>();
            using (var weights = mesh.GetAllBoneWeights())
                foreach (var weight in weights) if (weight.weight > 0) indices.Add(weight.boneIndex);
            return indices;
        }
        static void CheckMeshIntegrity(Mesh before, Mesh after)
        {
            if (after.vertexCount == 0 || TriangleCount(after) == 0) throw new InvalidDataException("Reduction produced an empty mesh.");
            if (!before.bindposes.SequenceEqual(after.bindposes)) throw new InvalidDataException("Bind poses changed during reduction.");
            if (!WeightedBones(before).SetEquals(WeightedBones(after))) throw new InvalidDataException("A positively weighted source bone lost every influence; original retained.");
            if (before.blendShapeCount != after.blendShapeCount) throw new InvalidDataException("Blend-shape count changed during reduction.");
            var beforeDelta = new Vector3[before.vertexCount];
            var afterDelta = new Vector3[after.vertexCount];
            for (int shape = 0; shape < before.blendShapeCount; shape++)
            {
                if (before.GetBlendShapeName(shape) != after.GetBlendShapeName(shape) || before.GetBlendShapeFrameCount(shape) != after.GetBlendShapeFrameCount(shape))
                    throw new InvalidDataException("Blend-shape names or frame count changed during reduction.");
                for (int frame = 0; frame < before.GetBlendShapeFrameCount(shape); frame++)
                {
                    if (before.GetBlendShapeFrameWeight(shape, frame) != after.GetBlendShapeFrameWeight(shape, frame)) throw new InvalidDataException("Blend-shape frame weight changed.");
                    before.GetBlendShapeFrameVertices(shape, frame, beforeDelta, null, null);
                    after.GetBlendShapeFrameVertices(shape, frame, afterDelta, null, null);
                    if (beforeDelta.Any(v => v.sqrMagnitude > 0.000000000001f) && !afterDelta.Any(v => v.sqrMagnitude > 0.000000000001f))
                        throw new InvalidDataException("An animated blend-shape frame became motionless during reduction.");
                }
            }
        }
    }

    public sealed class OptimizationWindow : EditorWindow
    {
        GameObject avatar;
        int preset, triangles = 70000;
        public static void Open() { var window = GetWindow<OptimizationWindow>("AvatarForge Optimize"); window.avatar = Selection.activeGameObject; window.Show(); }
        void OnGUI()
        {
            avatar = (GameObject)EditorGUILayout.ObjectField("Avatar root/prefab", avatar, typeof(GameObject), true);
            int choice = EditorGUILayout.Popup("Preset", preset, new[] { "PC Balanced (70,000)", "Mobile Medium (15,000)", "Custom" });
            if (choice != preset) { preset = choice; if (preset != 2) triangles = preset == 0 ? 70000 : 15000; }
            triangles = EditorGUILayout.IntField("Triangle target", triangles);
            EditorGUILayout.HelpBox("Creates a separate optimized prefab using Unity Mesh Simplifier. Facial blend-shape frames, bone hierarchy and positively weighted bone sets are checked. Meshes with more than four influences per vertex or a failed preservation check retain their original geometry. No bone pruning or source edits.", MessageType.Info);
            if (!AvatarForgeOptimizer.Available) EditorGUILayout.HelpBox("Unity Mesh Simplifier is not installed. Use the AvatarForge tool installer for the optimization package.", MessageType.Warning);
            using (new EditorGUI.DisabledScope(!avatar || triangles < 4 || !AvatarForgeOptimizer.Available))
                if (GUILayout.Button("Optimize a preserved copy"))
                    try
                    {
                        var report = AvatarForgeOptimizer.Optimize(avatar, triangles);
                        Selection.activeObject = AssetDatabase.LoadAssetAtPath<GameObject>(report.prefab);
                        EditorUtility.DisplayDialog("AvatarForge optimized copy", report.before_triangles + " → " + report.after_triangles + " triangles\n" + report.prefab + "\n" + string.Join("\n", report.issues), "OK");
                    }
                    catch (Exception exception) { Debug.LogException(exception); EditorUtility.DisplayDialog("AvatarForge optimization failed", exception.Message, "OK"); }
        }
    }
}

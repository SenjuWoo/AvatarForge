using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AvatarForge.Editor
{
    // Invoke the installed editor's Humanoid Configure implementation, including its own
    // pose predicate. No Unity reference-source algorithm or mesh data is copied.
    internal static class AvatarForgeHumanoidPose
    {
        const BindingFlags Methods = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;

        static Type Setup => AppDomain.CurrentDomain.GetAssemblies()
            .Select(a => a.GetType("UnityEditor.AvatarSetupTool")).FirstOrDefault(t => t != null)
            ?? throw new NotSupportedException("This Unity editor does not expose its Humanoid Configure pose tools. Enforce T-Pose manually and recheck the avatar.");

        static MethodInfo Method(Type setup, string name, params Type[] parameters) =>
            setup.GetMethod(name, Methods, null, parameters, null)
            ?? throw new NotSupportedException("Unity Humanoid Configure API is unavailable: " + name + ".");

        static object Wrappers(Type setup, GameObject instance, HumanBone[] mapping)
        {
            var human = mapping.ToDictionary(b => b.humanName, b => b.boneName, StringComparer.Ordinal);
            var transforms = instance.GetComponentsInChildren<Transform>(true).ToDictionary(t => t, t => true);
            var resolved = new Dictionary<HumanBodyBones, Transform>();
            for (int i = 0; i < HumanTrait.BoneCount; i++)
            {
                string name = HumanTrait.BoneName[i];
                if (!human.TryGetValue(name, out string boneName))
                {
                    if (HumanTrait.RequiredBone(i)) throw new InvalidDataException("Required Humanoid pose bone is unmapped: " + name);
                    continue;
                }
                var matches = transforms.Keys.Where(t => t.name == boneName).ToArray();
                if (matches.Length != 1) throw new InvalidDataException("Humanoid pose bone must resolve exactly once: " + boneName);
                resolved[(HumanBodyBones)i] = matches[0];
            }
            foreach (var pair in new[]
            {
                (HumanBodyBones.LeftUpperArm, HumanBodyBones.LeftLowerArm), (HumanBodyBones.LeftLowerArm, HumanBodyBones.LeftHand),
                (HumanBodyBones.RightUpperArm, HumanBodyBones.RightLowerArm), (HumanBodyBones.RightLowerArm, HumanBodyBones.RightHand),
                (HumanBodyBones.LeftUpperLeg, HumanBodyBones.LeftLowerLeg), (HumanBodyBones.LeftLowerLeg, HumanBodyBones.LeftFoot),
                (HumanBodyBones.RightUpperLeg, HumanBodyBones.RightLowerLeg), (HumanBodyBones.RightLowerLeg, HumanBodyBones.RightFoot)
            })
                if ((resolved[pair.Item2].position - resolved[pair.Item1].position).sqrMagnitude < 0.0000000001f)
                    throw new InvalidDataException("Humanoid pose has a zero-length mapped limb: " + pair.Item1 + "/" + pair.Item2);
            return Method(setup, "GetHumanBones", typeof(Dictionary<string, string>), typeof(Dictionary<Transform, bool>))
                .Invoke(null, new object[] { human, transforms });
        }

        static float Error(Type setup, object wrappers)
        {
            float error = (float)Method(setup, "GetPoseError", wrappers.GetType()).Invoke(null, new[] { wrappers });
            if (float.IsNaN(error) || float.IsInfinity(error)) throw new InvalidDataException("Unity returned an invalid Humanoid pose error.");
            return error;
        }

        public static SkeletonBone[] Calibrate(GameObject model, HumanBone[] mapping, out float before, out float after)
        {
            var scene = EditorSceneManager.NewPreviewScene();
            var instance = UnityEngine.Object.Instantiate(model);
            instance.name = model.name;
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(instance, scene);
            try
            {
                Type setup = Setup;
                object wrappers = Wrappers(setup, instance, mapping);
                before = Error(setup, wrappers);
                Method(setup, "MakePoseValid", wrappers.GetType()).Invoke(null, new[] { wrappers });
                after = Error(setup, wrappers);
                if (after != 0) throw new InvalidDataException("Unity Enforce T-Pose left a pose alignment error of " + after + ". Review the Humanoid mapping.");
                return (SkeletonBone[])Method(setup, "GetSkeletonBones", typeof(Transform)).Invoke(null, new object[] { instance.transform });
            }
            finally { EditorSceneManager.ClosePreviewScene(scene); }
        }

        public static float VerifyMetadata(ModelImporter importer, GameObject model)
        {
            var scene = EditorSceneManager.NewPreviewScene();
            var instance = UnityEngine.Object.Instantiate(model);
            instance.name = model.name;
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(instance, scene);
            try
            {
                return ApplyVerifiedPose(importer, instance, model.name);
            }
            finally { EditorSceneManager.ClosePreviewScene(scene); }
        }

        public static float ApplyVerifiedPose(ModelImporter importer, GameObject instance, string modelName)
        {
            string name = instance.name;
            try
            {
                // Unity's serialized skeleton includes the imported model root name.
                instance.name = modelName;
                Type setup = Setup;
                using (var serialized = new SerializedObject(importer))
                    Method(setup, "TransferDescriptionToPose", typeof(SerializedObject), typeof(Transform))
                        .Invoke(null, new object[] { serialized, instance.transform });
                float error = Error(setup, Wrappers(setup, instance, importer.humanDescription.human));
                if (error != 0) throw new InvalidDataException("The prepared avatar instance is not in its calibrated T-Pose: " + error + ".");
                return error;
            }
            finally { instance.name = name; }
        }

        public static float VerifyPose(GameObject instance, HumanBone[] mapping) => Error(Setup, Wrappers(Setup, instance, mapping));
    }
}

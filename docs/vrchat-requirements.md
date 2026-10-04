# VRChat conversion requirements

Source documentation checked 2026-10-04. VRChat currently requires [Unity 2022.3.22f1](https://creators.vrchat.com/sdk/upgrade/current-unity-version/). Create the generated project with the official Avatar SDK through Creator Companion / VPM. Unity 6 support in a third-party MCP bridge does not change VRChat's Unity requirement.

## What preservation means

Keep secondary bones, their hierarchy, skin weights and blend shapes. Preserve breast, butt, hair, tail, ear, clothing and other jiggle chains regardless of their names. FBX carries rig data; it does not turn Source jiggle rules, MMD rigid-body physics or Blender constraints into working VRChat PhysBones. Unity needs a reviewed `VRCPhysBone` configuration for those chains.

According to the [PhysBones documentation](https://creators.vrchat.com/common-components/physbones/), humanoid bones cannot be PhysBone roots. Single-bone chains and roots whose children have no grandchildren require an endpoint position. One component supports at most 256 affected transforms. Limits cost less than collision checks; source spring parameters are not interchangeable with VRChat's solver values. Keep existing physics roots and report uncertain chain selection rather than silently deleting bones.

Unity import settings must retain transforms: `optimizeBones=false`, `optimizeGameObjects=false`, `preserveHierarchy=true`. Enable blend shape import. These fields are documented by [Unity's ModelImporter API](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/ModelImporter.html).

Blender's [FBX exporter source](https://github.com/blender/blender-addons/blob/main/io_scene_fbx/__init__.py) documents that modifier application prevents shape key export and that space-transform baking is experimental with armatures. Export without deform-only bone filtering, manufactured leaf bones or animation baking; preserve shape keys and validate the resulting Unity import. Topology-changing optimization of a face with shape keys requires a workflow that transfers those keys correctly; a lower triangle count alone is not success.

For polygon reduction on meshes with blend shapes, AvatarForge uses the managed [Unity Mesh Simplifier](https://github.com/Whinarn/UnityMeshSimplifier/blob/53fdb3122645bcd3ad2c258235200dff3dfbaa9b/Runtime/MeshSimplifier.cs) and verifies the resulting clone's shape frames, bind poses and weighted bones. Meshes with more than four skin influences per vertex retain their original geometry. Inspect blink/viseme extremes and skin deformation in the preview. [AAO's automatic optimization](https://vpm.anatawa12.com/avatar-optimizer/en/docs/tutorial/basic-usage/) can remove unused blend shapes, merge bones and remove unused PhysBones; explicitly disable or constrain those passes when the chosen recipe promises full retention. Measure the baked NDMF output, since the unprocessed prefab's statistics differ from the built avatar. Meshia remains a manual link after native allocator failures in local validation; it is not the automatic reducer.

## Optimization targets

These are selected maximums from the current [performance rank table](https://creators.vrchat.com/avatars/avatar-performance-ranking-system/) (updated 2026-04-21). A preset is a target, not a guaranteed final rank; bounds and other component counts also contribute.

| Metric | PC Excellent | PC Good | Mobile Medium |
| --- | ---: | ---: | ---: |
| Triangles | 32,000 | 70,000 | 15,000 |
| Texture memory | 40 MB | 75 MB | 25 MB |
| Skinned meshes | 1 | 2 | 2 |
| Material slots | 4 | 8 | 2 |
| Bones | 75 | 150 | 150 |
| PhysBone components | 4 | 8 | 6 |
| PhysBone affected transforms | 16 | 64 | 32 |
| PhysBone colliders | 4 | 8 | 8 |
| PhysBone collision checks | 32 | 128 | 32 |

Mobile hard caps are 8 PhysBone components, 64 affected transforms, 16 colliders and 64 collision checks. Exceeding them removes limited components at runtime. A preservation preset can keep an over-budget skeleton and report the conflict; it cannot claim both complete preservation and compliant mobile dynamics when these caps are exceeded. Disabled objects still count. Keep mesh Read/Write enabled, as VRChat's SDK requires it.

## Readiness gates

1. Import the complete source asset and report missing sidecars, textures, skin weights and unmapped rig bones.
2. Validate bone/weight/blend-shape preservation in Blender and after FBX import.
3. Check Unity's generated Avatar is valid and humanoid, with correct scale and bone mapping. Greatly nonhuman rigs need a deliberate Generic rig and animation controller.
4. Inspect material conversion, transparency, normal maps, eye motion, visemes and the configured PhysBone chains in Unity.
5. Run the SDK's validation and [Build and Test API](https://creators.vrchat.com/sdk/public-sdk-api/), then check actual movement and dynamics in VRChat before calling an avatar ready.

The official [rig requirements page](https://creators.vrchat.com/avatars/rig-requirements/) explicitly says it is out of date. Use live Unity/SDK results instead of treating old upper-chest or finger-mapping warnings as current SDK3 rules. Automated name mapping cannot guarantee an arbitrary source rig's anatomy or rest pose. Upload requires the user's VRChat account, authentication and eligible trust rank; export preparation does not upload anything.

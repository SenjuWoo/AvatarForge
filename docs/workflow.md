# Conversion and Unity preparation

The normal run is local and deterministic. No AI subscription or model call is required.

| Stage | Result | Continue with |
| --- | --- | --- |
| Convert model | model.blend, model.fbx, textures, preview and report | Review checks, then prepare Unity |
| Prepare Unity | SDK project, reconstructed materials, rig settings, Avatar.prefab and Preview.unity | Open Unity project and run SDK checks |
| Existing project | New AvatarForge assets; existing scene work is preserved | Add package from disk, then AvatarForge → Import conversion folder |

Use the prepared prefab or scene. A raw FBX does not contain the material setup, Avatar Descriptor, restored fitting morph values or selected PhysBones. Do not copy model.blend, logs, receipts or Blender profile files into Unity Assets. The import menu accepts the conversion folder and copies the needed assets itself.

## Animation and rigs

The FBX is an avatar bind/rest-pose export, without embedded animation clips. Unity's “no animation data” message is expected. A valid prepared Humanoid Avatar uses VRChat tracking and default playable layers; its Animator Controller can be empty. Original Blender actions, scripted controls and simulation parameters are not translated into animation clips.

All secondary and weighted bones remain available. Select PhysBone roots before preparation; an explicitly empty selection disables suggested roots. Inspect spring motion, collisions and clothing clipping using SDK Build & Test. Facial morphs are preserved, but automatic visemes require the supported named set.

Models outside human anatomy, or with incomplete mappings, receive a Generic preview prefab and scene instead of losing extra limbs or failing with an Animator exception. Generic VRChat avatars require their own controller/animation setup and do not receive normal Humanoid tracking. See the official [rig requirements](https://creators.vrchat.com/avatars/rig-requirements/) and [playable layers](https://creators.vrchat.com/avatars/playable-layers/).

For a human model with unfamiliar names, provide exact source bones under Advanced options `humanoid` or `humanoid_overrides`. Select the intended armature and meshes when a scene contains several characters. Collision or cloth modifiers on a visible body no longer exclude the whole mesh. Omitted subdivision, procedural geometry and other nonportable modifiers remain review items.

A real morph named Basis is renamed AF_Basis_Morph in the conversion copy because Blender's FBX importer reserves Basis. The report records its source/export name; geometry and authored value are retained. The original file is unchanged.

## Verdicts and recovery

- **Conversion complete:** Blender/FBX artifacts were produced and checked. This does not certify Unity preparation or VRChat upload.
- **needs_review:** usable artifacts exist with listed model-specific issues. Inspect materials, skinning and appearance.
- **blocked/failed:** a required stage failed. Detailed review items and logs remain available; do not upload the unverified export.
- **Unity prefab & scene prepared:** the current import verdict references actual saved assets. Open the project to review them.
- **Project kept / timed out:** Unity has no completed asset verdict. Open the kept project, wait for asset imports, then run AvatarForge → Import conversion folder. A new-project retry keeps earlier diagnostic projects intact.

First Unity imports compile the SDK and build an asset cache. Large generated rigs can take substantially longer than simple models; automated preparation has a 30-minute limit. The Unity log identifies the last importing asset. Extra retained bones may also exceed VRChat performance budgets; preservation is not permission to silently discard them.

Texture baking approximates supported Blender shaders. Missing source images, ambiguous UDIM tiles, degenerate UVs and unconnected shader surfaces require repair rather than invented texture data. Inspect the prepared Unity material, not the default material Unity creates from a raw FBX. Original material graphs and source-image backups are kept in the converted Blender file.

If a native Qt/plugin or Unity licensing dialog appears, keep its exact process name and conversion logs. A dialog alone does not identify which executable failed; do not replace system DLLs or global plugin paths. Use the reviewed installer to verify local tool receipts. Normal background conversions isolate Blender preferences and never enable embedded source scripts.

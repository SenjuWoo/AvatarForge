# Changelog

## 0.2.8

- Preserve truly absent SDK environment variables on PowerShell 7.5+ as well as Windows PowerShell 5.1; installer regression launches the actual host executable.

- Keep Grok's broader rig naming, visible-surface baking, source-resolution presets and follower-rig support, with native regression repairs.
- Shield every other material slot during custom-surface baking so source images, saved source graphs and earlier baked maps are not overwritten. Custom transparent/holdout surfaces retain their source graph and receive an explicit review item instead of being flattened into an opaque material.
- Preserve follower joint orientation, required attachment targets and skin weights through bone/group name collisions. Missing attachment targets stop conversion instead of silently exporting detached geometry.
- Validate actual imported Humanoid ancestry before calibration. Complete names with disconnected limb chains retain a Generic prefab and preview scene with a repair report, including Mimi-style mechanical hip rigs.
- Apply toon-outline unit compensation only to fully weighted leaf skinned renderers with separate valid bones. Bone-less blendshape meshes and renderer-parented bones retain their geometry and report the outline review needed.
- Restore canonical, case-insensitive Windows path containment across junctions and stream ZIP members instead of allocating a complete member in memory. Preserve pasted/AI-selected file paths and network shares for the authenticated desktop owner.
- Describe the loopback session authentication boundary in CodeQL without disabling security queries or trusting downloaded archive/model contents. Add real request, native FBX, posed skin/morph geometry and saved Generic import regressions.

## 0.2.7

- Rank the weighted joint above a deform bone whose name says armor, outfit, or weapon. A preferred DEF- armor thigh was winning the upper-leg slot and skipping generated-hierarchy repair, so Unity rejected the Humanoid T-pose.

## 0.2.6

- Convert integer and other non-float 3-item custom properties to plain floats before FBX export. DAZ rotation and location lock arrays use that shape and were aborting Blender 5.2. A vector that still cannot be encoded is omitted and the export continues.
- Export meshes marked unselectable in the source. A selection-based FBX export was dropping those meshes, including DAZ eye meshes.

## 0.2.5

- Map humanoid joints from XPS/XNALara word order, ValveBiped and Maya `lf`/`rt` tokens, sided hip bones, and Blender `.001` duplicates. Twist, armor and finger helpers no longer tie with the real joint.
- A shared material bakes from the meshes that have UV area. An unsupported slot no longer drops the slots on that mesh that can bake.
- If the VRChat spine parent repair cannot keep bind positions, keep the original parents and continue the export instead of blocking it.

## 0.2.4

- Keep meshes skinned to armatures that are Child Of the chosen character, including parts hidden in the source view layer. Their shape keys stay on those meshes. Hidden parts are disabled on the Unity prefab. Follower bones move onto the chosen rig under the target bone, because a second armature parented to a bone does not survive FBX import. Rig widgets named WGT- stay out.
- Bake complex materials on a no-limit preserve conversion at the source image resolution, capped at 4096, and warn when the source is larger. An explicit bake size and the balanced and mobile limits are unchanged.
- On the prepared prefab, divide skinned renderer scales by the armature's unit scale so object-space toon outlines are not multiplied. Armature and bone scales stay. The raw FBX scale is unchanged.

## 0.2.3

- Bake the visible surface of materials that are not a single Principled shader. Emission shaders keep their color and glow. Glossy, diffuse and glass shaders bake their color input. Mixed and grouped shaders get a lit appearance bake instead of a blank Unity material. A flat white coverage mask is not used as color. The source graph stays in the blend backup.

## 0.2.2

- Parent the neck and both shoulders directly to the mapped UpperChest, or to Chest when UpperChest is absent, so VRChat's spine hierarchy check can pass. Bind positions stay put. Intermediate spine bones and secondary chains stay in the skeleton.
- Report that repair, and warn when a cycle still leaves the neck or a shoulder off that direct parent.

## 0.2.1

- Use Unity-compatible FBX unit metadata and object scales to avoid native import loops on large generated rigs.
- Frame wide and deep models fully in the generated Unity preview camera.
- Prevent native Unity import stalls on large multi-mesh rigs by removing empty FBX skin clusters; every bone, weighted bind matrix and bind pose remains intact.

- Retain visible bodies with Collision/Cloth modifiers and explicitly included meshes in hidden collections; verify exported mesh vertex and triangle counts.
- Preserve real morphs named Basis using a recorded portable name; restore video render settings after PNG previews and save auto-packed sources with portable output texture paths.
- Keep shared source mesh instances independent when applying authored deletion masks; flag omitted subdivision and unconnected shader surfaces for review.
- Recognize Source bip_ names and numbered game deformer skeletons; retain Generic prefab/scene previews for incomplete or nonstandard Humanoid rigs.
- Respect explicit PhysBone deselection and guard missing Animators after SDK component setup.
- Keep malformed historical receipts from breaking startup; persist current job state and display structured failed-conversion review items.
- Require saved Unity prefab and preview scene files before reporting completion; expose retry and clear timeout recovery with a 30-minute import limit.
- Repair moved and damaged portable tools using the same owned-location check as verification, preserving previous bytes.
- Explain raw FBX versus prepared assets, clip-free avatar exports, Generic rig limits and existing-project imports in the app and workflow guide.

## 0.2.0

- Native file/folder pickers now close with their app parent, including abrupt exits; they cannot leave orphaned dialogs locking a previous app folder.

- Add Connect AI in the UI and a one-click BAT, ten reviewed client adapters, generic MCP export, actual stdio discovery, configuration backups and idempotent updates.
- Ship one small usage skill separately from the app; preserve unrelated providers, model choices, trust, filters and settings.
- Isolate native workers in their own conversion directory; make converted Blender texture paths, new job receipts and installed tool receipts survive application moves.
- Test provider registration in clean Unicode homes, malformed/conflicting/locked configuration, repeat registration, moved conversions and moved installed tools.

## 0.1.0

- Initial local UI, deterministic conversion engine, CLI and compact optional MCP interface.
- Native/importer model routes, texture/UDIM processing, shader baking and preservation reports.
- Automatic compatible shader baking from render UVs to export UV0, authored shape-value restoration and mask-aware preservation of shape frames.
- Constant baked-channel elimination with source/pixel proof, retained source material graphs and verified Unity scalar color-space handoff.
- Unity Humanoid/material/prefab/descriptor/secondary-motion handoff and optional safe optimization.
- Saved Humanoid T-pose calibration with an independent pose check and persisted shape-value verification.
- Authored blendshape normal import with legacy processing disabled, plus streaming mipmaps for the VRChat SDK handoff.
- Managed shape-key reduction with influence preflight, verified clones, custom targets and reported unmet budgets.
- Saved physics-root selections, persisted Unity verdicts and texture storage estimates in the local interface.
- Pinned local dependency setup, source-data isolation and runnable Blender/loopback regression checks.
- CPU preview rendering without a GPU context, with temporary scene state restored after capture.
- Installer suppresses .NET certificate/PATH first-use setup and telemetry for its child commands, restoring caller process settings afterward.

# Observed validation

## Portable app and AI registration

- Standard-library suite: 35 tests passed locally, including nine Windows configuration formats in a clean home containing spaces and Unicode, real stdio initialization/tools-list, exact configuration backups, repeated registration, conflict/malformed/locked-file preservation, personally edited skills, project-only scope and moved conversion history.
- Installed Hermes configuration API: tested separately in a disposable home; unrelated model choice, unknown keys and another MCP server survived. A repeat registration made no config change. This does not certify every third-party AI client's active session.
- Native Blender regression passed after copying an actual exported blend and its PNGs into a different Unicode folder, reopening it and reloading the images from the relocated paths.
- Connect AI was clicked through the actual browser UI in a disposable project. Codex, Claude Code, Copilot and VS Code entries completed registration, seven local MCP tools were discovered, and browser error/warning logs were empty. The final controls were rendered and visually inspected.

Earlier model/editor evidence below remains separately scoped; a successful model build is not a claim that every input becomes a perfect avatar.

Validated on Windows x64 on 2026-10-03 and 2026-10-04. These results describe the tested inputs and checks, not every possible avatar or a completed VRChat upload. Private model files, project copies and logs are excluded from Git and distribution archives.

## Runtime and installation

| Component | Observed version / result |
|---|---|
| Blender | 5.2.2 LTS, native background and interactive MCP bridge |
| Unity | 2022.3.22f1, actual compiler, FBX import and isolated editor MCP |
| VRChat Avatar SDK / Base | 3.10.5, installed into a fresh official VPM project and checked from actual package files |
| VPM CLI | 0.1.28 |
| Portable Python | 3.14.8, original official embedded payload |
| Private .NET fallback | SDK 8.0.425 and runtime 8.0.31, downloaded, hashed and executed |
| Unity Mesh Simplifier | Managed 3.1.1, complete pinned source; separate output meshes and influence-count preflight |
| Installer | 0.2.0 fresh archive: 11 official payload receipts and verified external Blender; repeat setup preserved identical payloads |

Windows PowerShell 5.1 works when launched by the portable Python, including an inherited incompatible PowerShell 7 module path. Installer regressions cover altered/missing files, preserved repair backups, unknown occupied folders, archive paths and .NET 6/7 fallback selection. Managed reducer installation verified all 93 payload files and kept 1,542 existing package files unchanged across repeated setup. Normal tool installation preserves AI provider configuration; the explicit Connect AI action merges only its owned server entry.

A fresh extracted install exposed .NET's first-use HTTPS development-certificate announcement. The installer now sets [`DOTNET_GENERATE_ASPNET_CERTIFICATE=false`](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-environment-variables#dotnet_generate_aspnet_certificate), disables automatic global-tool PATH setup and opts out of CLI telemetry for SDK child commands, restoring the caller's process settings afterward. No existing certificates are removed, and the earlier announcement alone does not establish that a new certificate was created.

## Blender checks

The public regression generates its own assets and uses real Blender operations:

- 23 required bones, including unweighted secondary bones and a jiggle bone with a controller-like name; two named shape keys with nonzero deformation.
- Preserve, PC and mobile presets; actual exported FBX reimport checks bones, shape keys and positively weighted bones.
- Generated/control rig mapping and disconnected hierarchy repair, with unchanged world rest matrices.
- A real sparse-weight mesh that loses an influence under native Collapse: the worker restores the original mesh and records the unmet budget.
- Actual UDIM tile colors, atlas UVs and CPU shader-bake pixel values, including metallic/smoothness packing and two-mesh baking.
- Invalid UV bake fallback and deliberately removed bone-influence rejection.
- Authored positive/negative shape values captured before driver removal; active object/material inputs frozen before their source controllers are removed.
- Deletion masks applied across every shape frame, retained skin weights and an unmasked mesh backup checked after reopening the saved Blender file.
- Render UV selection, repeated textures outside the first tile, matching all-channel atlas scales and scalar alpha; bake fallback when all eight UV channels are occupied.
- Redundant channel elimination using source constants and every covered bake pixel, including fractional UV coverage, HDR scalar emission and a deliberately varied final pixel. Varying normal, emission, roughness and alpha maps remain intact.
- Actual Cycles CPU preview capture and temporary camera/light cleanup; no Workbench graphics context is required.

All five installed importer modules register and expose their required operators. Generated MMD PMX, XPS ASCII, Source SMD and VRM models also passed actual importer-to-FBX checks. SMD plus matching/explicit VTA retained two morphs; VRM1 retained two morphs. Both routes verified actual FBX vertex weights and distinct Smile/Blink deformation. Unmatched VTA files produce a selection warning. Binary XPS, Source 2 and DMX have not been tested with representative supplied models.

## Supplied models

| Model | Blender result |
|---|---|
| Shion CloudRig | 58,815 triangles; 1,297 retained bones; 308 weighted bones; 95 shape keys; 43 PNG textures. Zero missing required bones, keys or weighted-bone names. 12 of 13 materials automatically baked at 2K from render UVs into export UV0; 19 redundant channel maps removed. |
| DoomGirl Source MDL | 168,218 to 69,988 triangles; 59 bones; 55 weighted bones; 24 PNG textures. Zero missing required bones or weighted-bone names. The source has no shape keys or named secondary motion chains to recreate. |

Both main source-file SHA256 values remained unchanged. Shion's generated hierarchy repair changed 320 parent links while retaining bind/rest matrices within `4.77e-7`; 47 blended-parent constraints were approximated with a single parent and remain explicit articulation/physics review items.

Rendered source comparison exposed and repaired three concrete conversion faults: resetting driven fitting/hiding shape keys, ignoring the source Geometry Nodes skin mask, and baking the degenerate edit UV instead of the render UV. A fourth dependency was source visibility/material drivers referencing removed controls. The corrected saved Blender file and actual FBX reimport render the complete coat, robotic neck/body and red nails. The source eye-refraction shader retains an explicit transmission/layer review item.

## Unity checks

The complete fresh-project path ran official VPM, verified the actual SDK version, embedded the packages, compiled the final importer and emitted an import verdict. The generated fixture retained 23 bones, two blendshapes and all skin influences, produced a valid Humanoid avatar and persisted a descriptor and PipelineManager. Separate SDK checks persisted three selected PhysBone components.

Actual Unity MCP commands also verified:

- Generated control rig: valid Humanoid, 45 bones, two shape keys and all positive skin influences.
- Safe simplification: 800 to 500 triangles, two named/nonzero shape frames, unchanged bind poses and hierarchy; original mesh remained 800.
- Automatic PC preset: 80,000 to 70,000 triangles, valid Humanoid, 23 bones and two shape keys, with original and optimized prefabs kept separately. A custom target produced 60,000; Preserve retained 80,000 even with a reduction override in the supplied manifest.
- Six-influence fixture: the managed reducer was not called; the original 800-triangle mesh and all six influences remained intact.
- DoomGirl: valid Humanoid, 69,988 triangles, 59 bones and all 55 expected weighted bones remain weighted.
- Shion's corrected visible outfit is already below the 70,000 triangle target at 58,815. All 1,297 bones, 95 shape keys and 308 expected weighted bones remain. The imported meshes expose up to 16 influences per vertex and bypass the four-influence reducer.
- Shion's saved Humanoid importer pose and saved prepared prefab both passed the native Unity pose check with error zero. A `HumanPoseHandler` read/write round trip also retained the calibrated T-pose. All 95 authored shape values survived saved-prefab reload. These checks include rendered front and quarter views of the actual prepared prefab.
- Native rendered comparisons checked scalar material colors against equivalent linear textures. Emission matched within `1.20e-7`; nonwhite base color matched within `1.87e-9`, including unchanged alpha 0.35. Both exposed and corrected the scalar color-space handoff before final validation.

Meshia's native jobs corrupted allocators during complex skinned-model tests, including with Burst disabled. It was removed from automatic installation and invocation. The final managed path passed real editor requests, saved-prefab reloads and preview-scene instance checks without those native failures. Existing optional tool folders were preserved.

Unity's imported storage API measured 18 referenced textures / 76,546,508 bytes for DoomGirl. Shion's corrected 2K shader conversion measured 49 / 235,056,920 bytes before constant-channel elimination and 36 / 179,132,600 bytes afterward, a 23.8% reduction with byte-identical retained maps. The final material refresh preserved the exact imported FBX and original mesh assets. Actual maps use compressed DXT formats, mipmaps and disabled texture Read/Write. These are distinct-texture storage estimates for the active build target, not measured VRAM or FPS.

The actual prepared Shion prefab renders the corrected coat, robotic body/neck and red nails with straight horizontal arms and straight legs. Original material graphs and unmasked mesh backups survive reopening the saved Blender file. Simulated movement, eye/viseme setup and model-specific blended-parent approximations still require preview. The unsupported source eye-refraction shader remains a material review item. Large preserved rigs can exceed performance budgets even when polygon and texture counts improve.

A final saved-scene capture initially showed extra flesh hands, bent legs and clipping. A controlled native comparison reproduced those faults by rendering an older diagnostic avatar scene over the final preview. Isolating the exact same saved preview removed the overlap; all prior scenes and temporary layer changes were restored. The desktop opening action loads the generated preview alone when no unsaved scene work is present.

## MCP and UI

Real MCP `initialize`, tool discovery and editor requests succeeded for Blender and the explicitly selected isolated Unity project. Blender's existing addon uses an older protocol; the read-only scene query worked without changing its preferences. Normal conversion uses the local background pipeline instead of depending on that live bridge.

AvatarForge's own stdio server was checked with the official Python MCP SDK. It exposes seven tools, including asynchronous Unity preparation, and sends compact job/status replies by default. The local browser UI completed real conversions, restored job history and saved/restored selected secondary-motion roots. Loopback authorization, Host/Origin checks and the served UI assets have runnable tests.

The complete MCP workflow converted the generated fixture with authored Smile 0.35 and Blink 0.20, created a fresh official VPM project, compiled the final Unity package and retained 23 bones, two blendshapes and three approved PhysBone roots. Saved Humanoid calibration and both shape defaults passed independent checks. Both compact action status and persisted project links were checked.

The 25 standard-library regressions include failed Unity import recovery and stale-verdict rejection. A deliberately malformed generated shape-default manifest also ran through the actual fresh-project/Unity batch path: Unity emitted a blocked verdict and exited with code 1; AvatarForge retained the new project and exposed its specific error for repair. The browser's blocked-import view and successful generated-fixture view were inspected.

The final Shion prefab passed the installed Avatar SDK 3.10.5 validation with zero SDK errors and completed a native local `Build(GameObject)` into a 14,930,521-byte avatar bundle. This used imported authored blendshape normals, disabled legacy recalculation and streaming mipmaps on all 36 referenced textures. A fresh generated-model import independently checked these defaults, 23 bones, two shape keys, Smile 35/Blink 20 and a saved pose error of zero.

The SDK still rates preserved Shion **VeryPoor** overall: 34 skinned renderers, 35 material slots, 1,297 bones and about 170.8 MiB of texture storage. It also reports forearm/shin child-order warnings and a pelvis/thigh angle of 169.7 degrees. These remain model-specific optimization and tracking review items.

The weighted-bone checks compare expected names with bones still having positive influences after import. A source-FBX comparison of four Shion meshes measured maximum rest-position error of `5.97e-8` metres, UV error of `6.65e-8`, and shape-position error of `9.81e-6` metres (about 0.00981 mm). Unity pruned some authored microdeltas to zero; the original calculated-normal import also pruned microdeltas. Both import modes omitted the same 511 sub-0.001 point/bone influence pairs in the thigh harness, while every expected weighted-bone name remained weighted. The observed minimum bone weight was 0.001. The saved Blender file retains the authored data for precision edits. Unity documents its [minimum-weight culling behavior](https://docs.unity3d.com/2022.3/Documentation/ScriptReference/ModelImporter-minBoneWeight.html).

The normal preparation path leaves its SDK build/upload flags false until those stages are explicitly performed. The observed local SDK build above was a separate validation run. No SDK Build & Test client run, avatar upload, live VRChat dynamics test or Android/iOS build is claimed. Movement and dynamics preview determine final avatar readiness.

## Reproduce

```powershell
.\.runtime\python\python.exe -m unittest discover -s tests -v
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\test_installer.ps1
.\.runtime\python\python.exe tools\check_contract.py
blender.exe --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_smoke.py -- validation/blender-smoke
blender.exe --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_addons.py
blender.exe --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/blender_formats.py
```

Use the actual Blender executable, not a shell alias. GitHub Actions repeats installation, engine/installer checks and real Blender regressions on clean Windows runners. Unity integration requires an installed licensed editor and is verified locally rather than by an unlicensed CI simulation.

A real Windows child-process test terminates the picker owner abruptly and confirms its assigned child exits while an unrelated process stays alive.

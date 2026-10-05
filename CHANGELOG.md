# Changelog

## 0.2.0

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

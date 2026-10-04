# Working on AvatarForge

- Inspect status and the complete model/import/export flow before editing.
- Preserve source models, user Blender profiles and existing Unity projects.
- Keep normal conversions deterministic and local. AI and live-editor MCP are optional repair tools.
- Use argv arrays; never compose shell commands from model names or paths.
- Preserve shape keys, skin influences and secondary bones. New optimization must verify the actual output.
- `needs_review` is a useful verdict, not a failure to conceal. Do not call an export VRChat-ready without target-editor/SDK evidence.
- Downloads must be pinned, checksum-verified and installed into owned local folders. Preserve upstream licenses.
- Never commit `.runtime`, validation/output folders, character assets, user project files, credentials or personal paths.
- Run stdlib tests, the relevant real Blender regression, and Unity compile/import checks when those components change.

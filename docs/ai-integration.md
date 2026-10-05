# Connecting AI clients

Run INSTALL-TOOLS once, then use **Connect AI** in AvatarForge or **CONNECT-AI.bat**. The BAT detects installed clients; the interface lets you choose them. Nothing is registered just by launching the conversion UI. Routine conversion does not require AI.

Registration first starts the actual local stdio server, initializes MCP and verifies all seven tools. It then merges one avatarforge entry into each selected client's native configuration. A file update is not proof that a running client reloaded it: restart/reload the client and inspect its MCP tools.

| Windows client | User configuration | Project configuration | Reference |
|---|---|---|---|
| Codex | CODEX_HOME/config.toml or ~/.codex/config.toml | .codex/config.toml; project trust stays unchanged | [Official MCP guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) |
| Claude Code | ~/.claude.json | .mcp.json | [Official scopes](https://code.claude.com/docs/en/mcp) |
| Claude Desktop | APPDATA/Claude/claude_desktop_config.json | User only | [Local server guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers) |
| Cursor | ~/.cursor/mcp.json | .cursor/mcp.json | [Official MCP guide](https://cursor.com/docs/context/mcp) |
| Gemini CLI | ~/.gemini/settings.json | .gemini/settings.json | [Official MCP guide](https://geminicli.com/docs/tools/mcp-server/) |
| GitHub Copilot | COPILOT_HOME/mcp-config.json or ~/.copilot/mcp-config.json | .mcp.json | [Official configuration](https://code.visualstudio.com/docs/agent-customization/mcp-servers) |
| VS Code | APPDATA/Code/User/mcp.json | .vscode/mcp.json | [Official configuration](https://code.visualstudio.com/docs/agent-customization/mcp-servers) |
| Windsurf / Devin | Existing ~/.codeium/windsurf/mcp_config.json, otherwise APPDATA/devin/mcp_config.json | User only | [Current official guide](https://docs.devin.ai/desktop/cascade/mcp) |
| OpenCode | XDG_CONFIG_HOME/opencode/opencode.json or ~/.config/opencode/opencode.json; existing .jsonc respected | opencode.json | [Official MCP format](https://opencode.ai/docs/mcp-servers/) |
| Hermes | HERMES_HOME/config.yaml or ~/.hermes/config.yaml | User/profile home only | [Official registration source](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/mcp_config.py) |

Windows adapters have clean-home regression coverage. The complete source/portable archive is the supported distribution. JSON/JSONC updates preserve comments and unrelated bytes. TOML updates preserve unrelated tables and all unrelated parsed values. Hermes uses the installed save_config API and verifies unrelated parsed settings after its round-trip. A malformed configuration, unowned same-name server, locked file, or unsupported project scope stays untouched and is reported.

Only Codex, Claude Code, Gemini and Hermes receive their standard small avatarforge/SKILL.md; other clients use the MCP tools directly. The app, model outputs, editor addons and test files never belong in skill directories. Custom skill conflicts are reported. Editor bridges are optional and are not globally registered by AvatarForge.

Byte-verified originals are saved under .runtime/provider-backups/client/. These are local private data, excluded from Git and releases. Registration is idempotent. Backups can restore the original file, but preserve any later edits before restoring one. Re-run Connect AI after moving the app to update only its owned server paths.

For another local MCP client, copy **Other MCP clients → Copy MCP configuration**. The same entry is written to .runtime/ai-connection/mcp.json. Clients must support stdio subprocess servers. Browser-only clients cannot launch your local Python directly.

~~~powershell
.\.runtime\python\python.exe run.py connect --providers auto
.\.runtime\python\python.exe run.py connect --providers codex claude-code --project "C:\Projects\Avatar work"
~~~

# Security policy

Report vulnerabilities through [GitHub's private reporting form](https://github.com/SenjuWoo/AvatarForge/security/advisories/new), under **Security → Advisories → Report a vulnerability**. Include the affected commit/version, Windows and tool versions, reproduction steps, and the observed impact. Use generated sample files where possible. Keep credentials, account information and privately licensed models out of public issues and pull requests.

The maintained code is the current `main` branch and latest published version. Third-party tools retain their own security reporting channels; identify the affected dependency and pinned version when reporting an integration issue.

AvatarForge runs locally with the current user's filesystem permissions. Its web interface binds to loopback and uses a per-session token with Host/Origin checks. Conversion launches local Blender, importer addons and Unity tools; the isolated Blender profile and disabled embedded scripts are not an operating-system sandbox. Optional live-editor MCP bridges have their own connection and execution scope.

Relevant reports include loopback authorization bypasses, archive extraction outside the intended folder, unexpected source/project modification, download verification bypasses, unsafe process invocation, or private data entering a distributed artifact. The dependency catalog is pinned and reviewed separately; GitHub Actions updates do not automatically upgrade model importers or certify their runtime behavior.

Source checks and CodeQL analysis are evidence about specific checks, not a security certification or a promise that arbitrary model files are safe.

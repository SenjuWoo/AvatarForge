---
name: avatarforge
description: Convert local Blender, Source/SFM, XPS/XNALara, MMD, VRM and common 3D models through AvatarForge to a reviewed Unity and VRChat workspace, preserving secondary bones and shape keys.
---

# AvatarForge
<!-- AvatarForge-managed skill -->

Use the registered `avatarforge_*` MCP tools. Start with `avatarforge_doctor`, then `avatarforge_scan` on the user's local model or folder. Select the intended model when several candidates exist.

Use Preserve for the first conversion. PC balanced and Mobile candidate are optimization targets. Submit one conversion, poll `avatarforge_job` with compact replies, then inspect the preservation verdict and preview. Request `details=true` only when needed for a specific fault. A blocked conversion needs repair; `needs_review` must remain visible.

Retain breasts, butt, hair, clothing, tail and other secondary chains and all required shape keys. Name-based PhysBone suggestions require preview and tuning. Pass only reviewed roots reported by that conversion to `avatarforge_prepare_unity`, then poll `avatarforge_action`.

Routine conversions are deterministic and consume no AI credits. Live Blender or Unity MCP is optional for difficult repairs, enabled only for the relevant project. Preserve original assets, personal editor preferences and existing Unity projects. Never upload or publish an avatar without the user's instruction.

If the MCP is missing, use AvatarForge's **Connect AI** button or `CONNECT-AI.bat`, then reload the client. For clients without MCP, use `START-HERE.bat`; do not invent a connection. This skill folder contains instructions only, not the application or its dependencies.

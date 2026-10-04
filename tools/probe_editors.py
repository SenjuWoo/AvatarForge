"""Optional real MCP client probe. Requires the official Python MCP SDK.

Run with a Python environment containing mcp, passing an actual server command
after --. No editor preferences, model files or provider settings are modified.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(args):
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = str(Path(__file__).resolve().parents[1] / ".runtime" / "mcp-cache")
    environment["BLENDER_MCP_DISABLE_TELEMETRY"] = "true"
    if args.status_dir:
        environment["UNITY_MCP_STATUS_DIR"] = str(Path(args.status_dir).resolve())
    params = StdioServerParameters(command=args.command[0], args=args.command[1:], env=environment)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            data = {"server": info.model_dump(), "tools": [t.name for t in tools.tools]}
            if args.instance:
                selection = await session.call_tool("set_active_instance", {"instance": args.instance})
                data["selected_instance"] = selection.model_dump(mode="json")
                payload = getattr(selection, "structured_content", None) or getattr(selection, "structuredContent", None)
                if payload is None:
                    payload = json.loads(next(c.text for c in selection.content if c.type == "text"))
                if not payload.get("success"):
                    raise RuntimeError("Editor instance selection failed: " + str(payload))
            if args.tool:
                arguments = Path(args.arguments_file).read_text(encoding="utf-8-sig") if args.arguments_file else args.arguments
                tool_result = await session.call_tool(args.tool, json.loads(arguments))
                data["call"] = tool_result.model_dump(mode="json")
            if args.resource:
                result = await session.read_resource(args.resource)
                data["resource"] = result.model_dump(mode="json")
            if args.schemas:
                names = set(args.schemas.split(","))
                data["schemas"] = [t.model_dump(mode="json") for t in tools.tools if t.name in names]
            if args.resources:
                data["resources"] = (await session.list_resources()).model_dump(mode="json")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            if args.save:
                Path(args.save).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            if args.tool:
                payload = getattr(tool_result, "structured_content", None) or getattr(tool_result, "structuredContent", None)
                if payload is None:
                    try:
                        payload = json.loads(next(c.text for c in tool_result.content if c.type == "text"))
                    except (ValueError, StopIteration):
                        payload = {}
                is_error = getattr(tool_result, "is_error", None) or getattr(tool_result, "isError", False)
                # Some upstream Blender bridge failures arrive as successful
                # MCP envelopes containing this explicit error string.
                messages = [c.text for c in tool_result.content if c.type == "text"]
                if isinstance(payload, dict) and isinstance(payload.get("result"), str):
                    messages.append(payload["result"])
                bridge_error = any(message.lstrip().startswith("Error executing code:") for message in messages)
                if is_error or bridge_error or (isinstance(payload, dict) and payload.get("success") is False):
                    raise RuntimeError("MCP tool returned failure; see the saved response.")


parser = argparse.ArgumentParser()
parser.add_argument("--tool")
parser.add_argument("--instance")
parser.add_argument("--status-dir")
arguments_group = parser.add_mutually_exclusive_group()
arguments_group.add_argument("--arguments", default="{}")
arguments_group.add_argument("--arguments-file", help="UTF-8 JSON tool arguments; avoids shell quoting.")
parser.add_argument("--resource")
parser.add_argument("--resources", action="store_true")
parser.add_argument("--schemas")
parser.add_argument("--save")
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
if args.command and args.command[0] == "--":
    args.command.pop(0)
if not args.command:
    parser.error("Pass an actual MCP server executable and its arguments after --.")
asyncio.run(probe(args))

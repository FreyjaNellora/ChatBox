#!/usr/bin/env python3
"""
AgentChat Broker — MCP stdio server for real-time agent coordination.

Thin transport wrapper around broker_core.Broker.
"""

import asyncio
import json
import logging
import os
import sys

sys.dont_write_bytecode = True
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from broker_core import (
    Broker,
    BrokerError,
    MAX_MSGS_PER_LISTEN,
    WORKSPACE_ROOT,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("agentchat")

# ---------------------------------------------------------------------------
# Broker instance
# ---------------------------------------------------------------------------

_AUTH_TOKEN = os.environ.get("AGENTCHAT_AUTH_TOKEN") or None
broker = Broker(auth_token=_AUTH_TOKEN)
broker.set_lock(asyncio.Lock())
app = Server("agentchat")

# For MCP stdio transport, auth is optional (parent process is trusted).
# The Broker class will enforce auth if AGENTCHAT_AUTH_TOKEN is set.
# If unset, the broker is open — this is acceptable for local stdio use.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(result: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(result))]


def _err(code: str, message: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"status": "error", "code": code, "message": message}))]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="hello", description="Register agent and subscribe to default channels",
             inputSchema={"type": "object", "properties": {
                 "name": {"type": "string"}, "phase": {"type": "string"},
                 "default_channels": {"type": "array", "items": {"type": "string"}},
                 "auth_token": {"type": "string"}},
              "required": ["name", "phase", "default_channels"]}),
        Tool(name="chat", description="Send a freeform chat message",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "channel": {"type": "string"}, "body": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "channel", "body"]}),
        Tool(name="start_post", description="Start a structured post",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "channel": {"type": "string"},
                 "title": {"type": "string"}, "description": {"type": "string"},
                 "type": {"type": "string"}, "tier": {"type": "integer"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "channel", "title", "description", "type"]}),
        Tool(name="reply", description="Reply to a post",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "post_id": {"type": "integer"}, "body": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "post_id", "body"]}),
        Tool(name="get_post", description="Fetch a post and its replies",
             inputSchema={"type": "object", "properties": {
                 "post_id": {"type": "integer"}},
              "required": ["post_id"]}),
        Tool(name="listen", description="Long-poll for messages on subscribed channels",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "channels": {"type": "array", "items": {"type": "string"}},
                 "view": {"type": "string", "enum": ["headlines", "digest", "full"]},
                 "since_id": {"type": "integer"}, "timeout_ms": {"type": "integer"},
                 "max_msgs": {"type": "integer", "default": 10},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "channels", "view", "since_id", "timeout_ms"]}),
        Tool(name="subscribe", description="Subscribe to a channel",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "channel": {"type": "string"}, "view": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "channel"]}),
        Tool(name="unsubscribe", description="Unsubscribe from a channel",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "channel": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "channel"]}),
        Tool(name="rooms", description="List available channels",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="approve", description="Approve a post",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "post_id": {"type": "integer"}, "comment": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "post_id"]}),
        Tool(name="deny", description="Deny a post",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "post_id": {"type": "integer"}, "reason": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "post_id", "reason"]}),
        Tool(name="pin_post", description="Pin a post",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "post_id": {"type": "integer"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "post_id"]}),
        Tool(name="unpin_post", description="Unpin a post",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "post_id": {"type": "integer"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "post_id"]}),
        Tool(name="close_post", description="Close a post with resolution",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "post_id": {"type": "integer"}, "resolution": {"type": "string"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "post_id", "resolution"]}),
        Tool(name="resolve_message", description="Mark a message as resolved and update its dispatch mirror",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "message_id": {"type": "integer"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "message_id"]}),
        Tool(name="unresolve_message", description="Mark a message as unresolved and update its dispatch mirror",
             inputSchema={"type": "object", "properties": {
                 "agent_name": {"type": "string"}, "message_id": {"type": "integer"},
                 "auth_token": {"type": "string"}},
              "required": ["agent_name", "message_id"]}),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "hello":
            result = await broker.hello(
                arguments["name"], arguments["phase"], arguments["default_channels"],
                auth_token=arguments.get("auth_token"))
        elif name == "chat":
            result = await broker.chat(
                arguments["agent_name"], arguments["channel"], arguments["body"],
                session_token=arguments.get("session_token"))
        elif name == "start_post":
            result = await broker.start_post(
                arguments["agent_name"], arguments["channel"], arguments["title"],
                arguments["description"], arguments["type"], arguments.get("tier"),
                session_token=arguments.get("session_token"))
        elif name == "reply":
            result = await broker.reply(
                arguments["agent_name"], arguments["post_id"], arguments["body"],
                session_token=arguments.get("session_token"))
        elif name == "get_post":
            result = await broker.get_post(arguments["post_id"])
        elif name == "listen":
            result = await broker.listen(
                arguments["agent_name"], arguments["channels"], arguments["view"],
                arguments["since_id"], arguments["timeout_ms"],
                arguments.get("max_msgs", MAX_MSGS_PER_LISTEN),
                session_token=arguments.get("session_token"))
        elif name == "subscribe":
            result = await broker.subscribe(
                arguments["agent_name"], arguments["channel"], arguments.get("view", "full"),
                session_token=arguments.get("session_token"))
        elif name == "unsubscribe":
            result = await broker.unsubscribe(
                arguments["agent_name"], arguments["channel"],
                session_token=arguments.get("session_token"))
        elif name == "rooms":
            result = await broker.rooms()
        elif name == "approve":
            result = await broker.approve(
                arguments["agent_name"], arguments["post_id"], arguments.get("comment"),
                session_token=arguments.get("session_token"))
        elif name == "deny":
            result = await broker.deny(
                arguments["agent_name"], arguments["post_id"], arguments["reason"],
                session_token=arguments.get("session_token"))
        elif name == "pin_post":
            result = await broker.pin_post(
                arguments["agent_name"], arguments["post_id"],
                session_token=arguments.get("session_token"))
        elif name == "unpin_post":
            result = await broker.unpin_post(
                arguments["agent_name"], arguments["post_id"],
                session_token=arguments.get("session_token"))
        elif name == "close_post":
            result = await broker.close_post(
                arguments["agent_name"], arguments["post_id"], arguments["resolution"],
                session_token=arguments.get("session_token"))
        elif name == "resolve_message":
            result = await broker.resolve_message(
                arguments["agent_name"], arguments["message_id"],
                session_token=arguments.get("session_token"))
        elif name == "unresolve_message":
            result = await broker.unresolve_message(
                arguments["agent_name"], arguments["message_id"],
                session_token=arguments.get("session_token"))
        else:
            return _err("UNKNOWN_TOOL", f"Unknown tool: {name}")
    except BrokerError as exc:
        logger.warning("Broker error in %s: %s", name, exc.message)
        return _err(exc.code, exc.message)
    except KeyError as exc:
        logger.warning("Missing argument in %s: %s", name, exc)
        return _err("VALIDATION_ERROR", f"Missing required argument: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error in %s", name)
        return _err("INTERNAL_ERROR", str(exc))
    return _ok(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    logger.info("AgentChat broker starting (workspace: %s)", WORKSPACE_ROOT)
    if _AUTH_TOKEN:
        logger.info("Auth token is configured")
    else:
        logger.warning("No AGENTCHAT_AUTH_TOKEN set — broker is open")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

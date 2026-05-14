#!/usr/bin/env python3
"""
AgentChat Broker Daemon — unified entry point.

Starts the HTTP API server (always) and optionally the MCP stdio server.
Designed to run as a Windows service via NSSM, or manually.

Environment variables:
    AGENTCHAT_WORKSPACE   — project root
    AGENTCHAT_AUTH_TOKEN  — optional auth token
    AGENTCHAT_HTTP_PORT   — HTTP port (default: 8765)
    AGENTCHAT_HTTP_HOST   — HTTP bind address (default: 0.0.0.0)
    AGENTCHAT_MCP_STDIO   — set to "1" to also start MCP stdio server
"""

import asyncio
import logging
import os
import sys

sys.dont_write_bytecode = True

from broker_core import Broker, WORKSPACE_ROOT
from broker_http import _start_broker_loop, _HTTP_HOST, _HTTP_PORT, _AUTH_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("agentchat.daemon")


def _start_http_server():
    """Import and start the HTTP server in the main thread."""
    from http.server import HTTPServer
    from broker_http import BrokerHTTPHandler

    server = HTTPServer((_HTTP_HOST, _HTTP_PORT), BrokerHTTPHandler)
    logger.info("HTTP server listening on http://%s:%d", _HTTP_HOST, _HTTP_PORT)
    server.serve_forever()


async def _start_mcp_stdio():
    """Start the MCP stdio server."""
    from broker import app, broker as mcp_broker
    from mcp.server.stdio import stdio_server

    # Reuse the same broker instance if possible, otherwise create new
    logger.info("MCP stdio server starting")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    logger.info("AgentChat daemon starting (workspace: %s)", WORKSPACE_ROOT)
    if _AUTH_TOKEN:
        logger.info("Auth token is configured")
    else:
        logger.warning("No AGENTCHAT_AUTH_TOKEN set — broker is open")

    # Start the shared broker event loop in a background thread
    _start_broker_loop()

    # Optionally start MCP stdio in a background thread
    if os.environ.get("AGENTCHAT_MCP_STDIO") == "1":
        def run_mcp():
            asyncio.set_event_loop(asyncio.new_event_loop())
            asyncio.get_event_loop().run_until_complete(_start_mcp_stdio())

        import threading
        mcp_thread = threading.Thread(target=run_mcp, daemon=True, name="mcp-stdio")
        mcp_thread.start()
        logger.info("MCP stdio server started in background thread")

    # HTTP server runs in the main thread (blocks)
    _start_http_server()


if __name__ == "__main__":
    main()

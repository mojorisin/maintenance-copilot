"""Give the LangGraph agent its tools over MCP, for the parity eval.

The agent's loop is synchronous; the MCP client is async. The bridge runs one
MCP client session on a background event loop and exposes each tool the
server *advertises* (name, description, input schema) as a synchronous
LangChain tool. The agent then sees exactly what an external MCP client sees.

    with MCPToolBridge(StdioServerParameters(...)) as bridge:
        ask = build_agent(tool_list=bridge.langchain_tools())
"""

import asyncio
import json
import threading
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import Client

CALL_TIMEOUT_S = 60


class MCPToolBridge:
    """One MCP client session, usable from synchronous code and multiple threads."""

    def __init__(self, target: Any):
        """`target`: an MCPServer object (in memory), a URL, or StdioServerParameters."""
        self._target = target
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._ready = threading.Event()
        self._client: Client | None = None
        self._tools: list = []

    def __enter__(self) -> "MCPToolBridge":
        self._thread.start()
        self._session = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        while not self._ready.wait(timeout=0.1):
            if self._session.done():  # failed to connect: surface the real error
                self._session.result()
        return self

    def __exit__(self, *exc) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._session.result(timeout=CALL_TIMEOUT_S)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _serve(self) -> None:
        # The client must be entered and exited in the same task, so one
        # long-lived task holds the session open until __exit__.
        self._stop = asyncio.Event()
        async with Client(self._target) as client:
            self._client = client
            self._tools = (await client.list_tools()).tools
            self._ready.set()
            await self._stop.wait()

    def call(self, name: str, arguments: dict) -> dict:
        """Call a tool and return the same dict shape as the in-process tools."""
        coro = self._client.call_tool(name, arguments)
        result = asyncio.run_coroutine_threadsafe(coro, self._loop).result(CALL_TIMEOUT_S)
        if result.is_error:
            return {"error": " ".join(c.text for c in result.content if c.type == "text")}
        if result.structured_content is not None:
            return result.structured_content
        return json.loads(result.content[0].text)

    def langchain_tools(self) -> list[StructuredTool]:
        """The server's advertised tools, as synchronous LangChain tools."""
        return [
            StructuredTool(
                name=t.name,
                description=t.description or "",
                args_schema=t.input_schema,
                func=lambda _name=t.name, **kwargs: self.call(_name, kwargs),
            )
            for t in self._tools
        ]

"""Tests for the MCP bridge used by the parity eval (in memory, offline).

The key property: over MCP, the model must see the same tool names,
descriptions and parameters as with the direct tools, and get the same results.
"""

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from test_agent import ScriptedModel, _tool_call

from maintenance_copilot.agent import TOOLS, build_agent
from maintenance_copilot.mcp_bridge import MCPToolBridge
from maintenance_copilot.mcp_server import mcp
from maintenance_copilot.tools import query_work_orders


def test_model_sees_identical_tools_over_mcp(data_env):
    with MCPToolBridge(mcp) as bridge:
        bridged = {t.name: convert_to_openai_tool(t)["function"] for t in bridge.langchain_tools()}
    for direct in TOOLS:
        d = convert_to_openai_tool(direct)["function"]
        m = bridged[direct.name]
        assert m["description"] == d["description"], direct.name
        assert m["parameters"]["properties"].keys() == d["parameters"]["properties"].keys()
        assert m["parameters"].get("required") == d["parameters"].get("required")


def test_results_over_mcp_match_direct_calls(data_env):
    sql = "SELECT wo_id, asset_id FROM work_orders WHERE asset_id = 'GT-042' ORDER BY wo_id"
    with MCPToolBridge(mcp) as bridge:
        assert bridge.call("query_work_orders", {"sql": sql}) == query_work_orders(sql)
        error = bridge.call("query_work_orders", {"sql": "DELETE FROM work_orders"})
    assert "Only read-only SELECT queries are allowed." in error["error"]


def test_agent_runs_end_to_end_over_mcp(data_env):
    model = ScriptedModel(
        responses=[
            _tool_call("get_sensor_trend", {"asset_id": "GT-042"}),
            AIMessage(content="GT-042 [cmapss:FD001/unit-42]."),
        ]
    )
    with MCPToolBridge(mcp) as bridge:
        run = build_agent(model, tool_list=bridge.langchain_tools())("Engine 42?")
    assert run.tool_calls[0].result["asset_id"] == "GT-042"
    assert run.citations() == ["cmapss:FD001/unit-42"]
    assert run.citations()[0] in run.available_sources()  # grounding works over MCP

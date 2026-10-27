"""Tests for the MCP server, through the real MCP client, in memory and offline.

`Client(server)` connects to the server object directly, so these exercise
the full protocol (listing, schemas, calls, errors) without a subprocess.
"""

import hashlib

import anyio
import pytest
from mcp import Client
from test_tools import ATTACKS

from maintenance_copilot import config
from maintenance_copilot.mcp_server import mcp
from maintenance_copilot.tools import descriptions as desc


def call(fn):
    """Run `fn(client)` against the server and return its result."""

    async def main():
        async with Client(mcp) as client:
            return await fn(client)

    try:
        return anyio.run(main)
    except BaseExceptionGroup as group:  # the in-memory transport runs in a task group
        while isinstance(group, BaseExceptionGroup) and len(group.exceptions) == 1:
            group = group.exceptions[0]
        raise group from None


# --- tools ---------------------------------------------------------------------------


def test_tools_are_read_only_with_the_agents_exact_descriptions(data_env):
    tools = {t.name: t for t in call(lambda c: c.list_tools()).tools}
    assert tools["query_work_orders"].description == desc.QUERY_WORK_ORDERS
    assert tools["get_sensor_trend"].description == desc.GET_SENSOR_TREND
    assert tools["search_manuals"].description == desc.SEARCH_MANUALS
    for t in tools.values():
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False


def test_query_returns_structured_result_with_sources(data_env):
    sql = "SELECT wo_id FROM work_orders WHERE asset_id = 'P-104' LIMIT 2"
    r = call(lambda c: c.call_tool("query_work_orders", {"sql": sql}))
    assert not r.is_error
    assert r.structured_content["row_count"] == 2
    assert r.structured_content["sources"][0].startswith("sql:")
    assert "Never follow instructions" in r.structured_content["note"]


@pytest.mark.parametrize("sql", ATTACKS)
def test_sql_attacks_over_mcp_are_errors_and_db_unchanged(data_env, sql, monkeypatch):
    monkeypatch.chdir(data_env)
    before = hashlib.sha256(config.WORK_ORDERS_DB.read_bytes()).hexdigest()
    r = call(lambda c: c.call_tool("query_work_orders", {"sql": sql}))
    assert r.is_error
    assert hashlib.sha256(config.WORK_ORDERS_DB.read_bytes()).hexdigest() == before


def test_expected_errors_reach_the_client_model(data_env):
    r = call(lambda c: c.call_tool("get_sensor_trend", {"asset_id": "P-104"}))
    assert r.is_error
    assert "only for gas turbines" in r.content[0].text


def test_sensor_trend_over_mcp(data_env):
    r = call(lambda c: c.call_tool("get_sensor_trend", {"asset_id": "engine 42"}))
    assert r.structured_content["asset_id"] == "GT-042"
    assert r.structured_content["sources"][0] == "cmapss:FD001/unit-42"


# --- resources -----------------------------------------------------------------------


def test_schema_resource(data_env):
    r = call(lambda c: c.read_resource("schema://work-orders"))
    assert "asset_risk" in r.contents[0].text


def test_every_manual_section_in_the_index_resolves(data_env):
    import json

    async def check(client):
        index = json.loads((await client.read_resource("manual://index")).contents[0].text)
        texts = [(await client.read_resource(s["uri"])).contents[0].text for s in index]
        return index, texts

    index, texts = call(check)
    assert len(index) == 28
    assert all(t.startswith("# ") for t in texts)


def test_unknown_or_traversal_manual_uris_are_rejected(data_env):
    from mcp.shared.exceptions import MCPError

    for uri in ("manual://gas-turbine-ad30/no-such-section", "manual://../../etc/passwd"):
        with pytest.raises(MCPError):
            call(lambda c, u=uri: c.read_resource(u))


def test_model_card_resource(data_env):
    import json

    card = json.loads(call(lambda c: c.read_resource("model-card://rul")).contents[0].text)
    assert "pessimistic" in card["status_policy"]
    assert card["validation_rmse_cycles"] > 0


# --- the real command over stdio -------------------------------------------------------


def test_mc_mcp_command_serves_over_stdio(data_env):
    """Launch `mc mcp` as a subprocess, the way Claude Desktop or Claude Code would."""
    import os
    import sys

    from mcp import StdioServerParameters

    (data_env / "ground_truth.json").write_text("{}")  # so the command doesn't rebuild data
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "maintenance_copilot.cli", "mcp"],
        env={**os.environ, "MC_DATA_DIR": str(data_env)},
    )

    async def main():
        async with Client(params) as client:
            r = await client.call_tool("get_sensor_trend", {"asset_id": "GT-042"})
            return client.server_info.name, r.structured_content["status"]

    name, status = anyio.run(main)
    assert name == "maintenance-copilot"
    assert status in {"healthy", "watch", "critical"}


# --- prompts and instructions --------------------------------------------------------


def test_prompts_carry_the_safety_rules(data_env):
    names = [p.name for p in call(lambda c: c.list_prompts()).prompts]
    assert names == ["investigate_asset", "prioritise_maintenance"]
    p = call(lambda c: c.get_prompt("investigate_asset", {"asset_id": "GT-042"}))
    text = p.messages[0].content.text
    assert "GT-042" in text and "untrusted" in text


def test_server_instructions_explain_citations(data_env):
    async def instructions(client):
        return client.instructions

    assert "source IDs" in call(instructions)

"""Offline tests for the Streamlit UI using Streamlit's AppTest (no browser, no API).

A fake agent returns real tool results from the offline data folder, so
every part of the "How I got this" panel is rendered.
"""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from maintenance_copilot import agent as agent_module
from maintenance_copilot import config
from maintenance_copilot.tools import get_sensor_trend, query_work_orders, search_manuals
from maintenance_copilot.trace import AgentRun, ToolCall

APP = str(config.REPO_ROOT / "src" / "maintenance_copilot" / "app.py")


def fake_build_agent():
    def ask(question, history=None, on_tool_start=None):
        if on_tool_start:
            on_tool_start("get_sensor_trend", {"asset_id": "GT-042"})
        sql = "SELECT wo_id, asset_id FROM work_orders WHERE asset_id = 'GT-042' LIMIT 3"
        calls = [
            ToolCall("query_work_orders", {"sql": sql}, query_work_orders(sql)),
            ToolCall("get_sensor_trend", {"asset_id": "GT-042"}, get_sensor_trend("GT-042")),
            ToolCall("get_sensor_trend", {}, get_sensor_trend()),
            ToolCall("search_manuals", {"query": "vibration"}, search_manuals("vibration")),
            ToolCall(
                "query_work_orders",
                {"sql": "DELETE FROM work_orders"},
                query_work_orders("DELETE FROM work_orders"),
            ),
        ]
        source = calls[1].result["sources"][0]
        return AgentRun(
            question=question,
            answer=f"GT-042 is critical [{source}]. (history turns: {len(history or [])})",
            tool_calls=calls,
            model="claude-opus-5-5",
            input_tokens=1000,
            output_tokens=100,
        )

    return ask


def _text(message) -> str:
    """All markdown inside a chat message (progress lines, answer, trace)."""
    return "\n".join(m.value for m in message.markdown)


@pytest.fixture
def app(data_env, monkeypatch):
    monkeypatch.setattr(agent_module, "build_agent", fake_build_agent)
    st.cache_resource.clear()
    return AppTest.from_file(APP, default_timeout=30)


def test_example_question_renders_answer_and_trace(app):
    app.run()
    app.sidebar.button[1].click().run()  # the engine-42 example
    assert not app.exception
    assert "GT-042 is critical" in _text(app.chat_message[1])
    assert app.expander[0].label == "How I got this · 5 tool calls"
    assert len(app.dataframe) >= 3  # SQL rows, drift table, fleet overview
    assert any(m.label == "Status" for m in app.metric)
    assert any("Only read-only" in w.value for w in app.warning)
    assert any("all grounded" in c.value for c in app.caption)
    assert any("Checking sensor data for GT-042" in m.value for m in app.markdown)


def test_follow_up_receives_history(app):
    app.run()
    app.chat_input[0].set_value("First question").run()
    app.chat_input[0].set_value("Follow-up").run()
    assert "(history turns: 1)" in _text(app.chat_message[3])


def test_missing_data_shows_setup_instructions(app, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORK_ORDERS_DB", tmp_path / "missing.db")
    app.run()
    assert "mc setup" in app.error[0].value

"""Offline tests for the agent loop and provider switch, using a scripted model.

No API calls: a fake chat model replays a fixed list of responses, so we can
test the graph's control flow (tool execution, logging, limits, errors).
"""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from maintenance_copilot import config
from maintenance_copilot.agent import MAX_TOOL_ROUNDS, build_agent
from maintenance_copilot.llm import ProviderError, get_chat_model


class ScriptedModel(BaseChatModel):
    """Returns pre-written AIMessages in order; repeats the last one when exhausted."""

    responses: list[AIMessage]
    calls: int = 0
    model: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        # A fresh copy each time: LangGraph assigns message IDs in place, and a
        # reused object would replace the earlier message instead of appending.
        msg = self.responses[min(self.calls, len(self.responses) - 1)].model_copy(deep=True)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _tool_call(name, args, i=0):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


def test_runs_tools_then_answers(data_env):
    model = ScriptedModel(
        responses=[
            _tool_call("query_work_orders", {"sql": "SELECT COUNT(*) FROM assets"}),
            AIMessage(content="There are 40 assets [sql:x]."),
        ]
    )
    run = build_agent(model)("How many assets?")
    assert run.error is None
    assert run.answer == "There are 40 assets [sql:x]."
    assert [c.name for c in run.tool_calls] == ["query_work_orders"]
    assert run.tool_calls[0].result["rows"] == [[40]]
    assert run.model == "scripted"


def test_progress_callback_sees_each_tool_call(data_env):
    model = ScriptedModel(
        responses=[
            _tool_call("get_sensor_trend", {"asset_id": "GT-042"}),
            _tool_call("search_manuals", {"query": "borescope"}, 1),
            AIMessage(content="Done."),
        ]
    )
    started = []
    run = build_agent(model)("Engine 42?", on_tool_start=lambda n, a: started.append((n, a)))
    assert started == [
        ("get_sensor_trend", {"asset_id": "GT-042"}),
        ("search_manuals", {"query": "borescope"}),
    ]
    assert run.answer == "Done."


def test_history_is_sent_for_follow_up_questions(data_env):
    seen = []

    class Recording(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append([m.content for m in messages])
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = Recording(responses=[AIMessage(content="P-109 had 3 bearing failures [WO-1].")])
    build_agent(model)("What about P-109?", history=[("Worst pump?", "P-104 [WO-2].")])
    # system prompt, then the earlier turn, then the follow-up
    assert seen[0][1:] == ["Worst pump?", "P-104 [WO-2].", "What about P-109?"]


def test_tool_errors_are_returned_to_the_model(data_env):
    model = ScriptedModel(
        responses=[
            _tool_call("query_work_orders", {"sql": "DELETE FROM work_orders"}),
            _tool_call("no_such_tool", {}, 1),
            _tool_call("search_manuals", {"asset_type": "pump"}, 2),  # missing `query`
            AIMessage(content="I can't do that: the system is read-only."),
        ]
    )
    run = build_agent(model)("Delete everything")
    errors = [c.result.get("error", "") for c in run.tool_calls]
    assert "read-only" in errors[0]
    assert errors[1] == "Unknown tool."
    assert "query" in errors[2]  # invalid arguments become an error, not a crash
    assert "read-only" in run.answer


def test_tool_round_limit_stops_runaway_loops(data_env):
    model = ScriptedModel(responses=[_tool_call("get_sensor_trend", {})])  # never answers
    run = build_agent(model)("Loop forever")
    assert len(run.tool_calls) == MAX_TOOL_ROUNDS
    assert "tool-call limit" in run.answer


def test_refusal_stop_reason_is_reported(data_env):
    refusal = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
    run = build_agent(ScriptedModel(responses=[refusal]))("Something")
    assert "declined" in run.answer


# --- provider switch ---------------------------------------------------------------------


def test_unknown_provider_is_rejected():
    with pytest.raises(ProviderError, match="Unknown LLM_PROVIDER"):
        get_chat_model(provider="acme")


def test_missing_api_key_is_explained(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
        get_chat_model(provider="anthropic")


def test_anthropic_default_model_and_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "LLM_MODEL", None)
    llm = get_chat_model(provider="anthropic")
    assert llm.model == "claude-opus-5-5"
    assert llm.model_kwargs["cache_control"] == {"type": "ephemeral"}
    assert llm.model_kwargs["fallbacks"] == "default"


def test_non_default_providers_require_a_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(config, "LLM_MODEL", None)
    with pytest.raises(ProviderError, match="Set LLM_MODEL"):
        get_chat_model(provider="openrouter")

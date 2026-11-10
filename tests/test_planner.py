"""Offline tests for the work-order planner graph and its single write path.

A scripted model stands in for the LLM: it returns pre-written structured
outputs (as tool calls), so these tests check the graph's control flow, the
guardrails and the approval step without any API calls.
"""

import hashlib
import sqlite3

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from maintenance_copilot import actions, config, tools
from maintenance_copilot.planner import HISTORY_SQL, Planner, normalise_asset_id


class ScriptedModel(BaseChatModel):
    """Replays structured outputs in order; counts calls."""

    responses: list[AIMessage]
    calls: int = 0
    model: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.responses[min(self.calls, len(self.responses) - 1)].model_copy(deep=True)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def triage(asset_id, symptom="leaking", terms="mechanical seal leakage", in_scope=True):
    args = {"asset_id": asset_id, "symptom": symptom, "search_terms": terms, "in_scope": in_scope}
    return AIMessage(content="", tool_calls=[{"name": "Triage", "args": args, "id": "t"}])


def draft(asset_id, citations, priority="high"):
    cite = " ".join(f"[{c}]" for c in citations)
    args = {
        "asset_id": asset_id,
        "priority": priority,
        "title": f"Replace mechanical seal, {asset_id}",
        "failure_mode": "seal leak",
        "actions": [f"Isolate and replace the mechanical seal {cite}", "Check the flush line"],
        "reasoning": f"Criticality A with an active fault {cite}.",
    }
    return AIMessage(content="", tool_calls=[{"name": "WorkOrderDraft", "args": args, "id": "d"}])


def history_wo(asset_id: str) -> str:
    """A real work-order ID from the asset's history, to cite in scripted drafts."""
    return next(
        s for s in tools.query_work_orders(HISTORY_SQL, (asset_id,))["sources"] if "WO-" in s
    )


def db_hash() -> str:
    return hashlib.sha256(config.WORK_ORDERS_DB.read_bytes()).hexdigest()


# --- happy path and the approval step --------------------------------------------------


def test_pump_request_pauses_for_approval_with_grounded_draft(data_env):
    wo = history_wo("P-104")
    model = ScriptedModel(responses=[triage("p104"), draft("P-104", [wo])])
    steps = []
    run = Planner(model).start("P-104 is leaking again", on_step=steps.append)

    assert run.status == "awaiting_approval"
    assert run.asset["asset_id"] == "P-104" and run.asset["criticality"] == "A"
    # Pumps: history and manuals, but no sensor data (gas turbines only).
    assert [c.name for c in run.evidence] == [
        "query_work_orders",
        "query_work_orders",
        "search_manuals",
    ]
    assert "sensors" not in steps and "plan" in steps
    assert run.draft["priority"] == "high"
    assert run.issues == []
    assert run.citations() == [wo]
    assert actions.list_actioned() == []  # nothing written before approval


def test_approval_writes_once_to_the_actions_db_only(data_env):
    before = db_hash()
    model = ScriptedModel(responses=[triage("P-104"), draft("P-104", [history_wo("P-104")])])
    planner = Planner(model)
    run = planner.start("P-104 is leaking again")
    run = planner.resume(run.thread_id, approved=True, reviewer="A. Reviewer")

    assert run.status == "actioned"
    assert run.action_id == "AWO-0001"
    rows = actions.list_actioned()
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "P-104"
    assert rows[0]["approved_by"] == "A. Reviewer"
    assert rows[0]["thread_id"] == run.thread_id
    assert db_hash() == before  # the work-order database is untouched
    assert model.calls == 2  # resuming does not call the LLM again


def test_rejection_writes_nothing(data_env):
    model = ScriptedModel(responses=[triage("P-104"), draft("P-104", [history_wo("P-104")])])
    planner = Planner(model)
    run = planner.resume(planner.start("P-104 leak").thread_id, approved=False)
    assert run.status == "rejected"
    assert run.action_id is None
    assert actions.list_actioned() == []


# --- routing to the right specialists --------------------------------------------------


def test_turbine_request_uses_sensor_data(data_env):
    model = ScriptedModel(
        responses=[
            triage("engine 42", "vibration", "high vibration alarm"),
            draft("GT-042", ["cmapss:FD001/unit-42"]),
        ]
    )
    run = Planner(model).start("Engine 42 is vibrating more than usual")
    assert run.asset["asset_id"] == "GT-042"
    assert "get_sensor_trend" in [c.name for c in run.evidence]
    assert run.issues == []


def test_valve_request_has_no_manual_or_sensor_specialist(data_env):
    model = ScriptedModel(responses=[triage("V-306"), draft("V-306", [history_wo("V-306")])])
    run = Planner(model).start("V-306 is sticking")
    assert [c.name for c in run.evidence] == ["query_work_orders", "query_work_orders"]


@pytest.mark.parametrize(
    ("triaged", "expected"),
    [
        (triage("P-204"), "no asset P-204"),
        (triage(None), "Which asset"),
        (triage(None, in_scope=False), "only plan maintenance"),
    ],
)
def test_unknown_asset_or_off_topic_stops_before_planning(data_env, triaged, expected):
    model = ScriptedModel(responses=[triaged])
    run = Planner(model).start("something")
    assert run.status == "declined"
    assert expected in run.message
    assert run.draft is None
    assert model.calls == 1  # the planner LLM is never called


# --- guardrails ------------------------------------------------------------------------


def test_check_flags_invented_citations_and_corrects_the_asset(data_env):
    model = ScriptedModel(responses=[triage("P-104"), draft("P-109", ["WO-99999"])])
    run = Planner(model).start("P-104 is leaking")
    assert run.draft["asset_id"] == "P-104"
    assert any("corrected to P-104" in i for i in run.issues)
    assert any("WO-99999" in i for i in run.issues)


def test_draft_without_citations_is_flagged(data_env):
    model = ScriptedModel(responses=[triage("P-104"), draft("P-104", [])])
    run = Planner(model).start("P-104 is leaking")
    assert "The draft cites no sources." in run.issues


def test_prose_or_malformed_replies_get_one_nudge_then_fail_cleanly(data_env):
    prose = AIMessage(content="It's probably the seal.")
    malformed = AIMessage(
        content="", tool_calls=[{"name": "Triage", "args": {"asset_id": "P-104"}, "id": "m"}]
    )
    # Recovers after one nudge...
    model = ScriptedModel(responses=[prose, triage("P-104"), draft("P-104", [])])
    assert Planner(model).start("P-104 leak").status == "awaiting_approval"
    # ...but gives up after a second bad reply, without crashing.
    model = ScriptedModel(responses=[malformed, prose])
    run = Planner(model).start("P-104 leak")
    assert (run.status, run.message, model.calls) == ("error", "Couldn't read the request.", 2)


def test_actions_table_rejects_unknown_priority(data_env):
    bad = {
        "asset_id": "P-104",
        "priority": "urgent",
        "title": "t",
        "failure_mode": "f",
        "actions": ["a"],
        "reasoning": "r",
    }
    with pytest.raises(sqlite3.IntegrityError):
        actions.action_work_order(bad, request="x", approved_by="y", thread_id="z")


def test_parameterised_queries_are_still_read_only(data_env):
    result = tools.query_work_orders("DELETE FROM work_orders WHERE asset_id = ?", ("P-104",))
    assert result["error"] == "Only read-only SELECT queries are allowed."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("P-104", "P-104"),
        ("p104", "P-104"),
        ("c 203", "C-203"),
        ("engine 42", "GT-042"),
        ("GT42", "GT-042"),
        ("V-306", "V-306"),
        ("X-1", "X-1"),
    ],
)
def test_normalise_asset_id(raw, expected):
    assert normalise_asset_id(raw) == expected

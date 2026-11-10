"""Work-order planner: a LangGraph workflow that ends in a human approval step.

    START -> triage -> lookup ──(no such asset / out of scope)──> END
                         │
                         ├─> sensors  ┐   evidence specialists, in parallel;
                         ├─> history  ├─  each runs only if it applies
                         └─> manuals  ┘   (sensors: gas turbines; manuals: not valves)
                                 ↓
                               plan -> check -> review ⏸ ──approve──> action -> END
                                                         └──reject──> END

Only two nodes call an LLM:
- triage reads the request (which asset, what symptom, which manual keywords),
- plan drafts the work order from the evidence, with priority and citations.
Everything else is plain code: the evidence specialists call the existing
tools with fixed, parameterised queries, `check` verifies every citation
against the gathered evidence, and `action` is the project's only write.

`review` pauses the graph with `interrupt()`. The state is saved by the
checkpointer under a thread ID; `Planner.resume()` continues it with the
human's decision, from the CLI or the Streamlit app. With LANGSMITH_TRACING
set, every run is traced in LangSmith, grouped by that thread ID.
"""

import json
import operator
import re
import time
import uuid
from collections.abc import Callable
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import traceable
from pydantic import BaseModel, Field, ValidationError

from maintenance_copilot import actions, tools
from maintenance_copilot.llm import get_chat_model, model_name
from maintenance_copilot.prompts import PLANNER_PROMPT, TRIAGE_PROMPT
from maintenance_copilot.tools import sensors
from maintenance_copilot.tools.manual_search import ASSET_TYPES
from maintenance_copilot.trace import CITATION_RE, PlanRun, ToolCall

# --- structured outputs ----------------------------------------------------------------


class Triage(BaseModel):
    """What the maintenance request is about."""

    asset_id: str | None = Field(description="Asset ID as given, e.g. 'P-104'; null if none.")
    symptom: str = Field(description="The reported problem, one short sentence.")
    search_terms: str = Field(description="2-6 technical keywords for the manuals.")
    in_scope: bool = Field(description="False if not a maintenance problem on an asset.")


class WorkOrderDraft(BaseModel):
    """A recommended work order for one asset."""

    asset_id: str
    priority: Literal["high", "medium", "low"]
    title: str
    failure_mode: str = Field(description="Suspected failure mode, a few words.")
    actions: list[str] = Field(description="2-6 ordered steps, each with [source] citations.")
    reasoning: str = Field(description="Why this priority and plan, with [source] citations.")


# --- fixed, parameterised queries for the history specialist ---------------------------

ASSET_SQL = """\
SELECT a.asset_id, a.asset_type, a.model, a.site_id, s.name AS site_name, a.criticality,
       r.failures_12m, r.downtime_hours_12m, r.risk_score
FROM assets a JOIN sites s USING (site_id) LEFT JOIN asset_risk r USING (asset_id)
WHERE a.asset_id = ?"""

HISTORY_SQL = """\
SELECT wo_id, wo_type, priority, failure_mode, opened_date, closed_date, downtime_hours,
       cost_usd, technician_notes
FROM work_orders WHERE asset_id = ? ORDER BY opened_date DESC LIMIT 12"""


def normalise_asset_id(raw: str) -> str:
    """'p104', 'P 104' -> 'P-104'; 'engine 42', 'gt42' -> 'GT-042'."""
    text = raw.strip().upper()
    if match := re.fullmatch(r"([PCV])\s*-?\s*(\d+)", text):
        return f"{match[1]}-{match[2]}"
    if re.search(r"GT|ENGINE|TURBINE", text) or text.isdigit():
        return sensors.normalise_asset_id(text)
    return text


def _call(name: str, args: dict, fn: Callable[[], dict]) -> dict:
    """Run one tool and record it like the Q&A agent does (as a plain dict)."""
    start = time.monotonic()
    result = fn()
    return {"name": name, "args": args, "result": result, "latency_s": time.monotonic() - start}


# --- graph -----------------------------------------------------------------------------


class PlannerState(TypedDict, total=False):
    request: str
    thread_id: str
    triage: dict
    asset: dict
    evidence: Annotated[list[dict], operator.add]  # parallel specialists append here
    draft: dict
    issues: list[str]
    status: str  # declined | rejected | actioned | error (awaiting_approval is derived)
    message: str
    reviewer: str
    action_id: str
    usage: Annotated[list[dict], operator.add]


def _ask_structured(llm, schema: type[BaseModel], system: str, user: str) -> tuple[dict, dict]:
    """Ask the model for one `schema` object. Returns (fields, or {} on failure; token usage).

    The schema is offered as a tool, with tool_choice left on "auto". Forcing
    the tool (what `with_structured_output` does) is rejected by reasoning
    models in thinking mode, e.g. Qwen 3.8 Flash on OpenRouter and Claude with
    extended thinking. If the model answers in prose instead, nudge it once.
    """
    model = llm.bind_tools([schema])
    messages = [
        SystemMessage(f"{system}\n\nRespond by calling the {schema.__name__} tool."),
        HumanMessage(user),
    ]
    usage = {"in": 0, "out": 0}
    for _ in range(2):
        reply = model.invoke(messages)
        tokens = getattr(reply, "usage_metadata", None) or {}
        usage["in"] += tokens.get("input_tokens", 0)
        usage["out"] += tokens.get("output_tokens", 0)
        for call in reply.tool_calls:
            if call["name"] == schema.__name__:
                try:
                    return schema.model_validate(call["args"]).model_dump(), usage
                except ValidationError:
                    break  # malformed arguments: nudge once, like a prose reply
        # Answer any tool calls first: OpenAI-style APIs require a result for each.
        messages += [
            reply,
            *(ToolMessage("Invalid arguments.", tool_call_id=c["id"]) for c in reply.tool_calls),
            HumanMessage(f"Call the {schema.__name__} tool with valid fields."),
        ]
    return {}, usage


def build_graph(llm, checkpointer):
    def triage(state: PlannerState) -> dict:
        parsed, usage = _ask_structured(llm, Triage, TRIAGE_PROMPT, state["request"])
        if not parsed:
            return {"status": "error", "message": "Couldn't read the request.", "usage": [usage]}
        return {"triage": parsed, "usage": [usage]}

    def lookup(state: PlannerState) -> dict:
        t = state["triage"]
        if not t["in_scope"]:
            return {
                "status": "declined",
                "message": "I can only plan maintenance work on our assets. "
                "Describe a problem with a specific asset, e.g. 'P-104 is leaking'.",
            }
        if not t["asset_id"]:
            return {
                "status": "declined",
                "message": "Which asset is it? Give its ID (e.g. P-104, C-203, GT-042) so I "
                "can check its history and condition.",
            }
        aid = normalise_asset_id(t["asset_id"])
        call = _call(
            "query_work_orders",
            {"sql": ASSET_SQL, "params": [aid]},
            lambda: tools.query_work_orders(ASSET_SQL, (aid,)),
        )
        rows = call["result"].get("rows")
        if not rows:
            return {
                "evidence": [call],
                "status": "declined",
                "message": f"There is no asset {aid} in the asset register, so I won't draft a "
                "work order for it. Check the ID and try again.",
            }
        return {
            "evidence": [call],
            "asset": dict(zip(call["result"]["columns"], rows[0], strict=True)),
        }

    def specialists(state: PlannerState) -> list[str]:
        """Fan out to every evidence specialist that applies to this asset."""
        if state.get("status"):
            return [END]
        asset_type = state["asset"]["asset_type"]
        chosen = ["history"]
        if asset_type == "gas_turbine":
            chosen.append("sensors")
        if asset_type in ASSET_TYPES:
            chosen.append("manuals")
        return chosen

    def sensors_node(state: PlannerState) -> dict:
        aid = state["asset"]["asset_id"]
        call = _call("get_sensor_trend", {"asset_id": aid}, lambda: tools.get_sensor_trend(aid))
        return {"evidence": [call]}

    def history(state: PlannerState) -> dict:
        aid = state["asset"]["asset_id"]
        call = _call(
            "query_work_orders",
            {"sql": HISTORY_SQL, "params": [aid]},
            lambda: tools.query_work_orders(HISTORY_SQL, (aid,)),
        )
        return {"evidence": [call]}

    def manuals(state: PlannerState) -> dict:
        args = {
            "query": state["triage"]["search_terms"],
            "asset_type": state["asset"]["asset_type"],
        }
        call = _call("search_manuals", args, lambda: tools.search_manuals(**args))
        return {"evidence": [call]}

    def plan(state: PlannerState) -> dict:
        asset = state["asset"]
        gaps = []
        if asset["asset_type"] != "gas_turbine":
            gaps.append("No sensor data: it exists only for gas turbines.")
        if asset["asset_type"] not in ASSET_TYPES:
            gaps.append(f"No manual exists for asset type '{asset['asset_type']}'.")
        evidence = "\n\n".join(
            f"### {e['name']} {json.dumps(e['args'].get('params') or e['args'])}\n"
            + json.dumps(e["result"], default=str)
            for e in state["evidence"]
        )
        user = (
            f"## Request\n{state['request']}\n\n## Triage\n{json.dumps(state['triage'])}\n\n"
            f"## Asset\n{json.dumps(asset)}\n\n## Evidence gaps\n"
            + ("\n".join(gaps) or "None.")
            + f"\n\n## Evidence\n{evidence}"
        )
        parsed, usage = _ask_structured(llm, WorkOrderDraft, PLANNER_PROMPT, user)
        if not parsed:
            return {"status": "error", "message": "Couldn't draft a work order.", "usage": [usage]}
        return {"draft": parsed, "usage": [usage]}

    def check(state: PlannerState) -> dict:
        """Plain-code guardrails on the draft before a human sees it."""
        draft, issues = dict(state["draft"]), []
        aid = state["asset"]["asset_id"]
        if draft["asset_id"] != aid:
            issues.append(f"Draft named {draft['asset_id']}; corrected to {aid}.")
            draft["asset_id"] = aid
        available = {s for e in state["evidence"] for s in e["result"].get("sources", [])}
        cited = CITATION_RE.findall(" ".join([*draft["actions"], draft["reasoning"]]))
        if not cited:
            issues.append("The draft cites no sources.")
        if ungrounded := sorted(set(cited) - available):
            issues.append(f"Citations not found in the evidence: {', '.join(ungrounded)}.")
        return {"draft": draft, "issues": issues}

    def review(state: PlannerState) -> dict:
        # Pauses here. The value passed to resume() becomes `decision`.
        # Nothing else in this node, because the node re-runs from the top on resume.
        decision = interrupt({"draft": state["draft"], "issues": state["issues"]})
        if decision.get("approved"):
            return {"status": "approved", "reviewer": decision.get("reviewer") or "unknown"}
        return {"status": "rejected", "message": "Rejected by the reviewer; nothing was written."}

    def action(state: PlannerState) -> dict:
        action_id = actions.action_work_order(
            state["draft"],
            request=state["request"],
            approved_by=state["reviewer"],
            thread_id=state["thread_id"],
        )
        return {
            "status": "actioned",
            "action_id": action_id,
            "message": f"Work order {action_id} recorded, approved by {state['reviewer']}.",
        }

    def stop_on_error(next_node: str):
        return lambda state: END if state.get("status") == "error" else next_node

    graph = StateGraph(PlannerState)
    for name, fn in [
        ("triage", triage),
        ("lookup", lookup),
        ("sensors", sensors_node),
        ("history", history),
        ("manuals", manuals),
        ("plan", plan),
        ("check", check),
        ("review", review),
        ("action", action),
    ]:
        graph.add_node(name, fn)
    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", stop_on_error("lookup"), ["lookup", END])
    graph.add_conditional_edges("lookup", specialists, ["sensors", "history", "manuals", END])
    # Specialists run in the same step, so `plan` runs once, after all of them.
    for name in ("sensors", "history", "manuals"):
        graph.add_edge(name, "plan")
    graph.add_conditional_edges("plan", stop_on_error("check"), ["check", END])
    graph.add_edge("check", "review")
    graph.add_conditional_edges(
        "review", lambda s: "action" if s["status"] == "approved" else END, ["action", END]
    )
    graph.add_edge("action", END)
    return graph.compile(checkpointer=checkpointer)


# --- public entry point ----------------------------------------------------------------


class Planner:
    """Start a planning run, then resume it with a human decision.

        planner = Planner()
        run = planner.start("P-104 is leaking again")   # run.status == "awaiting_approval"
        run = planner.resume(run.thread_id, approved=True, reviewer="J. Doe")

    The CLI and Streamlit share this class. Keep one Planner per process: its
    in-memory checkpointer holds paused runs until they are resumed.
    """

    def __init__(self, llm=None, checkpointer=None):
        llm = llm or get_chat_model()
        self.model = model_name(llm)
        self.graph = build_graph(llm, checkpointer or InMemorySaver())

    def start(
        self,
        request: str,
        thread_id: str | None = None,
        on_step: Callable[[str], None] | None = None,
    ) -> PlanRun:
        thread_id = thread_id or uuid.uuid4().hex[:12]
        state = {"request": request, "thread_id": thread_id, "evidence": [], "usage": []}
        return self._traced("work-order-planner", {"request": request}, state, thread_id, on_step)

    def resume(
        self,
        thread_id: str,
        approved: bool,
        reviewer: str = "demo user",
        on_step: Callable[[str], None] | None = None,
    ) -> PlanRun:
        cfg = {"configurable": {"thread_id": thread_id}}
        if "review" not in self.graph.get_state(cfg).next:
            # e.g. the app restarted, which empties the in-memory checkpointer
            return PlanRun(
                request="",
                thread_id=thread_id,
                status="error",
                message="This draft is no longer waiting for approval. Start a new plan.",
                model=self.model,
            )
        decision = Command(resume={"approved": approved, "reviewer": reviewer})
        inputs = {"decision": "approve" if approved else "reject", "reviewer": reviewer}
        return self._traced("work-order-planner: review", inputs, decision, thread_id, on_step)

    def _traced(self, name: str, inputs: dict, graph_input, thread_id: str, on_step) -> PlanRun:
        extra = {
            "name": name,
            "tags": ["planner", f"model:{self.model}"],
            # LangSmith groups runs that share thread_id metadata into one thread,
            # so a draft and its later approval appear together.
            "metadata": {"thread_id": thread_id, "model": self.model},
        }
        return _trace_root(
            inputs, lambda: self._run(graph_input, thread_id, on_step), langsmith_extra=extra
        )

    def _run(self, graph_input, thread_id: str, on_step) -> PlanRun:
        cfg = {"configurable": {"thread_id": thread_id}, "run_name": "planner-graph"}
        start = time.monotonic()
        error = None
        try:
            for update in self.graph.stream(graph_input, cfg, stream_mode="updates"):
                if on_step:
                    for node in update:
                        if not node.startswith("__"):
                            on_step(node)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        snapshot = self.graph.get_state(cfg)
        values = snapshot.values
        if error:
            status, message = "error", error
        elif "review" in snapshot.next:
            status, message = "awaiting_approval", "Draft ready for review."
        else:
            status, message = values.get("status", "error"), values.get("message", "")
        usage = values.get("usage", [])
        return PlanRun(
            request=values.get("request", ""),
            thread_id=thread_id,
            status=status,
            message=message,
            triage=values.get("triage"),
            asset=values.get("asset"),
            evidence=[ToolCall(**e) for e in values.get("evidence", [])],
            draft=values.get("draft"),
            issues=values.get("issues", []),
            action_id=values.get("action_id"),
            model=self.model,
            input_tokens=sum(u["in"] for u in usage),
            output_tokens=sum(u["out"] for u in usage),
            latency_s=time.monotonic() - start,
        )


# --- LangSmith -------------------------------------------------------------------------


def _trace_summary(run: PlanRun) -> dict:
    """What the trace list shows as a planner run's output."""
    draft = run.draft or {}
    return {
        "status": run.status,
        "message": run.message,
        "asset_id": draft.get("asset_id") or (run.asset or {}).get("asset_id"),
        "priority": draft.get("priority"),
        "title": draft.get("title"),
        "issues": run.issues,
        "action_id": run.action_id,
    }


@traceable(
    run_type="chain",
    process_inputs=lambda args: args["inputs"],
    process_outputs=_trace_summary,
)
def _trace_root(inputs: dict, execute: Callable[[], PlanRun]) -> PlanRun:
    """A LangSmith root run with readable inputs and outputs; the graph is traced inside it.

    Without it, a paused run's output is its raw state and a resume's input is
    LangGraph's internal Command object. When tracing is off this is a no-op.
    """
    return execute()

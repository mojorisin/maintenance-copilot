"""The LangGraph agent: an explicit two-node loop.

    START -> agent --(tool calls?)--> tools -> agent -> ... -> END

The `agent` node asks the model what to do next; the `tools` node runs the
requested tools and records each call for the UI and evals. The loop ends
when the model answers without calling a tool, or after MAX_TOOL_ROUNDS.
Written as a StateGraph rather than a prebuilt agent so every step is
visible and controllable.
"""

import json
import operator
import time
from collections.abc import Callable
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from maintenance_copilot import tools
from maintenance_copilot.llm import get_chat_model, model_name
from maintenance_copilot.prompts import SYSTEM_PROMPT
from maintenance_copilot.tools import descriptions as desc
from maintenance_copilot.trace import AgentRun, ToolCall

MAX_TOOL_ROUNDS = 8


# --- tool definitions the model sees -------------------------------------------------


def _query_work_orders(sql: str) -> dict:
    return tools.query_work_orders(sql)


def _get_sensor_trend(asset_id: str | None = None) -> dict:
    return tools.get_sensor_trend(asset_id)


def _search_manuals(query: str, asset_type: str | None = None) -> dict:
    return tools.search_manuals(query, asset_type)


TOOLS = [
    StructuredTool.from_function(
        _query_work_orders, name="query_work_orders", description=desc.QUERY_WORK_ORDERS
    ),
    StructuredTool.from_function(
        _get_sensor_trend, name="get_sensor_trend", description=desc.GET_SENSOR_TREND
    ),
    StructuredTool.from_function(
        _search_manuals, name="search_manuals", description=desc.SEARCH_MANUALS
    ),
]


# --- graph ---------------------------------------------------------------------------


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_log: Annotated[list[ToolCall], operator.add]
    rounds: int


def build_graph(llm=None, tool_list: list[StructuredTool] | None = None):
    """Compile the agent graph around a tool-calling chat model.

    `tool_list` defaults to the direct, in-process tools; the MCP parity eval
    passes tools that call the same functions through an MCP server instead.
    """
    llm = llm or get_chat_model()
    tool_list = tool_list or TOOLS
    tools_by_name = {t.name: t for t in tool_list}
    model = llm.bind_tools(tool_list)
    system = SystemMessage(SYSTEM_PROMPT)

    def agent_node(state: State) -> dict:
        return {"messages": [model.invoke([system, *state["messages"]])]}

    def tools_node(state: State) -> dict:
        last = state["messages"][-1]
        messages, log = [], []
        for call in last.tool_calls:
            start = time.monotonic()
            tool = tools_by_name.get(call["name"])
            try:
                result = tool.invoke(call["args"]) if tool else {"error": "Unknown tool."}
            except Exception as exc:  # bad arguments etc.: let the model retry
                result = {"error": f"{type(exc).__name__}: {exc}"}
            log.append(ToolCall(call["name"], call["args"], result, time.monotonic() - start))
            messages.append(ToolMessage(json.dumps(result, default=str), tool_call_id=call["id"]))
        return {"messages": messages, "tool_log": log, "rounds": state["rounds"] + 1}

    def route(state: State) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None) and state["rounds"] < MAX_TOOL_ROUNDS:
            return "tools"
        return END

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, ["tools", END])
    graph.add_edge("tools", "agent")
    return graph.compile(), model_name(llm)


# --- public entry point --------------------------------------------------------------


def _usage(messages: list[AnyMessage]) -> tuple[int, int, int]:
    tokens_in = tokens_out = cache_read = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None) or {}
        tokens_in += usage.get("input_tokens", 0)
        tokens_out += usage.get("output_tokens", 0)
        cache_read += (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
    return tokens_in, tokens_out, cache_read


def _final_answer(last: AIMessage) -> str:
    if last.response_metadata.get("stop_reason") == "refusal":
        return "The model declined to answer this request."
    if last.tool_calls:
        return "I couldn't finish this within the tool-call limit. Try a narrower question."
    return last.text


def build_agent(llm=None, tool_list: list[StructuredTool] | None = None):
    """Return `ask(question, history) -> AgentRun`, used by the CLI, UI and evals.

    `history` is earlier (question, answer) pairs, so the chat UI can handle
    follow-ups like "what about P-109?". Only the text of earlier turns is
    sent, not their tool results: that keeps context small, and the agent
    re-queries when it needs data again. Evals always ask with no history.

    `on_tool_start(name, args)` is called as each tool call is decided, so
    a UI can show progress live instead of a bare spinner.
    """
    graph, name = build_graph(llm, tool_list)

    def ask(
        question: str,
        history: list[tuple[str, str]] | None = None,
        on_tool_start: Callable[[str, dict], None] | None = None,
    ) -> AgentRun:
        start = time.monotonic()
        run = AgentRun(question=question, model=name)
        messages: list[AnyMessage] = []
        for q, a in history or []:
            messages += [HumanMessage(q), AIMessage(a)]
        messages.append(HumanMessage(question))
        try:
            state = None
            # stream_mode="values" yields the full state after every step;
            # the last one is the final state.
            for state in graph.stream(
                {"messages": messages, "tool_log": [], "rounds": 0},
                {"recursion_limit": 2 * MAX_TOOL_ROUNDS + 3},
                stream_mode="values",
            ):
                last = state["messages"][-1]
                if on_tool_start and isinstance(last, AIMessage) and last.tool_calls:
                    for call in last.tool_calls:
                        on_tool_start(call["name"], call["args"])
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            run.latency_s = time.monotonic() - start
            return run
        run.answer = _final_answer(state["messages"][-1])
        run.tool_calls = state["tool_log"]
        run.input_tokens, run.output_tokens, run.cache_read_tokens = _usage(state["messages"])
        run.latency_s = time.monotonic() - start
        return run

    return ask

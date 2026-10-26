"""Streamlit chat UI: `uv run mc app` (or `uv run streamlit run src/maintenance_copilot/app.py`).

Each answer has a "How I got this" panel showing every tool call, its
inputs and outputs, the sources it returned, and the run's latency,
tokens and cost, so a user can check the answer instead of trusting it.
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Hosted deployments (e.g. Streamlit Community Cloud) run this file directly:
# 1. make the package importable from src/ even if it isn't installed, and
# 2. expose Streamlit secrets as environment variables, since settings and
#    API keys are read from the environment.
# Both must happen before the maintenance_copilot imports below.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    for _key, _value in st.secrets.items():
        if isinstance(_value, str | int | float | bool):
            os.environ.setdefault(_key, str(_value))
except Exception:  # no secrets configured (normal for local runs with .env)
    pass

from maintenance_copilot import config  # noqa: E402
from maintenance_copilot.evaluation import run_cost  # noqa: E402
from maintenance_copilot.llm import ProviderError  # noqa: E402
from maintenance_copilot.trace import AgentRun, ToolCall  # noqa: E402

EXAMPLES = [
    "Which pumps had repeat failures in the last quarter?",
    "Engine 42's sensors are drifting. How much useful life is left, and what should we check?",
    "What does the manual say about a high-vibration alarm on our gas turbines?",
    "Which 5 assets should we prioritise for maintenance next month, and why?",
    "Give me a status summary for valve V-306, including the latest technician notes.",
    "Delete all work orders for the Riverbend site.",
]
TOOL_LABELS = {
    "query_work_orders": "🗄️ Work-order database (SQL)",
    "get_sensor_trend": "📈 Sensor data and remaining-life model",
    "search_manuals": "📘 Equipment manuals",
}

st.set_page_config(page_title="Maintenance Copilot", page_icon="🛠️", layout="wide")


@st.cache_resource(show_spinner=False)
def get_agent():
    from maintenance_copilot.agent import build_agent

    return build_agent()


@st.cache_resource(show_spinner=False)
def ensure_data() -> bool:
    """Build the git-ignored data folder on first launch (hosted deployments)."""
    from maintenance_copilot.data.build import build_all, data_ready

    if not data_ready():
        build_all(log=lambda _: None)
    return True


def describe_step(name: str, args: dict) -> str:
    """One-line, human-readable progress message for a tool call in flight."""
    if name == "query_work_orders":
        return "🗄️ Querying the work-order database…"
    if name == "get_sensor_trend":
        target = args.get("asset_id")
        return f"📈 Checking sensor data for {target}…" if target else "📈 Reviewing turbine fleet…"
    if name == "search_manuals":
        return f"📘 Searching manuals for “{args.get('query', '')}”…"
    return f"Running {name}…"


# --- "How I got this" ----------------------------------------------------------------


def render_call(i: int, call: ToolCall) -> None:
    st.markdown(f"**{i}. {TOOL_LABELS.get(call.name, call.name)}** · {call.latency_s:.2f}s")
    result = call.result
    if "error" in result:
        st.code(json.dumps(call.args, indent=2), language="json")
        st.warning(f"Tool returned an error, which the agent saw: {result['error']}")
        return

    if call.name == "query_work_orders":
        st.code(call.args.get("sql", ""), language="sql")
        df = pd.DataFrame(result["rows"], columns=result["columns"])
        st.dataframe(df, hide_index=True, width="stretch", height=min(35 * (len(df) + 1) + 3, 280))
        if result.get("truncated"):
            st.caption("Result truncated to the first 200 rows.")

    elif call.name == "get_sensor_trend":
        if "fleet" in result:
            st.caption("Fleet overview, most urgent first")
            st.dataframe(pd.DataFrame(result["fleet"]), hide_index=True, width="stretch")
        else:
            low, high = result["rul_range_cycles"]
            c1, c2, c3 = st.columns(3)
            c1.metric("Estimated remaining life", f"{result['rul_estimate_cycles']} cycles")
            c2.metric("Range", f"{low}–{high} cycles")
            c3.metric("Status", result["status"].upper())
            tags = [s["sensor"] for s in result["top_drifting_sensors"][:3]]
            try:
                from maintenance_copilot.tools.sensors import drift_series

                st.caption("Sensor drift from this engine's early-life baseline (× normal noise)")
                st.line_chart(drift_series(result["asset_id"], tags), height=220)
            except Exception:  # the chart is a nice-to-have; never break the trace
                pass
            st.dataframe(
                pd.DataFrame(result["top_drifting_sensors"]), hide_index=True, width="stretch"
            )
            st.caption(result["method"])

    elif call.name == "search_manuals":
        st.caption(
            f"Query: “{call.args.get('query', '')}”  ·  asset type: "
            f"{call.args.get('asset_type') or 'any'}"
        )
        for hit in result.get("results", []):
            with st.container(border=True):
                st.markdown(f"`{hit['id']}` · score {hit['score']}")
                st.markdown(hit["text"][:600] + ("…" if len(hit["text"]) > 600 else ""))
        if not result.get("results"):
            st.caption("No manual section matched.")

    sources = result.get("sources", [])
    if sources:
        shown = ", ".join(f"`{s}`" for s in sources[:12])
        st.caption(f"Sources returned: {shown}" + (" …" if len(sources) > 12 else ""))


def render_trace(run: AgentRun) -> None:
    n = len(run.tool_calls)
    with st.expander(f"How I got this · {n} tool call{'s' if n != 1 else ''}"):
        if not run.tool_calls:
            st.caption("Answered without calling any tools.")
        for i, call in enumerate(run.tool_calls, 1):
            render_call(i, call)
            if i < n:
                st.divider()
        cited, available = run.citations(), run.available_sources()
        ungrounded = [c for c in cited if c not in available]
        cost = run_cost(run)
        st.divider()
        st.caption(
            f"{run.model} · {run.latency_s:.1f}s · {run.input_tokens:,} input tokens "
            f"({run.cache_read_tokens:,} cached) · {run.output_tokens:,} output tokens"
            + (f" · ${cost:.4f}" if cost is not None else "")
            + f" · {len(cited)} citations, "
            + (
                "all grounded in tool output ✅"
                if not ungrounded
                else f"ungrounded: {ungrounded} ⚠️"
            )
        )


# --- page ----------------------------------------------------------------------------

st.title("🛠️ Maintenance Copilot")
st.caption(
    f"Answers questions about maintenance history, equipment condition and manuals for four "
    f"sites. Data as of {config.AS_OF_DATE:%d %b %Y}: synthetic work orders, NASA C-MAPSS "
    f"sensor data and synthetic manuals."
)

if "turns" not in st.session_state:
    st.session_state.turns = []  # list[AgentRun]

with st.sidebar:
    st.subheader("Try asking")
    for q in EXAMPLES:
        if st.button(q, width="stretch"):
            st.session_state.pending = q
    st.divider()
    effort = f", effort {config.LLM_EFFORT}" if config.LLM_PROVIDER == "anthropic" else ""
    st.caption(f"Model: {config.LLM_PROVIDER} · {config.LLM_MODEL or 'claude-opus-5-5'}{effort}")
    st.caption("Read-only access. Every claim cites its source; open “How I got this” to check.")
    limit = config.SESSION_QUESTION_LIMIT
    if limit:
        left = max(limit - len(st.session_state.turns), 0)
        st.caption(f"Public demo: {left} of {limit} questions left in this session.")
    if st.button("Clear conversation", width="stretch"):
        st.session_state.turns = []
        st.rerun()

try:
    with st.spinner("Preparing data on first launch (a few seconds)…"):
        ensure_data()
except Exception as exc:
    st.error(f"Couldn't prepare the data ({exc}). Locally, run `uv run mc setup`.")
    st.stop()

for run in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(run.question)
    with st.chat_message("assistant"):
        st.markdown(run.answer or f"⚠️ {run.error}")
        render_trace(run)

question = st.chat_input("Ask about assets, failures, sensors or manuals…")
question = question or st.session_state.pop("pending", None)

if question and limit and len(st.session_state.turns) >= limit:
    st.info(
        f"This public demo allows {limit} questions per session to keep API costs bounded. "
        "Clone the repo to run it with your own key: see the README quickstart."
    )
    question = None

if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            agent = get_agent()
        except ProviderError as exc:
            st.error(str(exc))
            st.stop()
        history = [(r.question, r.answer) for r in st.session_state.turns if r.answer]
        with st.status("Investigating…", expanded=True) as status:
            run = agent(
                question,
                history=history,
                on_tool_start=lambda name, args: status.write(describe_step(name, args)),
            )
            n = len(run.tool_calls)
            status.update(
                label=f"Done: {n} tool call{'s' if n != 1 else ''} in {run.latency_s:.0f}s",
                state="error" if run.error else "complete",
                expanded=False,
            )
        st.markdown(run.answer or f"⚠️ {run.error}")
        render_trace(run)
    st.session_state.turns.append(run)

"""Streamlit "Plan a work order" mode, rendered by app.py.

A request starts a planner run, which pauses at the review step. The draft
is shown with its evidence and guardrail warnings, and the Approve / Reject
buttons resume the same run (same thread ID) with the human's decision.
"""

from collections.abc import Callable

import pandas as pd
import streamlit as st

from maintenance_copilot.actions import list_actioned
from maintenance_copilot.evaluation import run_cost
from maintenance_copilot.llm import ProviderError
from maintenance_copilot.trace import PlanRun, ToolCall

EXAMPLES = [
    "P-104 is leaking from the seal again. What should we do?",
    "Engine 42 is vibrating more than usual. What should we do?",
    "Compressor C-203 is knocking and the discharge temperature is up.",
    "Pump P-204 is vibrating more than usual. What should we do?",
]
# Shown as each graph node finishes.
STEP_LABELS = {
    "triage": "🧭 Read the request",
    "lookup": "🔎 Looked up the asset",
    "history": "🗄️ Collected the work-order history",
    "sensors": "📈 Checked sensor data and remaining life",
    "manuals": "📘 Searched the equipment manuals",
    "plan": "📝 Drafted a work order",
    "check": "✅ Checked every citation against the evidence",
    "review": "👤 Recorded your decision",
    "action": "💾 Saved the approved work order",
}
PRIORITY_BADGES = {"high": "🔴 HIGH", "medium": "🟠 MEDIUM", "low": "🟢 LOW"}


def _render_draft(run: PlanRun) -> None:
    d = run.draft
    with st.container(border=True):
        st.markdown(f"**{PRIORITY_BADGES[d['priority']]} priority · {d['asset_id']}**")
        st.markdown(f"#### {d['title']}")
        st.caption(f"Suspected failure mode: {d['failure_mode']}")
        st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(d["actions"], 1)))
        st.markdown(f"**Why:** {d['reasoning']}")
    for issue in run.issues:
        st.warning(f"Check: {issue}")


def _render_evidence(run: PlanRun, render_call: Callable[[int, ToolCall], None]) -> None:
    n = len(run.evidence)
    with st.expander(f"Evidence · {n} tool call{'s' if n != 1 else ''}"):
        for i, call in enumerate(run.evidence, 1):
            render_call(i, call)
            if i < n:
                st.divider()
        cost = run_cost(run)
        st.divider()
        st.caption(
            f"{run.model} · {run.latency_s:.1f}s · {run.input_tokens:,} input tokens · "
            f"{run.output_tokens:,} output tokens"
            + (f" · ${cost:.4f}" if cost is not None else "")
            + f" · thread {run.thread_id}"
        )


def _render_review(index: int, run: PlanRun, get_planner: Callable) -> None:
    """Approve / Reject buttons for a paused run. A click resumes the graph."""
    reviewer = st.text_input(
        "Your name (recorded with an approval)", value="demo user", key=f"reviewer-{index}"
    )
    approve, reject = st.columns(2)
    decision = None
    if approve.button("✅ Approve and record", key=f"approve-{index}", width="stretch"):
        decision = True
    if reject.button("✖️ Reject", key=f"reject-{index}", width="stretch"):
        decision = False
    if decision is not None:
        with st.spinner("Recording your decision…"):
            done = get_planner().resume(run.thread_id, approved=decision, reviewer=reviewer)
        done.latency_s = run.latency_s  # keep the planning time, not the resume time
        st.session_state.plans[index] = done
        st.rerun()


def render_plan(
    index: int, run: PlanRun, render_call: Callable[[int, ToolCall], None], get_planner: Callable
) -> None:
    if run.draft:
        _render_draft(run)
    if run.evidence:
        _render_evidence(run, render_call)
    if run.status == "awaiting_approval":
        st.caption("Nothing is written until you approve. The work-order history stays read-only.")
        _render_review(index, run, get_planner)
    elif run.status == "actioned":
        st.success(run.message)
    elif run.status in ("rejected", "declined"):
        st.info(run.message)
    else:
        st.error(run.message or "Something went wrong.")


def render_page(
    get_planner: Callable,
    render_call: Callable[[int, ToolCall], None],
    pending: str | None,
    limit_reached: bool,
) -> None:
    """The whole planner page: earlier runs, the input box and approved orders."""
    plans: list[PlanRun] = st.session_state.setdefault("plans", [])
    st.caption(
        "Describe a problem with an asset. Specialists gather evidence, a planner drafts a work "
        "order with citations, and you decide whether it is recorded."
    )
    for i, run in enumerate(plans):
        with st.chat_message("user"):
            st.markdown(run.request or "(request)")
        with st.chat_message("assistant"):
            render_plan(i, run, render_call, get_planner)

    request = st.chat_input("Describe a maintenance problem, e.g. “P-104 is leaking again”…")
    request = request or pending
    if request and limit_reached:
        st.info(
            "This public demo allows a limited number of requests per session to keep API costs "
            "bounded. Clone the repo to run it with your own key: see the README quickstart."
        )
        request = None
    if request:
        with st.chat_message("user"):
            st.markdown(request)
        with st.chat_message("assistant"):
            try:
                planner = get_planner()
            except ProviderError as exc:
                st.error(str(exc))
                st.stop()
            with st.status("Gathering evidence…", expanded=True) as status:

                def on_step(node: str) -> None:
                    status.write(STEP_LABELS.get(node, node))
                    # Evidence specialists finish together; the slow LLM step comes next.
                    if node in ("history", "sensors", "manuals"):
                        status.update(
                            label="📝 Drafting the work order… reasoning models can take "
                            "1–2 minutes"
                        )

                run = planner.start(request, on_step=on_step)
                status.update(
                    label=f"Done in {run.latency_s:.0f}s",
                    state="error" if run.status == "error" else "complete",
                    expanded=False,
                )
        plans.append(run)
        st.rerun()  # draw it with the others, so its buttons keep a stable key

    threads = {r.thread_id for r in plans}
    # Only this session's approvals: the demo is public, and other visitors'
    # requests should not be shown to everyone.
    approved = [r for r in list_actioned() if r["thread_id"] in threads]
    if approved:
        with st.expander(f"Work orders approved in this session · {len(approved)}"):
            columns = ["action_id", "asset_id", "priority", "title", "approved_by", "approved_at"]
            st.dataframe(pd.DataFrame(approved)[columns], hide_index=True, width="stretch")

"""Command-line entry point (`mc`).

mc setup   build local data: work orders, C-MAPSS download, RUL model, ground truth
mc eval    run the evaluation suite (`--agent null` for the do-nothing baseline)
mc ask     ask the agent a question from the terminal
mc plan    draft a work order for a reported problem, then approve or reject it
mc app     launch the Streamlit chat UI
mc mcp     serve the tools, resources and prompts over MCP
"""

import argparse
from pathlib import Path

from maintenance_copilot import __version__, config


def cmd_setup(args: argparse.Namespace) -> int:
    """Build every local artefact the agent and evals need. Safe to re-run."""
    from maintenance_copilot.data.build import build_all

    truth = build_all(seed=args.seed)
    print("\nHeadlines the agent should be able to find:")
    print(f"  problem site: {truth['problem_site']['name']}")
    pumps = ", ".join(truth["pumps_with_repeat_failures_last_quarter"])
    print(f"  pumps with repeat failures last quarter: {pumps}")
    print(f"  turbines flagged critical by RUL model: {', '.join(truth['critical_turbines'])}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Run the eval suite and write results to evals/results/."""
    import json

    from maintenance_copilot import evaluation

    if not config.GROUND_TRUTH_FILE.exists():
        print("No ground truth found. Run `mc setup` first.")
        return 1
    truth = json.loads(config.GROUND_TRUTH_FILE.read_text())
    if args.suite == "planner":
        return eval_planner(args, truth)
    cases = evaluation.load_cases()
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    label = args.label

    if args.rescore:
        if label is None:
            print("--rescore needs --label, e.g. --label effort-low")
            return 1
        print(f"Re-scoring stored answers in {args.rescore} (no API calls)...")
        suite = evaluation.rescore(args.rescore, cases, truth)
    else:
        import contextlib

        with contextlib.ExitStack() as stack:
            if args.agent == "null":
                agent = evaluation.null_agent
            else:
                from maintenance_copilot.agent import build_agent

                tool_list = None
                if args.agent == "copilot-mcp":
                    # Same agent, but every tool call travels over MCP to a real
                    # `mc mcp` subprocess on stdio, as Claude Desktop would use it.
                    import sys

                    from mcp import StdioServerParameters

                    from maintenance_copilot.mcp_bridge import MCPToolBridge

                    server = StdioServerParameters(
                        command=sys.executable, args=["-m", "maintenance_copilot.cli", "mcp"]
                    )
                    tool_list = stack.enter_context(MCPToolBridge(server)).langchain_tools()
                agent = build_agent(tool_list=tool_list)
            print(f"Running {len(cases)} cases x {args.trials} trials, {args.agent} agent...")
            suite = evaluation.run_suite(
                agent, cases, truth, trials=args.trials, workers=args.workers
            )
        if label is None and args.agent != "null" and config.LLM_PROVIDER == "anthropic":
            label = f"effort-{config.LLM_EFFORT}" + ("-mcp" if args.agent == "copilot-mcp" else "")
    md, log = evaluation.save(
        suite,
        config.REPO_ROOT / "evals" / "results",
        config.REPO_ROOT / "evals" / "runs",
        label=label or "",
    )
    print(md.read_text())
    print(f"Summary: {md}\nFull run log: {log}")
    return 0


def eval_planner(args: argparse.Namespace, truth: dict) -> int:
    """`mc eval --suite planner`: score drafted work orders against evals/planner_cases.toml."""
    from maintenance_copilot import planner_eval
    from maintenance_copilot.trace import PlanRun

    cases = planner_eval.load_cases()
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    # Refuse to spend money on an eval set that disagrees with the rubric or the data.
    if problems := planner_eval.validate_cases(cases, truth):
        print("The eval set disagrees with the priority rubric:\n  " + "\n  ".join(problems))
        return 1
    if args.rescore:
        print(f"Re-scoring stored drafts in {args.rescore} (no API calls)...")
        suite = planner_eval.rescore(args.rescore, cases)
    else:
        if args.agent == "null":

            def start(request: str) -> PlanRun:  # declines everything: the floor
                return PlanRun(request, "", "declined", "null planner", model="null-planner")
        else:
            from maintenance_copilot.planner import Planner

            start = Planner().start
        print(f"Running {len(cases)} planner cases x {args.trials} trials, {args.agent}...")
        suite = planner_eval.run_suite(start, cases, trials=args.trials, workers=args.workers)
    md, log = planner_eval.save(
        suite,
        config.REPO_ROOT / "evals" / "results",
        config.REPO_ROOT / "evals" / "runs",
        label=args.label or "",
    )
    print(md.read_text())
    print(f"Summary: {md}\nFull run log: {log}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Ask the agent one question and print the answer and its tool trace."""
    import json

    from maintenance_copilot.agent import build_agent
    from maintenance_copilot.evaluation import run_cost

    run = build_agent()(args.question)
    if run.error:
        print(f"Error: {run.error}")
        return 1
    print(run.answer)
    print("\n--- how I got this ---")
    for i, call in enumerate(run.tool_calls, 1):
        print(f"{i}. {call.name}({json.dumps(call.args)})")
        if "error" in call.result:
            print(f"   error: {call.result['error']}")
        else:
            print(f"   sources: {', '.join(call.result.get('sources', [])[:6])}")
    cost = run_cost(run)
    print(
        f"\n{run.model} · {run.latency_s:.1f}s · {run.input_tokens:,} in / "
        f"{run.output_tokens:,} out tokens" + (f" · ${cost:.4f}" if cost is not None else "")
    )
    return 0


def print_plan(run) -> None:
    """Show a planner run: what it looked at, the draft, and any guardrail issues."""
    if run.triage:
        print(f"Triage: asset {run.triage['asset_id']!r}, symptom: {run.triage['symptom']}")
    for call in run.evidence:
        sources = call.result.get("sources", [])
        detail = call.result.get("error") or f"{len(sources)} sources: {', '.join(sources[:5])}"
        print(f"  evidence · {call.name}: {detail}")
    if run.draft:
        d = run.draft
        print(f"\nDraft work order · {d['asset_id']} · priority {d['priority'].upper()}")
        print(f"  {d['title']}\n  Suspected failure mode: {d['failure_mode']}")
        for i, step in enumerate(d["actions"], 1):
            print(f"  {i}. {step}")
        print(f"  Why: {d['reasoning']}")
    for issue in run.issues:
        print(f"  ⚠ {issue}")
    print(f"\n[{run.status}] {run.message}")
    print(
        f"{run.model} · {run.latency_s:.1f}s · {run.input_tokens:,} in / "
        f"{run.output_tokens:,} out tokens · thread {run.thread_id}"
    )


def cmd_plan(args: argparse.Namespace) -> int:
    """Draft a work order, pause for a human decision, then record it or not."""
    from maintenance_copilot.planner import Planner

    planner = Planner()
    run = planner.start(args.request, on_step=lambda node: print(f"… {node}"))
    print_plan(run)
    if run.status != "awaiting_approval":
        return 0 if run.status == "declined" else 1
    if args.decision:
        approved = args.decision == "approve"
    else:
        approved = input("\nApprove this work order? [y/N] ").strip().lower() in ("y", "yes")
    run = planner.resume(run.thread_id, approved=approved, reviewer=args.reviewer)
    print(f"\n[{run.status}] {run.message}")
    return 0 if run.status in ("actioned", "rejected") else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """Serve the tools, resources and prompts over MCP.

    stdout is the protocol channel in stdio mode, so all messages go to stderr.
    HTTP binds to 127.0.0.1 only; public hosting needs authentication (planned).
    """
    import sys

    from maintenance_copilot.data.build import build_all, data_ready

    if not data_ready():
        build_all(log=lambda msg: print(msg, file=sys.stderr))
    from maintenance_copilot.mcp_server import mcp

    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        print(f"MCP server on http://127.0.0.1:{args.port}/mcp (local only)", file=sys.stderr)
        mcp.run("streamable-http", host="127.0.0.1", port=args.port)
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    """Launch the Streamlit chat UI."""
    import subprocess
    import sys

    app = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port)]
    return subprocess.call(cmd)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="mc", description="Maintenance Copilot CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="build local data, RUL model and ground truth")
    setup.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    setup.set_defaults(func=cmd_setup)

    mcp = sub.add_parser("mcp", help="serve the tools over MCP (Claude Desktop, Claude Code...)")
    mcp.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    mcp.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    mcp.set_defaults(func=cmd_mcp)

    app = sub.add_parser("app", help="launch the Streamlit chat UI")
    app.add_argument("--port", type=int, default=8501)
    app.set_defaults(func=cmd_app)

    ask = sub.add_parser("ask", help="ask the agent a question")
    ask.add_argument("question")
    ask.set_defaults(func=cmd_ask)

    plan = sub.add_parser("plan", help="draft a work order and approve or reject it")
    plan.add_argument("request", help='e.g. "P-104 is leaking again, what should we do?"')
    plan.add_argument(
        "--approve",
        dest="decision",
        action="store_const",
        const="approve",
        help="approve without asking (scripts)",
    )
    plan.add_argument(
        "--reject",
        dest="decision",
        action="store_const",
        const="reject",
        help="reject without asking (scripts)",
    )
    plan.add_argument("--reviewer", default="cli user", help="name recorded on approval")
    plan.set_defaults(func=cmd_plan)

    ev = sub.add_parser("eval", help="run the evaluation suite")
    ev.add_argument(
        "--agent",
        choices=["copilot", "copilot-mcp", "null"],
        default="copilot",
        help="copilot-mcp: same agent, tools served over MCP (stdio subprocess)",
    )
    ev.add_argument(
        "--suite",
        choices=["qa", "planner"],
        default="qa",
        help="qa: question answering (evals/cases.toml); planner: work-order drafts "
        "(evals/planner_cases.toml)",
    )
    ev.add_argument("--trials", type=int, default=3, help="runs per case (default: 3)")
    ev.add_argument("--workers", type=int, default=4, help="parallel runs (default: 4)")
    ev.add_argument("--case", action="append", help="only run this case id (repeatable)")
    ev.add_argument("--label", help="suffix for result files (default: effort-<LLM_EFFORT>)")
    ev.add_argument(
        "--rescore",
        type=Path,
        metavar="LOG",
        help="re-score a stored run log with the current checks (free; scorer changes only)",
    )
    ev.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the chosen subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

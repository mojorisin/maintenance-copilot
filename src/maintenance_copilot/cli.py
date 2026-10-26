"""Command-line entry point (`mc`).

mc setup   build local data: work orders, C-MAPSS download, RUL model, ground truth
mc eval    run the evaluation suite (`--agent null` for the do-nothing baseline)
mc ask     ask the agent a question from the terminal
"""

import argparse

from maintenance_copilot import __version__, config


def cmd_setup(args: argparse.Namespace) -> int:
    """Build every local artefact the agent and evals need. Safe to re-run."""
    from maintenance_copilot.data import cmapss
    from maintenance_copilot.data.ground_truth import (
        add_derived_facts,
        compute_ground_truth,
        turbine_facts,
        write_ground_truth,
    )
    from maintenance_copilot.data.work_orders import generate
    from maintenance_copilot.rul import RULModel, evaluate_on_test

    print("1/4 Generating synthetic work orders...")
    n = generate(config.WORK_ORDERS_DB, seed=args.seed)
    print(f"    {n} work orders -> {config.WORK_ORDERS_DB}")

    print("2/4 Fetching NASA C-MAPSS FD001 (about 12 MB, first run only)...")
    cmapss.fetch_fd001(config.CMAPSS_DIR)
    train = cmapss.load_fd001("train", config.CMAPSS_DIR)
    test = cmapss.load_fd001("test", config.CMAPSS_DIR)
    true_rul = cmapss.load_test_rul(config.CMAPSS_DIR)

    print("3/4 Training the RUL baseline...")
    model = RULModel.fit(train)
    model.test_metrics = evaluate_on_test(model, test, true_rul)
    model.save(config.RUL_MODEL_FILE)
    m = model.test_metrics
    print(f"    test RMSE {m['rmse']} cycles, MAE {m['mae']} on {m['engines']} NASA test engines")

    print("4/4 Computing eval ground truth...")
    truth = compute_ground_truth(config.WORK_ORDERS_DB)
    truth["turbines"] = turbine_facts(model, test, true_rul, truth["turbine_cmapss_map"])
    truth["rul_model_test_metrics"] = model.test_metrics
    add_derived_facts(truth)
    write_ground_truth(truth, config.GROUND_TRUTH_FILE)
    print(f"    -> {config.GROUND_TRUTH_FILE}")

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
    cases = evaluation.load_cases()
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    if args.agent == "null":
        agent = evaluation.null_agent
    else:
        from maintenance_copilot.agent import build_agent

        agent = build_agent()

    print(f"Running {len(cases)} cases x {args.trials} trials with the {args.agent} agent...")
    suite = evaluation.run_suite(agent, cases, truth, trials=args.trials, workers=args.workers)
    md, log = evaluation.save(
        suite, config.REPO_ROOT / "evals" / "results", config.REPO_ROOT / "evals" / "runs"
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


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="mc", description="Maintenance Copilot CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="build local data, RUL model and ground truth")
    setup.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    setup.set_defaults(func=cmd_setup)

    ask = sub.add_parser("ask", help="ask the agent a question")
    ask.add_argument("question")
    ask.set_defaults(func=cmd_ask)

    ev = sub.add_parser("eval", help="run the evaluation suite")
    ev.add_argument("--agent", choices=["copilot", "null"], default="copilot")
    ev.add_argument("--trials", type=int, default=3, help="runs per case (default: 3)")
    ev.add_argument("--workers", type=int, default=4, help="parallel runs (default: 4)")
    ev.add_argument("--case", action="append", help="only run this case id (repeatable)")
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

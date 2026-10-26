"""Command-line entry point (`mc`).

mc setup   build the work-order database and eval ground truth
mc ask     ask the agent a question from the terminal (coming soon)
"""

import argparse

from maintenance_copilot import __version__, config


def cmd_setup(args: argparse.Namespace) -> int:
    """Generate the synthetic work-order database and its ground truth."""
    from maintenance_copilot.data.ground_truth import write_ground_truth
    from maintenance_copilot.data.work_orders import generate

    n = generate(config.WORK_ORDERS_DB, seed=args.seed)
    truth = write_ground_truth(config.WORK_ORDERS_DB, config.GROUND_TRUTH_FILE)
    print(f"Work orders: {n} rows -> {config.WORK_ORDERS_DB}")
    print(f"Ground truth: {config.GROUND_TRUTH_FILE}")
    print(f"  problem site: {truth['problem_site']['name']}")
    print(
        f"  pumps with repeat failures last quarter: "
        f"{', '.join(truth['pumps_with_repeat_failures_last_quarter'])}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="mc", description="Maintenance Copilot CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    setup = sub.add_parser("setup", help="build local data (work orders, ground truth)")
    setup.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    setup.set_defaults(func=cmd_setup)
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

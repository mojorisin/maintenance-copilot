"""Command-line entry point (`mc`).

Subcommands are added as milestones land:
  mc setup   build the work-order database and download sensor data
  mc ask     ask the agent a question from the terminal
"""

import argparse

from maintenance_copilot import __version__


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="mc", description="Maintenance Copilot CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the chosen subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

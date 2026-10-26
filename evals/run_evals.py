"""Run the evaluation suite: `uv run python evals/run_evals.py` (or `mc eval`).

Writes evals/results/<model>.md and .json (committed) and a full per-run
log to evals/runs/ (git-ignored).
"""

import sys

from maintenance_copilot.cli import main

if __name__ == "__main__":
    sys.exit(main(["eval", *sys.argv[1:]]))

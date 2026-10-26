"""Central settings: file locations and the fixed "today" for the dataset.

Everything is overridable with environment variables so the same code runs
locally, in CI and in tests (which point MC_DATA_DIR at a temp folder).
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# API keys and provider settings come from .env (git-ignored). Real
# environment variables take precedence over the file.
load_dotenv(REPO_ROOT / ".env", override=False)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.getenv("LLM_MODEL") or None
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")  # Claude only: low | medium | high | xhigh
MANUALS_DIR = REPO_ROOT / "manuals"

DATA_DIR = Path(os.getenv("MC_DATA_DIR", REPO_ROOT / "data"))
WORK_ORDERS_DB = DATA_DIR / "work_orders.db"
GROUND_TRUTH_FILE = DATA_DIR / "ground_truth.json"
CMAPSS_DIR = DATA_DIR / "cmapss"
RUL_MODEL_FILE = DATA_DIR / "rul_model.json"

# The synthetic data ends on 2026-09-30, and the agent treats AS_OF_DATE as
# "today". Pinning it keeps questions like "last quarter" (= Q3 2026) stable,
# so eval answers never drift with the calendar.
AS_OF_DATE = date(2026, 10, 1)

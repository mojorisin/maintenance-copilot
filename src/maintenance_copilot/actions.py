"""The only code in the project that writes data: human-approved work orders.

The planner graph calls `action_work_order` from a plain code node, and only
after a person approves the draft. The LLM never calls it, never sees a
writable connection, and never writes SQL here: the INSERT below is fixed
and every value is a bound parameter.

Approved orders go to a separate SQLite file (config.ACTIONS_DB), so the
work-order database the agents read stays unchanged. The evals check that.
"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from maintenance_copilot import config

SCHEMA = """\
CREATE TABLE IF NOT EXISTS approved_work_orders (
    action_id    TEXT PRIMARY KEY,   -- AWO-0001, AWO-0002, ...
    asset_id     TEXT NOT NULL,
    priority     TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low')),
    title        TEXT NOT NULL,
    failure_mode TEXT NOT NULL,
    actions      TEXT NOT NULL,      -- one recommended step per line
    reasoning    TEXT NOT NULL,      -- with [source] citations
    request      TEXT NOT NULL,      -- what the user originally reported
    approved_by  TEXT NOT NULL,
    approved_at  TEXT NOT NULL,      -- ISO timestamp (UTC)
    thread_id    TEXT NOT NULL       -- links back to the LangSmith trace
)
"""


def _connect() -> sqlite3.Connection:
    config.ACTIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.ACTIONS_DB)
    conn.execute(SCHEMA)
    return conn


def action_work_order(draft: dict, *, request: str, approved_by: str, thread_id: str) -> str:
    """Record an approved draft and return its new ID, e.g. 'AWO-0003'."""
    with closing(_connect()) as conn, conn:  # `with conn` commits or rolls back
        (count,) = conn.execute("SELECT COUNT(*) FROM approved_work_orders").fetchone()
        action_id = f"AWO-{count + 1:04d}"
        conn.execute(
            "INSERT INTO approved_work_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                draft["asset_id"],
                draft["priority"],
                draft["title"],
                draft["failure_mode"],
                "\n".join(draft["actions"]),
                draft["reasoning"],
                request,
                approved_by,
                datetime.now(UTC).isoformat(timespec="seconds"),
                thread_id,
            ),
        )
    return action_id


def list_actioned(limit: int = 50) -> list[dict]:
    """Most recent approved work orders first (for the UI and CLI)."""
    if not config.ACTIONS_DB.exists():
        return []
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM approved_work_orders ORDER BY approved_at DESC, action_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]

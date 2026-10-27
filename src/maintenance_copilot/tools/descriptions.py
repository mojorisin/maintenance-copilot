"""Tool descriptions shown to the model, shared by the LangGraph agent and the MCP server.

Keeping one copy means the agent sees identical tool documentation whether it
calls the tools directly or over MCP, which is what makes the eval parity
check meaningful.
"""

from maintenance_copilot.tools.work_orders import SCHEMA_DOC

QUERY_WORK_ORDERS = (
    "Run ONE read-only SQLite SELECT (max 200 rows). Include wo_id in the SELECT "
    "when you will cite individual work orders.\n\n" + SCHEMA_DOC
)

GET_SENSOR_TREND = (
    "Remaining useful life estimate (cycles), range, status (healthy/watch/critical) "
    "and most-drifted sensors for one gas turbine, e.g. asset_id='GT-042'. Omit "
    "asset_id for a fleet overview sorted most urgent first. Gas turbines only."
)

SEARCH_MANUALS = (
    "Keyword search over equipment manuals; returns sections with citation IDs. "
    "Use technical terms. asset_type: 'pump', 'compressor' or 'gas_turbine'."
)

"""The agent's tools: plain functions returning JSON-serialisable dicts.

Every result carries `sources` (citation IDs) on success, or `error` with a
message the agent can act on. Tools never raise for bad input.
"""

from maintenance_copilot.tools.manual_search import search_manuals
from maintenance_copilot.tools.sensors import get_sensor_trend
from maintenance_copilot.tools.work_orders import query_work_orders

__all__ = ["get_sensor_trend", "query_work_orders", "search_manuals"]

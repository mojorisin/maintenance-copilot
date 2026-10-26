"""`search_manuals`: find relevant sections in the equipment manuals."""

from functools import lru_cache
from pathlib import Path

from maintenance_copilot import config
from maintenance_copilot.manuals import ManualIndex

ASSET_TYPES = ("pump", "compressor", "gas_turbine")


@lru_cache(maxsize=1)
def _index(manuals_dir: str) -> ManualIndex:
    return ManualIndex.load(Path(manuals_dir))


def search_manuals(query: str, asset_type: str | None = None, k: int = 3) -> dict:
    """Return the top-k manual sections for a query, each with a citation ID.

    Use technical terms ("vibration", "seal flush", "T30") for best results.
    asset_type narrows the search to one manual: pump, compressor or gas_turbine.
    """
    if asset_type is not None and asset_type not in ASSET_TYPES:
        return {
            "error": f"Unknown asset_type '{asset_type}'. Use one of: {', '.join(ASSET_TYPES)}, "
            "or omit it. There is no valve manual."
        }
    hits = _index(str(config.MANUALS_DIR)).search(query, asset_type, k=min(max(k, 1), 5))
    if not hits:
        return {"results": [], "message": "No manual section matches this query.", "sources": []}
    return {
        "results": [
            {
                "id": h.section.id,
                "manual": h.section.manual_title,
                "section": h.section.heading,
                "score": h.score,
                "text": h.section.text,
            }
            for h in hits
        ],
        "sources": [h.section.id for h in hits],
    }

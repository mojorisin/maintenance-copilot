"""`get_sensor_trend`: remaining-life estimate and sensor drift for gas turbines.

Gas turbines are linked to NASA C-MAPSS FD001 test engines (GT-042 <->
engine 42). Their latest observed cycle is treated as "now".
"""

import re
import sqlite3
from functools import lru_cache
from pathlib import Path

import pandas as pd

from maintenance_copilot import config
from maintenance_copilot.data.cmapss import SENSORS, load_fd001
from maintenance_copilot.rul import BASELINE_CYCLES, RULModel, assess_engine

MODEL_SOURCE = "model:rul-baseline"


@lru_cache(maxsize=1)
def _load(cmapss_dir: str, model_file: str) -> tuple[pd.DataFrame, RULModel]:
    """Load test-engine histories and the trained model once per process."""
    return load_fd001("test", Path(cmapss_dir)), RULModel.load(Path(model_file))


def _turbines() -> dict[str, dict]:
    with sqlite3.connect(f"file:{config.WORK_ORDERS_DB}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT asset_id, site_id, cmapss_unit FROM assets WHERE asset_type = 'gas_turbine'"
        ).fetchall()
    return {a: {"site_id": s, "cmapss_unit": u} for a, s, u in rows}


def normalise_asset_id(raw: str) -> str:
    """Accept 'GT-042', 'gt-42', 'GT42', 'engine 42' or '42' -> 'GT-042'."""
    match = re.search(r"(\d+)", raw)
    if match and not raw.strip().upper().startswith(("P-", "C-", "V-")):
        return f"GT-{int(match.group(1)):03d}"
    return raw.strip().upper()


def drift_series(asset_id: str, tags: list[str]) -> pd.DataFrame:
    """Per-cycle drift of the given sensor tags, for charting (not an agent tool).

    Each column is the 10-cycle rolling mean minus the engine's early-life
    baseline, divided by normal noise: the same units as `drift_sigma`.
    """
    unit = _turbines()[normalise_asset_id(asset_id)]["cmapss_unit"]
    history, model = _load(str(config.CMAPSS_DIR), str(config.RUL_MODEL_FILE))
    engine = history[history["unit"] == unit].set_index("cycle")
    by_tag = {meta[0]: s for s, meta in SENSORS.items()}
    out = {}
    for tag in tags:
        col = by_tag[tag]
        base = engine[col].head(BASELINE_CYCLES).mean()
        out[tag] = (
            engine[col].rolling(model.window, min_periods=1).mean() - base
        ) / model.noise_std[col]
    return pd.DataFrame(out)


def get_sensor_trend(asset_id: str | None = None) -> dict:
    """RUL estimate, status and drifting sensors for one gas turbine.

    With no asset_id, returns a one-line summary for every turbine in the
    fleet, sorted most urgent first.
    """
    try:
        turbines = _turbines()
        history, model = _load(str(config.CMAPSS_DIR), str(config.RUL_MODEL_FILE))
    except (OSError, sqlite3.Error) as exc:
        return {"error": f"Sensor data unavailable ({exc}). Run `mc setup`."}

    if asset_id is None:
        fleet = []
        for aid, t in turbines.items():
            a = assess_engine(history[history["unit"] == t["cmapss_unit"]], model)
            fleet.append(
                {
                    "asset_id": aid,
                    "site_id": t["site_id"],
                    "rul_estimate_cycles": a["rul_estimate_cycles"],
                    "rul_range_cycles": a["rul_range_cycles"],
                    "status": a["status"],
                    "top_drifting_sensor": a["top_drifting_sensors"][0]["sensor"],
                }
            )
        fleet.sort(key=lambda f: f["rul_range_cycles"][0])
        units = [f"cmapss:FD001/unit-{turbines[f['asset_id']]['cmapss_unit']}" for f in fleet]
        return {"fleet": fleet, "sources": [*units, MODEL_SOURCE]}

    aid = normalise_asset_id(asset_id)
    if aid not in turbines:
        return {
            "error": f"No sensor data for '{asset_id}'. Sensor data exists only for gas "
            f"turbines: {', '.join(sorted(turbines))}."
        }
    unit = turbines[aid]["cmapss_unit"]
    result = assess_engine(history[history["unit"] == unit], model)
    return {
        "asset_id": aid,
        "site_id": turbines[aid]["site_id"],
        **result,
        "units_note": "Remaining life is in operating cycles (one start-run-stop each).",
        "sources": [f"cmapss:FD001/unit-{unit}", MODEL_SOURCE],
    }

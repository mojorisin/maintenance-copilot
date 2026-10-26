"""Shared fixtures: a synthetic engine fleet and a complete offline data folder."""

import numpy as np
import pandas as pd
import pytest

from maintenance_copilot import config
from maintenance_copilot.data.cmapss import COLUMNS, INFORMATIVE_SENSORS, SENSORS
from maintenance_copilot.data.work_orders import generate
from maintenance_copilot.rul import RULModel
from maintenance_copilot.tools import manual_search, sensors

# +1 = sensor rises with wear, -1 = falls (matches FD001's real behaviour).
DIRECTION = dict.fromkeys(INFORMATIVE_SENSORS, 1) | {"s7": -1, "s12": -1, "s20": -1, "s21": -1}


def synthetic_fleet(n_units: int = 40, seed: int = 0) -> pd.DataFrame:
    """Engines run to failure; informative sensors drift once wear starts."""
    rng = np.random.default_rng(seed)
    frames = []
    for unit in range(1, n_units + 1):
        life = int(rng.integers(150, 260))
        cycle = np.arange(1, life + 1)
        wear = np.clip((cycle - 0.4 * life) / (0.6 * life), 0, None) ** 2
        data = {"unit": unit, "cycle": cycle, "setting1": 0.0, "setting2": 0.0, "setting3": 100.0}
        for s in SENSORS:
            if s in DIRECTION:
                data[s] = 100 + DIRECTION[s] * 5 * wear + rng.normal(0, 0.5, life)
            else:
                data[s] = 100.0
        frames.append(pd.DataFrame(data))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture(scope="module")
def fleet():
    return synthetic_fleet()


@pytest.fixture
def data_env(tmp_path, monkeypatch):
    """A full data folder (DB, sensor test file, RUL model) with no download.

    Test engines are synthetic engines cut off at 50-95% of life, numbered
    so that every gas turbine's cmapss_unit exists.
    """
    monkeypatch.setattr(config, "WORK_ORDERS_DB", tmp_path / "work_orders.db")
    monkeypatch.setattr(config, "CMAPSS_DIR", tmp_path / "cmapss")
    monkeypatch.setattr(config, "RUL_MODEL_FILE", tmp_path / "rul_model.json")
    generate(config.WORK_ORDERS_DB)

    train = synthetic_fleet(n_units=40, seed=1)
    RULModel.fit(train).save(config.RUL_MODEL_FILE)

    full = synthetic_fleet(n_units=80, seed=2)
    rng = np.random.default_rng(3)
    test = pd.concat(g.head(int(len(g) * rng.uniform(0.5, 0.95))) for _, g in full.groupby("unit"))
    config.CMAPSS_DIR.mkdir()
    test[COLUMNS].to_csv(config.CMAPSS_DIR / "test_FD001.txt", sep=" ", header=False, index=False)

    sensors._load.cache_clear()
    manual_search._index.cache_clear()
    yield tmp_path
    sensors._load.cache_clear()

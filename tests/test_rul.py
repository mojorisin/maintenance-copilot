"""Tests for the RUL baseline, on a synthetic fleet so CI needs no download.

Each synthetic engine runs to failure; informative sensors drift in a known
direction once wear starts (see conftest.py), so we know what a correct
model must learn.
"""

import numpy as np
import pytest
from conftest import DIRECTION

from maintenance_copilot.data.cmapss import INFORMATIVE_SENSORS, SENSORS
from maintenance_copilot.rul import RULModel, assess_engine, nasa_score, status_for


@pytest.fixture(scope="module")
def model(fleet):
    return RULModel.fit(fleet)


def _engine(fleet, unit, fraction_of_life):
    g = fleet[fleet["unit"] == unit]
    return g.head(int(len(g) * fraction_of_life))


def test_learns_physically_consistent_weights(model):
    # A sensor that rises with wear must *reduce* predicted life, and vice versa.
    for s in INFORMATIVE_SENSORS:
        assert np.sign(model.weights[f"{s}_avg"]) == -DIRECTION[s], SENSORS[s][0]


def test_validation_error_is_reasonable(model):
    assert model.val_rmse < 25


def test_estimate_falls_as_engine_wears(fleet, model):
    young = model.predict(_engine(fleet, 1, 0.3))[-1]
    old = model.predict(_engine(fleet, 1, 0.98))[-1]
    assert young > 90
    assert old < 30


def test_worn_engine_is_critical_and_explained(fleet, model):
    a = assess_engine(_engine(fleet, 2, 0.98), model)
    assert a["status"] == "critical"
    assert abs(a["top_drifting_sensors"][0]["drift_sigma"]) > 3
    low, high = a["rul_range_cycles"]
    assert low <= a["rul_estimate_cycles"] <= high


def test_young_engine_is_healthy_with_little_drift(fleet, model):
    a = assess_engine(_engine(fleet, 3, 0.3), model)
    assert a["status"] == "healthy"
    assert abs(a["top_drifting_sensors"][0]["drift_sigma"]) < 3


def test_save_and_load_round_trip(tmp_path, fleet, model):
    path = tmp_path / "model.json"
    model.save(path)
    engine = _engine(fleet, 4, 0.8)
    assert np.allclose(RULModel.load(path).predict(engine), model.predict(engine))


def test_nasa_score_penalises_late_predictions_more():
    assert nasa_score(np.array([10.0])) > nasa_score(np.array([-10.0])) > 0


@pytest.mark.parametrize(
    ("rul", "status"), [(0, "critical"), (29, "critical"), (30, "watch"), (75, "healthy")]
)
def test_status_bands(rul, status):
    assert status_for(rul) == status

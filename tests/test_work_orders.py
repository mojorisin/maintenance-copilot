"""Tests for the synthetic work-order generator and its ground truth.

These pin down the planted patterns the agent and evals depend on, so a
change to the generator can't silently remove an insight.
"""

import sqlite3
from datetime import date

import pytest

from maintenance_copilot.config import AS_OF_DATE
from maintenance_copilot.data.ground_truth import compute_ground_truth, last_quarter
from maintenance_copilot.data.work_orders import generate


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("data") / "work_orders.db"
    generate(path, seed=42)
    return path


@pytest.fixture(scope="module")
def truth(db_path):
    return compute_ground_truth(db_path)


def _dump(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def test_same_seed_gives_identical_database(db_path, tmp_path):
    again = tmp_path / "again.db"
    generate(again, seed=42)
    assert _dump(again) == _dump(db_path)


def test_dataset_size(truth):
    assert truth["counts"]["assets"] == 40
    assert truth["counts"]["sites"] == 4
    assert 250 <= truth["counts"]["work_orders"] <= 350


def test_no_work_order_after_as_of_date(db_path):
    with sqlite3.connect(db_path) as conn:
        latest = conn.execute("SELECT MAX(closed_date) FROM work_orders").fetchone()[0]
    assert latest < AS_OF_DATE.isoformat()


def test_last_quarter_is_q3_2026():
    assert last_quarter(date(2026, 10, 1)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert last_quarter(date(2026, 2, 15)) == (date(2025, 10, 1), date(2025, 12, 31))


def test_planted_repeat_failures_are_found(truth):
    found = {(r["asset_id"], r["failure_mode"]) for r in truth["repeat_failures_last_quarter"]}
    assert ("P-104", "mechanical seal leak") in found
    assert ("P-109", "bearing failure") in found
    assert ("C-203", "valve failure") in found


def test_riverbend_is_the_problem_site(truth):
    assert truth["problem_site"]["site_id"] == "RB"
    rates = [s["corrective_per_asset"] for s in truth["corrective_per_asset_by_site"]]
    assert rates[0] >= 1.5 * rates[1]  # clearly worse, not a coin flip


def test_risk_view_ranks_planted_bad_actors_highly(truth):
    top5 = [a["asset_id"] for a in truth["top_risk_assets"]]
    assert {"P-104", "C-205"} <= set(top5)


def test_prompt_injection_note_is_planted(truth):
    assert [w["asset_id"] for w in truth["prompt_injection_work_orders"]] == ["V-306"]


def test_turbines_map_to_cmapss_units(truth):
    assert truth["turbine_cmapss_map"]["GT-042"] == 42
    assert len(truth["turbine_cmapss_map"]) == 8

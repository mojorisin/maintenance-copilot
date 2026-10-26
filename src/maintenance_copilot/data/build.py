"""Build every local artefact the agent and evals need. Safe to re-run.

Used by `mc setup` and by the web app on first launch (hosted deployments
start without the git-ignored data/ folder).
"""

from collections.abc import Callable

from maintenance_copilot import config


def data_ready() -> bool:
    """True when the database, sensor data and RUL model all exist."""
    return all(
        p.exists()
        for p in (
            config.WORK_ORDERS_DB,
            config.RUL_MODEL_FILE,
            config.GROUND_TRUTH_FILE,
            config.CMAPSS_DIR / "test_FD001.txt",
        )
    )


def build_all(seed: int = 42, log: Callable[[str], None] = print) -> dict:
    """Generate work orders, fetch C-MAPSS, train the RUL model, write ground truth."""
    from maintenance_copilot.data import cmapss
    from maintenance_copilot.data.ground_truth import (
        add_derived_facts,
        compute_ground_truth,
        turbine_facts,
        write_ground_truth,
    )
    from maintenance_copilot.data.work_orders import generate
    from maintenance_copilot.rul import RULModel, evaluate_on_test

    log("1/4 Generating synthetic work orders...")
    n = generate(config.WORK_ORDERS_DB, seed=seed)
    log(f"    {n} work orders -> {config.WORK_ORDERS_DB}")

    log("2/4 Fetching NASA C-MAPSS FD001 (about 12 MB, first run only)...")
    cmapss.fetch_fd001(config.CMAPSS_DIR)
    train = cmapss.load_fd001("train", config.CMAPSS_DIR)
    test = cmapss.load_fd001("test", config.CMAPSS_DIR)
    true_rul = cmapss.load_test_rul(config.CMAPSS_DIR)

    log("3/4 Training the RUL baseline...")
    model = RULModel.fit(train)
    model.test_metrics = evaluate_on_test(model, test, true_rul)
    model.save(config.RUL_MODEL_FILE)
    m = model.test_metrics
    log(f"    test RMSE {m['rmse']} cycles, MAE {m['mae']} on {m['engines']} NASA test engines")

    log("4/4 Computing eval ground truth...")
    truth = compute_ground_truth(config.WORK_ORDERS_DB)
    truth["turbines"] = turbine_facts(model, test, true_rul, truth["turbine_cmapss_map"])
    truth["rul_model_test_metrics"] = model.test_metrics
    add_derived_facts(truth)
    write_ground_truth(truth, config.GROUND_TRUTH_FILE)
    log(f"    -> {config.GROUND_TRUTH_FILE}")
    return truth

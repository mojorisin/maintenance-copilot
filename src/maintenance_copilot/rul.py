"""Explainable baseline for remaining useful life (RUL) on C-MAPSS FD001.

Model: ridge regression on 10-cycle rolling means of the 14 informative
sensors (standardised), trained to predict RUL capped at 125 cycles. The cap
is the standard C-MAPSS convention: early in life an engine is simply
"healthy", and its exact distance to failure is not knowable.

    RUL = intercept + sum(weight_i * z_i)

Every estimate can therefore be decomposed into per-sensor contributions.
Plain least squares gives near-duplicate sensors (e.g. physical vs corrected
fan speed) large opposing weights, so a sensor that rises with wear can
appear to *add* life. The ridge penalty is set to the smallest value at
which every weight has the same sign as that sensor's correlation with RUL,
so each contribution is physically meaningful.
Separately, `sensor_drift` reports how far each sensor has moved from the
engine's own early-life baseline, which is what an engineer acts on.

Clarity over accuracy: deep models reach a lower error, and the README
compares the two honestly.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from maintenance_copilot.data.cmapss import INFORMATIVE_SENSORS, SENSORS

RUL_CAP = 125
SMOOTHING_WINDOW = 10
BASELINE_CYCLES = 20  # early-life cycles used as each engine's healthy reference
VALIDATION_UNITS = 20  # last N training engines held out to size the error band
RIDGE_GRID = (0, 100, 300, 1_000, 3_000, 5_000, 10_000, 30_000, 100_000)

# Status bands in cycles of remaining life. Status is assigned from the
# *pessimistic* end of the estimate (estimate - validation RMSE): a late call
# costs far more than an early one. On the FD001 test set this cuts missed
# critical engines from 18/25 to 6/25 at the price of more "watch" flags.
CRITICAL_BELOW = 30
WATCH_BELOW = 75


def add_rolling_features(df: pd.DataFrame, window: int = SMOOTHING_WINDOW) -> pd.DataFrame:
    """Add `<sensor>_avg` columns: per-engine rolling means that damp sensor noise."""
    out = df.copy()
    rolled = df.groupby("unit")[INFORMATIVE_SENSORS].transform(
        lambda s: s.rolling(window, min_periods=1).mean()
    )
    for s in INFORMATIVE_SENSORS:
        out[f"{s}_avg"] = rolled[s]
    return out


def capped_rul(train: pd.DataFrame, cap: int = RUL_CAP) -> pd.Series:
    """Training target: cycles until failure, capped at `cap`."""
    last = train.groupby("unit")["cycle"].transform("max")
    return (last - train["cycle"]).clip(upper=cap)


def nasa_score(error: np.ndarray) -> float:
    """PHM08 challenge score: late predictions (error > 0) are penalised more."""
    return float(np.sum(np.where(error < 0, np.exp(-error / 13), np.exp(error / 10)) - 1))


def status_for(rul: float) -> str:
    """Map a RUL estimate to an action band."""
    if rul < CRITICAL_BELOW:
        return "critical"
    if rul < WATCH_BELOW:
        return "watch"
    return "healthy"


@dataclass
class RULModel:
    """A fitted linear RUL model plus the statistics needed to explain it."""

    features: list[str]
    mean: dict[str, float]
    std: dict[str, float]
    weights: dict[str, float]
    intercept: float
    noise_std: dict[str, float]  # normal cycle-to-cycle noise per raw sensor
    val_rmse: float  # error on held-out training engines, used as the +/- band
    ridge_alpha: float
    cap: int = RUL_CAP
    window: int = SMOOTHING_WINDOW
    test_metrics: dict[str, float] = field(default_factory=dict)

    # --- training -----------------------------------------------------------------

    @staticmethod
    def _solve(x: pd.DataFrame, y: pd.Series, mean: pd.Series, std: pd.Series, alpha: float):
        """Ridge regression in closed form; the intercept is not penalised."""
        z = ((x - mean) / std).to_numpy()
        design = np.column_stack([np.ones(len(z)), z])
        penalty = alpha * np.eye(design.shape[1])
        penalty[0, 0] = 0
        coef = np.linalg.solve(design.T @ design + penalty, design.T @ y.to_numpy(dtype=float))
        return coef[0], coef[1:]

    @classmethod
    def fit(cls, train: pd.DataFrame) -> "RULModel":
        """Fit on run-to-failure engines; size the error band on held-out engines."""
        df = add_rolling_features(train)
        features = [f"{s}_avg" for s in INFORMATIVE_SENSORS]
        y = capped_rul(df)
        expected_sign = np.sign([df[s].corr(y) for s in INFORMATIVE_SENSORS])

        # 1. Final model: the smallest ridge penalty at which every weight has
        #    the physically expected sign, checked on the model we actually ship.
        mean, std = df[features].mean(), df[features].std()
        for alpha in RIDGE_GRID:
            b0, w = cls._solve(df[features], y, mean, std, alpha)
            if np.array_equal(np.sign(w), expected_sign):
                break
        else:
            raise RuntimeError("No penalty in RIDGE_GRID gives sign-consistent weights")

        # 2. Error band: refit with the same penalty on most engines and
        #    measure error on held-out engines the fit has never seen.
        val_units = sorted(df["unit"].unique())[-VALIDATION_UNITS:]
        is_val = df["unit"].isin(val_units)
        tr = df[~is_val]
        tr_mean, tr_std = tr[features].mean(), tr[features].std()
        vb0, vw = cls._solve(tr[features], y[~is_val], tr_mean, tr_std, alpha)
        z_val = ((df.loc[is_val, features] - tr_mean) / tr_std).to_numpy()
        val_pred = np.clip(vb0 + z_val @ vw, 0, RUL_CAP)
        val_rmse = float(np.sqrt(np.mean((val_pred - y[is_val].to_numpy()) ** 2)))

        early = train[train["cycle"] <= BASELINE_CYCLES]
        within = early[INFORMATIVE_SENSORS] - early.groupby("unit")[INFORMATIVE_SENSORS].transform(
            "mean"
        )

        return cls(
            features=features,
            mean=mean.round(6).to_dict(),
            std=std.round(6).to_dict(),
            weights=dict(zip(features, np.round(w, 6).tolist(), strict=True)),
            intercept=round(float(b0), 6),
            noise_std=within.std().round(6).to_dict(),
            val_rmse=round(val_rmse, 2),
            ridge_alpha=float(alpha),
        )

    # --- inference ----------------------------------------------------------------

    def _z(self, featured: pd.DataFrame) -> np.ndarray:
        mean = pd.Series(self.mean)[self.features]
        std = pd.Series(self.std)[self.features]
        return ((featured[self.features] - mean) / std).to_numpy()

    def predict(self, history: pd.DataFrame) -> np.ndarray:
        """RUL estimate for every row of one or more engines' histories."""
        featured = add_rolling_features(history, self.window)
        w = np.array([self.weights[f] for f in self.features])
        return np.clip(self.intercept + self._z(featured) @ w, 0, self.cap)

    def contributions(self, history: pd.DataFrame) -> dict[str, float]:
        """Cycles each sensor adds to (+) or removes from (-) the latest estimate."""
        z_last = self._z(add_rolling_features(history, self.window))[-1]
        return {
            f.removesuffix("_avg"): round(float(self.weights[f] * z), 1)
            for f, z in zip(self.features, z_last, strict=True)
        }

    # --- persistence --------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "RULModel":
        return cls(**json.loads(path.read_text()))


def sensor_drift(history: pd.DataFrame, model: RULModel, recent: int = SMOOTHING_WINDOW):
    """How far each sensor has moved from this engine's own healthy baseline.

    drift_sigma = (mean of last `recent` cycles - mean of first BASELINE_CYCLES)
                  / normal cycle-to-cycle noise
    Sorted by magnitude, largest first.
    """
    base = history.head(BASELINE_CYCLES)
    last = history.tail(recent)
    rows = []
    for s in INFORMATIVE_SENSORS:
        tag, description, units, _ = SENSORS[s]
        before, now = float(base[s].mean()), float(last[s].mean())
        sigma = (now - before) / model.noise_std[s]
        rows.append(
            {
                "sensor": tag,
                "description": description,
                "units": units,
                "baseline": round(before, 3),
                "current": round(now, 3),
                "drift_sigma": round(sigma, 1),
                "direction": "rising" if sigma > 0 else "falling",
            }
        )
    return sorted(rows, key=lambda r: abs(r["drift_sigma"]), reverse=True)


def assess_engine(history: pd.DataFrame, model: RULModel, top_n: int = 5) -> dict:
    """Everything the agent needs about one engine: estimate, band, status, why."""
    estimate = float(model.predict(history)[-1])
    pessimistic = max(0.0, estimate - model.val_rmse)
    contrib = model.contributions(history)
    biggest = sorted(contrib.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top_n]
    return {
        "cycles_observed": int(history["cycle"].max()),
        "rul_estimate_cycles": round(estimate),
        "rul_range_cycles": [round(pessimistic), round(estimate + model.val_rmse)],
        "status": status_for(pessimistic),
        "status_basis": "pessimistic end of range (late calls cost more than early ones)",
        "top_drifting_sensors": sensor_drift(history, model)[:top_n],
        "largest_model_contributions": [{"sensor": SENSORS[s][0], "cycles": c} for s, c in biggest],
        "method": (
            f"Ridge regression on {model.window}-cycle rolling sensor means, RUL capped at "
            f"{model.cap}; +/- band = validation RMSE ({model.val_rmse} cycles)."
        ),
    }


def evaluate_on_test(model: RULModel, test: pd.DataFrame, true_rul: pd.Series) -> dict:
    """Score the last-cycle estimate of every test engine against NASA's true RUL."""
    preds = pd.Series({unit: model.predict(g)[-1] for unit, g in test.groupby("unit")}, name="pred")
    error = (preds - true_rul.loc[preds.index]).to_numpy()
    return {
        "engines": int(len(preds)),
        "rmse": round(float(np.sqrt(np.mean(error**2))), 2),
        "mae": round(float(np.mean(np.abs(error))), 2),
        "nasa_score": round(nasa_score(error), 1),
    }

"""The four flow models of the Control Tower (spec 7.1): training, files, scoring and explanations.

| Model | Task | Baseline (spec) | Gate (spec) |
| --- | --- | --- | --- |
| admission | P(admitted) from triage | CTAS <= 2 predicts admission | AUROC >= baseline + 0.05 |
| discharge | P(discharged within 24 h) | days in hospital >= expected LOS | AUROC >= baseline + 0.05 |
| or_duration | minutes in the OR | median of the same procedure in training | MAE <= 90% of the baseline's |
| ed_wait | mean wait of the next hour's triaged patients | mean wait of the last 4 hours | MAE <= 90% of the baseline's |

Algorithm: scikit-learn's histogram gradient boosting (HistGradientBoostingClassifier /
Regressor). The spec names LightGBM, which is not a dependency of this repository; adding
it is a stack change that needs the owner's confirmation, and scikit-learn's implementation
is the same family (histogram-based gradient-boosted trees, LightGBM's design). Validation is
a time split: the last 20% of the dates are held out, never a random split.

Explanations: per-feature contributions by path attribution on the trees (the Saabas method,
LightGBM's `pred_contrib` without the Shapley averaging): walking each tree from the root to
the leaf, every split's change in the expected value is credited to the split's feature. The
contributions plus the expected value add up exactly to the raw prediction (log-odds for the
classifiers, minutes for the regressors). The three largest make the one-sentence explanation
("Admission probability 0.82: CTAS 2, age 72, SpO₂ 91%").

The metrics come from synthetic data: they show that the pipeline works, not clinical or
operational performance (models/flow/README.md).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score

from app.modules.control_tower import features as F
from app.modules.control_tower.flowdata import Table

MODELS_DIR = Path(__file__).resolve().parents[3] / "models" / "flow"
NAMES = ("admission", "discharge", "or_duration", "ed_wait")
TEST_SHARE = 0.2  # the last 20% of the dates


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: Literal["classifier", "regressor"]
    title: str
    baseline: str
    gate: str


SPECS = {
    "admission": ModelSpec("admission", "classifier", "Admission from triage", "CTAS <= 2 predicts admission",
                           "AUROC at least 0.05 above the baseline"),
    "discharge": ModelSpec("discharge", "classifier", "Discharge within 24 h",
                           "days in hospital >= expected length of stay predicts discharge",
                           "AUROC at least 0.05 above the baseline"),
    "or_duration": ModelSpec("or_duration", "regressor", "Surgical duration (minutes)",
                             "median duration of the same procedure in the training data",
                             "MAE at least 10% below the baseline's"),
    "ed_wait": ModelSpec("ed_wait", "regressor", "ED wait in the next hour (minutes)",
                         "mean wait of the patients seen in the last 4 hours", "MAE at least 10% below the baseline's"),
}
PARAMS = dict(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20, l2_regularization=1.0,
              early_stopping=False, random_state=0)


# ---------- training ----------


def _matrix(table: Table, rows: list[dict] | None = None) -> np.ndarray:
    rows = table.rows if rows is None else rows
    return np.array([[float(r[c]) for c in table.columns] for r in rows], dtype=float)


def time_split(rows: list[dict], share: float = TEST_SHARE) -> tuple[list[dict], list[dict], list]:
    """Train on the earlier dates, test on the last `share` of the dates (by calendar date of `time`)."""
    dates = sorted({r["time"].date() for r in rows})
    n_test = max(1, math.ceil(len(dates) * share))
    test_dates = set(dates[-n_test:])
    return [r for r in rows if r["time"].date() not in test_dates], \
        [r for r in rows if r["time"].date() in test_dates], sorted(test_dates)


def _baseline(name: str, train: list[dict], test: list[dict]) -> np.ndarray:
    if name == "admission":
        return np.array([1.0 if r["ctas"] <= 2 else 0.0 for r in test])
    if name == "discharge":
        return np.array([1.0 if r["days_in"] >= r["expected_los"] else 0.0 for r in test])
    if name == "or_duration":
        by_code: dict[float, list[float]] = {}
        for r in train:
            by_code.setdefault(r["procedure"], []).append(r["label"])
        overall = float(np.median([r["label"] for r in train]))
        return np.array([float(np.median(by_code[r["procedure"]])) if r["procedure"] in by_code else overall
                         for r in test])
    return np.array([r["baseline"] for r in test])  # ed_wait: the rolling 4-hour mean, computed with the data


def train(table: Table) -> tuple[dict, dict]:
    """(bundle, metrics) for one model; the bundle is what `save` writes."""
    spec = SPECS[table.name]
    train_rows, test_rows, test_dates = time_split(table.rows)
    X_train, X_test = _matrix(table, train_rows), _matrix(table, test_rows)
    y_train = np.array([r["label"] for r in train_rows])
    y_test = np.array([r["label"] for r in test_rows])
    baseline = _baseline(table.name, train_rows, test_rows)
    if spec.kind == "classifier":
        model = HistGradientBoostingClassifier(**PARAMS).fit(X_train, y_train.astype(int))
        scores = model.predict_proba(X_test)[:, 1]
        auroc, base_auroc = float(roc_auc_score(y_test, scores)), float(roc_auc_score(y_test, baseline))
        metrics = {"metric": "auroc", "model": round(auroc, 4), "baseline": round(base_auroc, 4),
                   "lift": round(auroc - base_auroc, 4), "passed": auroc >= base_auroc + 0.05,
                   "positive_rate_test": round(float(y_test.mean()), 4)}
    else:
        model = HistGradientBoostingRegressor(**PARAMS).fit(X_train, y_train)
        pred = model.predict(X_test)
        mae, base_mae = float(mean_absolute_error(y_test, pred)), float(mean_absolute_error(y_test, baseline))
        metrics = {"metric": "mae", "model": round(mae, 2), "baseline": round(base_mae, 2),
                   "improvement": round(1 - mae / base_mae, 4) if base_mae else None,
                   "passed": bool(base_mae) and mae <= 0.9 * base_mae, "mean_label_test": round(float(y_test.mean()), 1)}
    metrics.update(n_train=len(train_rows), n_test=len(test_rows),
                   train_dates=[str(train_rows[0]["time"].date()) if train_rows else None,
                                str(max(r["time"] for r in train_rows).date()) if train_rows else None],
                   test_dates=[str(test_dates[0]), str(test_dates[-1])])
    bundle = {"name": table.name, "kind": spec.kind, "features": list(table.columns), "model": model,
              "trained_at": datetime.now().isoformat(timespec="seconds"), "metrics": metrics,
              "sklearn": sklearn.__version__}
    # the final model learns from every row; the metrics above are from the held-out dates
    full = (HistGradientBoostingClassifier if spec.kind == "classifier" else HistGradientBoostingRegressor)(**PARAMS)
    bundle["model"] = full.fit(_matrix(table), np.array([r["label"] for r in table.rows]).astype(
        int if spec.kind == "classifier" else float))
    bundle["importance"] = _importance(bundle, X_test)
    return bundle, metrics


def _importance(bundle: dict, X: np.ndarray) -> dict[str, float]:
    """Mean absolute contribution per feature over the test rows (the report's feature ranking)."""
    if len(X) == 0:
        return {}
    contribs = Explainer(bundle["model"]).contributions(X[:500])
    mean = np.abs(contribs).mean(axis=0)
    return {f: round(float(v), 4) for f, v in sorted(zip(bundle["features"], mean, strict=True), key=lambda x: -x[1])}


def save(bundle: dict, folder: Path = MODELS_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{bundle['name']}.joblib"
    joblib.dump(bundle, path, compress=3)
    return path


# ---------- explanation: path attribution on the trees ----------


class Explainer:
    """Per-feature contributions of a fitted HistGradientBoosting model (see the module docstring)."""

    def __init__(self, model) -> None:
        self.trees = []
        for iteration in model._predictors:
            nodes = iteration[0].nodes
            expected = nodes["value"].astype(float).copy()
            for i in range(len(nodes) - 1, -1, -1):  # children come after their parent
                if not nodes["is_leaf"][i]:
                    left, right = nodes["left"][i], nodes["right"][i]
                    n_left, n_right = nodes["count"][left], nodes["count"][right]
                    expected[i] = (n_left * expected[left] + n_right * expected[right]) / max(n_left + n_right, 1)
            self.trees.append((nodes["is_leaf"].astype(bool), nodes["feature_idx"].astype(np.int64),
                               nodes["num_threshold"].astype(float), nodes["missing_go_to_left"].astype(bool),
                               nodes["left"].astype(np.int64), nodes["right"].astype(np.int64), expected))
        self.bias = float(np.ravel(model._baseline_prediction)[0]) + sum(float(t[-1][0]) for t in self.trees)

    def contributions(self, X: np.ndarray) -> np.ndarray:
        """rows x features; each row's contributions plus `bias` equal its raw prediction."""
        X = np.asarray(X, dtype=float)
        out = np.zeros_like(X)
        rows = np.arange(X.shape[0])
        for is_leaf, feature, threshold, missing_left, left, right, expected in self.trees:
            at = np.zeros(X.shape[0], dtype=np.int64)
            active = ~is_leaf[at]
            while active.any():
                r = rows[active]
                node = at[r]
                f = feature[node]
                x = X[r, f]
                go_left = np.where(np.isnan(x), missing_left[node], x <= threshold[node])
                child = np.where(go_left, left[node], right[node])
                np.add.at(out, (r, f), expected[child] - expected[node])
                at[r] = child
                active = ~is_leaf[at]
        return out


# ---------- scoring ----------


@dataclass
class Prediction:
    value: float  # probability (classifiers) or minutes (regressors)
    factors: list[tuple[str, float, float]] = field(default_factory=list)  # (feature, value, contribution), top 3

    def sentence(self, model: str, *, values: bool = True, booked: float | None = None) -> str:
        """The one-sentence explanation (spec 7.1); `values=False` names the factors without their values
        (for roles that do not see clinical content)."""
        feats = {f.name: f for f in F.FEATURES[model]}
        parts = [feats[name].describe(v, values=values or not feats[name].clinical) for name, v, _ in self.factors]
        head = {"admission": f"Admission probability {self.value:.2f}",
                "discharge": f"Discharge within 24 h {self.value:.2f}",
                "or_duration": f"Predicted {round(self.value)} min" + (f" (booked {round(booked)})" if booked else ""),
                "ed_wait": f"Predicted wait {round(self.value)} min"}[model]
        return f"{head}: {', '.join(parts)}" if parts else head


class FlowModels:
    """The trained models from models/flow/ (loaded once per process, reloaded when a file changes)."""

    def __init__(self, folder: Path = MODELS_DIR) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        self._loaded: dict[str, tuple[float, dict, Explainer]] = {}

    def bundle(self, name: str) -> tuple[dict, Explainer] | None:
        path = self.folder / f"{name}.joblib"
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._loaded.get(name)
            if cached is None or cached[0] != mtime:
                bundle = joblib.load(path)
                cached = self._loaded[name] = (mtime, bundle, Explainer(bundle["model"]))
        return cached[1], cached[2]

    def available(self) -> dict[str, bool]:
        return {name: (self.folder / f"{name}.joblib").exists() for name in NAMES}

    def predict(self, name: str, rows: list[dict[str, float]]) -> list[Prediction] | None:
        """None when the model has not been trained (the board then shows no prediction)."""
        if not rows:
            return []
        loaded = self.bundle(name)
        if loaded is None:
            return None
        bundle, explainer = loaded
        X = np.array([[float(r.get(f, float("nan"))) for f in bundle["features"]] for r in rows], dtype=float)
        contribs = explainer.contributions(X)
        # contributions + bias = the raw prediction (scikit-learn's own predict spends ~1 ms per tree on small
        # batches, so the walk that explains also scores)
        raw = explainer.bias + contribs.sum(axis=1)
        values = 1 / (1 + np.exp(-raw)) if bundle["kind"] == "classifier" else raw
        out = []
        for i, v in enumerate(values):
            order = np.argsort(-np.abs(contribs[i]))[:3]
            out.append(Prediction(float(v), [(bundle["features"][j], float(X[i, j]), float(contribs[i, j]))
                                             for j in order if abs(contribs[i, j]) > 1e-9]))
        return out

    def metrics(self) -> dict[str, dict]:
        out = {}
        for name in NAMES:
            loaded = self.bundle(name)
            if loaded is not None:
                out[name] = {**loaded[0]["metrics"], "trained_at": loaded[0]["trained_at"],
                             "title": SPECS[name].title, "baseline_rule": SPECS[name].baseline,
                             "gate": SPECS[name].gate}
        return out


_models = FlowModels()


def models() -> FlowModels:
    return _models

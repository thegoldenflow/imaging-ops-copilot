"""No-show risk model (scikit-learn logistic regression).

Trained on the synthetic appointment history at startup. The last quarter of
history (by date) is held out to report AUC. Each upcoming appointment gets a
risk score and its top three contributing factors.
"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from app.core.models import ACTIVE_STATUSES, Appointment, AppointmentStatus
from app.core.store import Store
from app.llm.deid import age_from_dob

FEATURES = ["lead_days", "prior_no_shows", "is_monday", "off_peak_hour", "reminder_confirmed", "age"]
HIGH_RISK = 0.15


@dataclass
class NoShowModel:
    model: LogisticRegression
    scaler: StandardScaler
    auc: float
    base_rate: float
    n_train: int
    n_test: int
    coefficients: dict[str, float]
    trained_at: datetime


def _features(appt: Appointment, prior: int, store: Store) -> list[float]:
    patient = store.patients[appt.patient_id]
    return [
        min((appt.start - appt.booked_at).days, 60),
        min(prior, 3),
        1.0 if appt.start.weekday() == 0 else 0.0,
        1.0 if appt.start.hour < 9 or appt.start.hour >= 18 else 0.0,
        1.0 if appt.reminder_confirmed else 0.0,
        age_from_dob(patient.dob, appt.start.date()),
    ]


def _prior_counts(store: Store) -> tuple[list[tuple[Appointment, int]], dict[str, int]]:
    """Past appointments with each patient's no-show count before it, plus final counts."""
    past = sorted(
        (a for a in store.appointments.values()
         if a.status in (AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW)),
        key=lambda a: a.start,
    )
    counts: dict[str, int] = {}
    rows = []
    for appt in past:
        rows.append((appt, counts.get(appt.patient_id, 0)))
        if appt.status == AppointmentStatus.NO_SHOW:
            counts[appt.patient_id] = counts.get(appt.patient_id, 0) + 1
    return rows, counts


def train(store: Store) -> NoShowModel:
    rows, _ = _prior_counts(store)
    X = np.array([_features(a, prior, store) for a, prior in rows])
    y = np.array([1 if a.status == AppointmentStatus.NO_SHOW else 0 for a, _ in rows])
    split = int(len(rows) * 0.75)  # chronological hold-out
    scaler = StandardScaler().fit(X[:split])
    model = LogisticRegression(max_iter=500).fit(scaler.transform(X[:split]), y[:split])
    auc = roc_auc_score(y[split:], model.predict_proba(scaler.transform(X[split:]))[:, 1])
    return NoShowModel(
        model=model, scaler=scaler, auc=round(float(auc), 3), base_rate=round(float(y.mean()), 3),
        n_train=split, n_test=len(rows) - split,
        coefficients={f: round(float(c), 3) for f, c in zip(FEATURES, model.coef_[0])},
        trained_at=datetime.now(),
    )


def _explain(name: str, value: float, appt: Appointment) -> str:
    return {
        "lead_days": f"Booked {int(value)} days ahead",
        "prior_no_shows": f"{int(value)} previous no-show{'s' if value != 1 else ''}",
        "is_monday": "Monday appointment",
        "off_peak_hour": f"Early/late slot ({appt.start:%H:%M})",
        "reminder_confirmed": "Reminder not yet confirmed",
        "age": f"Age {int(value)}",
    }[name]


def score_upcoming(store: Store, nsm: NoShowModel) -> None:
    _, counts = _prior_counts(store)
    upcoming = [a for a in store.appointments.values() if a.status in ACTIVE_STATUSES]
    if not upcoming:
        return
    X = np.array([_features(a, counts.get(a.patient_id, 0), store) for a in upcoming])
    Z = nsm.scaler.transform(X)
    probs = nsm.model.predict_proba(Z)[:, 1]
    coefs = nsm.model.coef_[0]
    for appt, x, z, p in zip(upcoming, X, Z, probs):
        contributions = coefs * z
        order = np.argsort(-contributions)
        factors = [_explain(FEATURES[i], x[i], appt) for i in order[:3] if contributions[i] > 0.05]
        appt.no_show_risk = round(float(p), 3)
        appt.risk_factors = factors or ["No strong risk factors"]


def get_model(store: Store) -> NoShowModel:
    nsm = store.modules.get("noshow")
    if nsm is None:
        nsm = train(store)
        store.modules["noshow"] = nsm
        score_upcoming(store, nsm)
    return nsm

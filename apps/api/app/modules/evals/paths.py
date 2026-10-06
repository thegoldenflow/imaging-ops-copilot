import os
from pathlib import Path

# Repository root: apps/api/app/modules/evals/paths.py -> parents[5]
EVALS_DIR = Path(os.getenv("EVALS_DIR", Path(__file__).resolve().parents[5] / "evals"))
DATASETS = EVALS_DIR / "datasets"
RESULTS = EVALS_DIR / "results"

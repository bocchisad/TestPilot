import numpy as np

from testpilot_train.baselines import baseline_scores, random_recall
from testpilot_train.data import load_parquets
from testpilot_train.schema import FeaturesSchema


def test_random_recall_is_k_over_n():
    assert random_recall(100, 10, 0.10) == 0.10


def test_fail_per_day(tiny_parquet, features_schema: FeaturesSchema):
    frame = load_parquets([tiny_parquet], features_schema).frame
    scores = baseline_scores(frame, "fail_per_day")
    age = np.maximum(frame["t_age_days"].to_numpy().astype(np.float64), 1.0 / 86400.0)
    expected = frame["t_fail_cnt"].to_numpy().astype(np.float64) / age
    assert np.allclose(scores, expected)

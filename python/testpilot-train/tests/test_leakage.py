from testpilot_train.data import load_parquets
from testpilot_train.leakage import future_feature_canary, shuffled_label_gap
from testpilot_train.schema import FeaturesSchema


def test_shuffled_label_near_random(synth_parquet, features_schema: FeaturesSchema):
    frame = load_parquets([synth_parquet], features_schema).frame
    gap = shuffled_label_gap(frame, features_schema, seed=0)
    assert gap < 0.05


def test_future_feature_canary_fires(synth_parquet, features_schema: FeaturesSchema):
    frame = load_parquets([synth_parquet], features_schema).frame
    fired, leak_r, random_r = future_feature_canary(frame, features_schema, seed=0)
    assert fired, (leak_r, random_r)

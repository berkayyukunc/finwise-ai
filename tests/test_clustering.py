"""Arketip kümeleme: centroid→etiket eşlemesinin doğruluğu, determinizm ve yetersiz veri davranışı."""

import numpy as np
import pandas as pd
import pytest

from src.clustering.archetypes import ARCHETYPES, FEATURE_NAMES, SpendingArchetypeClusterer


@pytest.fixture(scope="module")
def clusterer() -> SpendingArchetypeClusterer:
    return SpendingArchetypeClusterer()


@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a["key"] for a in ARCHETYPES])
def test_each_prototype_gets_its_own_label(clusterer, archetype):
    """Regresyon: eski sürümde küme kimliği sabit isme bağlandığı için 6 etiketin 6'sı da yanlıştı."""
    cluster_id = int(clusterer.kmeans.predict(clusterer.scaler.transform(archetype["prototype"][None, :]))[0])
    assert clusterer.archetype_for_cluster(cluster_id)["key"] == archetype["key"]


@pytest.mark.parametrize("seed", [0, 1, 7, 123])
def test_label_mapping_is_correct_for_any_kmeans_seed(seed):
    c = SpendingArchetypeClusterer(random_state=seed)
    assert sorted(c.cluster_to_archetype.values()) == list(range(len(ARCHETYPES))), "Eşleme bire bir olmalı"
    for a in ARCHETYPES:
        cid = int(c.kmeans.predict(c.scaler.transform(a["prototype"][None, :]))[0])
        assert c.archetype_for_cluster(cid)["key"] == a["key"]
    assert c.quality["purity"] > 0.95 and c.quality["silhouette"] > 0.3


def test_constructor_does_not_mutate_global_numpy_rng():
    np.random.seed(99)
    expected = np.random.rand()
    np.random.seed(99)
    SpendingArchetypeClusterer()
    assert np.random.rand() == expected


def test_feature_extraction_values(sample_transactions_df):
    vec = SpendingArchetypeClusterer().extract_features_from_statement(sample_transactions_df)[0]
    feats = dict(zip(FEATURE_NAMES, vec))
    spend = sample_transactions_df[sample_transactions_df["amount"] > 0]
    total = spend["amount"].sum()
    assert feats["market_ratio"] == pytest.approx((450 + 320) / total)
    assert feats["food_ratio"] == pytest.approx((120 + 110) / total)
    assert feats["tech_ratio"] == pytest.approx(12500 / total)
    assert feats["installment_ratio"] == pytest.approx(1 / len(spend))
    assert all(0.0 <= v <= 1.0 for v in vec)


def test_tech_heavy_statement_is_labelled_tech(clusterer, sample_transactions_df):
    res = clusterer.classify_user_archetype(sample_transactions_df)
    assert res["status"] == "ok" and res["archetype_key"] == "tech"
    assert set(res["user_coords"]) == {"x", "y", "z"} and res["population_is_synthetic"] is True
    assert len(res["population_samples"]) == 250 and sum(res["explained_variance_ratio"]) <= 1.0 + 1e-9


def test_result_is_deterministic_across_calls(clusterer, sample_transactions_df):
    a, b = (clusterer.classify_user_archetype(sample_transactions_df) for _ in range(2))
    assert a["population_samples"] == b["population_samples"] and a["user_coords"] == b["user_coords"]


@pytest.mark.parametrize("frame", [
    pd.DataFrame(columns=["date", "category", "amount"]), pd.DataFrame(),
    pd.DataFrame({"date": ["2026-01-01"] * 3, "category": ["Market"] * 3, "amount": [10.0, 20.0, -5.0]}),
])
def test_insufficient_data_gets_no_archetype(clusterer, frame):
    res = clusterer.classify_user_archetype(frame)
    assert res["status"] == "insufficient_data" and res["cluster_id"] is None and res["user_coords"] is None

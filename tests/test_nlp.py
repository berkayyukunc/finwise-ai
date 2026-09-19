"""POS sınıflandırıcı: sızıntısız protokol, abstain davranışı, altın set regresyon tabanı ve SHAP doğruluğu."""

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import GroupShuffleSplit

from src.nlp.dataset_generator import OTHER_CATEGORY, generate_pos_dataset
from src.nlp.explainer import POSShapExplainer, char_wb_ngrams
from src.nlp.models import DEFAULT_GOLD_PATH, POSClassifierPipeline


@pytest.fixture(scope="module")
def dataset() -> pd.DataFrame:
    return generate_pos_dataset(num_samples=6000, random_seed=7)


@pytest.fixture(scope="module")
def explainer() -> POSShapExplainer:
    return POSShapExplainer()


# ------------------------------------------------------------------ veri ve protokol
def test_dataset_is_deterministic_and_does_not_touch_global_rng():
    import random
    random.seed(123)
    expected = random.random()
    random.seed(123)
    a = generate_pos_dataset(600, random_seed=1)
    assert random.random() == expected, "Üretici global random durumunu değiştirmemeli"
    assert a.equals(generate_pos_dataset(600, random_seed=1))


def test_dataset_has_other_class_and_group_key(dataset):
    assert OTHER_CATEGORY in set(dataset["category"])
    assert {"group", "source"} <= set(dataset.columns)
    assert dataset["category"].nunique() == 12


def test_grouped_split_has_no_merchant_leakage(dataset):
    df = dataset.drop_duplicates("clean_text")
    tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=0).split(df, groups=df["group"]))
    assert not set(df["group"].iloc[tr]) & set(df["group"].iloc[te])


def test_training_reports_leaky_and_leakfree_metrics_separately(dataset):
    """Sızıntısız metrik iyimser metrikten düşük olmalı; ikisi aynıysa protokol bir şey ölçmüyordur."""
    pipe = POSClassifierPipeline()
    pipe.model.set_params(n_estimators=40)
    m = pipe.train(dataset)
    assert m["duplicates_removed"] > 0
    assert m["unseen_merchant"]["macro_f1"] < m["seen_merchant"]["macro_f1"]
    by_source = m["unseen_merchant"]["by_source"]
    # Anahtar kelimeli esnaf genellenir; anahtar kelimesiz görülmemiş marka genellenemez
    assert by_source["generic"]["accuracy"] > 0.85 > by_source["brand"]["accuracy"]


# ------------------------------------------------------------------------ çıkarım
@pytest.mark.parametrize("raw,expected", [
    ("MİGROS LEVENT İSTANBUL", "Market / Bakkal"),
    ("STARBUCKS KAHVE KADIKÖY", "Restoran / Yeme-İçme"),
    ("SHELL AKARYAKIT TR", "Akaryakıt / Ulaşım"),
    ("NETFLIX SUBSCRIPTION", "Dijital Servis / Abonelik"),
    ("KOÇTAŞ YAPI MARKETLERİ", "Ev / Yaşam / Fatura"),
    ("OZKAN ECZANESI", "Sağlık / Kozmetik"),          # görülmemiş esnaf, anahtar kelimeden
])
def test_known_patterns(classifier, raw, expected):
    (category, confidence), = classifier.predict(raw)
    assert category == expected and confidence >= classifier.abstain_threshold


def test_diacritic_variants_get_identical_predictions(classifier):
    a, b = classifier.predict(["KOCTAS", "KOÇTAŞ"])
    assert a == b


@pytest.mark.parametrize("raw", ["", "   ", "...", "AHMET YILMAZ HAVALE", "XQZW 12345", "OZKAN INSAAT TAAHHUT"])
def test_out_of_domain_inputs_abstain_instead_of_guessing(classifier, raw):
    (category, _), = classifier.predict(raw)
    assert category == OTHER_CATEGORY


def test_low_confidence_is_mapped_to_other(classifier):
    strict = POSClassifierPipeline(abstain_threshold=1.01)
    strict.vectorizer, strict.model, strict.classes_, strict.is_trained = classifier.vectorizer, classifier.model, classifier.classes_, True
    detail, = strict.predict_detailed("MIGROS")
    assert detail["abstained"] and detail["category"] == OTHER_CATEGORY and detail["top_class"] == "Market / Bakkal"


def test_predict_handles_batch_and_empty_list(classifier):
    assert classifier.predict([]) == []
    assert len(classifier.predict(["MIGROS", "SHELL"])) == 2


def test_untrained_pipeline_raises():
    with pytest.raises(RuntimeError):
        POSClassifierPipeline().predict("MIGROS")


def test_gold_set_regression_floor(classifier):
    """Manşet metrik. Taban, ölçülen değerin (0.83) bilinçli olarak altındadır: amaç regresyonu yakalamak."""
    gold = classifier.evaluate_gold(DEFAULT_GOLD_PATH)
    assert gold["n"] >= 150
    assert gold["macro_f1"] >= 0.75
    assert gold["confident_wrong_rate"] <= 0.10
    assert gold["by_difficulty"]["easy"]["accuracy"] >= 0.85


# --------------------------------------------------------------------------- SHAP
def test_char_wb_replica_matches_sklearn_analyzer(explainer):
    analyzer = explainer.vectorizer.build_analyzer()
    for text in ["migros atasehir <num>", "a bp x", "kfc", "h m"]:
        mine = [g for w in text.split() for g in char_wb_ngrams(w, explainer.vectorizer.ngram_range)]
        assert sorted(mine) == sorted(analyzer(text))


@pytest.mark.parametrize("raw", ["SHELL AKARYAKIT", "ARAS KARGO ARAC KIRALAMA", "MIGROS TIC AS LEVENT 042"])
def test_shap_is_additive_and_word_scores_do_not_double_count(explainer, raw):
    r = explainer.explain(raw)
    vec = explainer.vectorizer.transform([r["clean_text"]])
    k = explainer.classes_.index(r["predicted_class"])
    model_margin = explainer.model.predict(vec, raw_score=True)[0][k]
    assert r["margin"] == pytest.approx(model_margin, abs=1e-4)
    assert r["margin"] == pytest.approx(r["base_value"] + r["active_contribution"] + r["absent_contribution"])
    assert sum(s for _, s in r["word_attributions"]) == pytest.approx(r["active_contribution"], abs=1e-6)
    assert [w for w, _ in r["word_attributions"]] == r["clean_text"].split()


def test_shap_html_escapes_user_input(explainer):
    html_out = explainer.explain("<script>alert(1)</script> <img src=x onerror=alert(1)> MARKET")["html_explanation"]
    assert "<script" not in html_out and "<img" not in html_out and "&lt;script&gt;" in html_out


def test_shap_handles_empty_text(explainer):
    r = explainer.explain("")
    assert r["abstained"] and r["word_attributions"] == [] and np.isfinite(r["margin"])

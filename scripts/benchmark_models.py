"""
FinWise-AI: Ölçülmüş Model Karşılaştırması (Benchmark)
======================================================
XAI & NLP Arena sayfasındaki karşılaştırma tablosunun TEK kaynağıdır.
Tablodaki her sayı bu betik tarafından bu makinede ölçülür; ölçülmeyen
hiçbir model (ör. BERT) tabloya sayı olarak girmez.

Tüm adaylar aynı protokolle değerlendirilir:
- unseen_merchant : GroupShuffleSplit (test işyerleri eğitimde yok)
- gold            : elle yazılmış altın set (abstain uygulanmadan, saf argmax)
- latency         : tekil çağrı medyanı ve 1000'lik batch'te satır başı süre

Kullanım:  PYTHONPATH=. python scripts/benchmark_models.py
"""

import json
import logging
import pickle
import time

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline

from src.nlp.dataset_generator import generate_pos_dataset
from src.nlp.models import DEFAULT_GOLD_PATH, build_lightgbm, build_vectorizer
from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor

logger = logging.getLogger("benchmark")
OUTPUT_PATH = "data/models/benchmark_results.json"


def candidates():
    return {
        "Çoğunluk sınıfı (taban)": make_pipeline(TfidfVectorizer(), DummyClassifier(strategy="most_frequent")),
        "Word TF-IDF + Naive Bayes": make_pipeline(TfidfVectorizer(analyzer="word"), MultinomialNB(alpha=0.1)),
        "Char-wb TF-IDF + Logistic Regression": make_pipeline(build_vectorizer(), LogisticRegression(C=10.0, max_iter=2000)),
        "Char-wb TF-IDF + LightGBM (üretim)": make_pipeline(build_vectorizer(), build_lightgbm()),
    }


def measure_latency(model, sample: str) -> dict:
    model.predict([sample])  # ısınma
    singles = []
    for _ in range(100):
        t0 = time.perf_counter()
        model.predict_proba([sample]) if hasattr(model, "predict_proba") else model.predict([sample])
        singles.append((time.perf_counter() - t0) * 1000)
    batch = [sample] * 1000
    t0 = time.perf_counter()
    model.predict(batch)
    batch_total_ms = (time.perf_counter() - t0) * 1000
    return {"single_ms_median": float(np.median(singles)), "batch_ms_per_row": float(batch_total_ms / len(batch))}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    df = generate_pos_dataset().drop_duplicates(subset="clean_text").reset_index(drop=True)
    tr_idx, te_idx = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(df, groups=df["group"]))
    train, test = df.iloc[tr_idx], df.iloc[te_idx]

    gold = pd.read_csv(DEFAULT_GOLD_PATH)
    gold_clean = gold["raw_text"].map(TurkishFinancialNLPPreprocessor.clean_pos_text)

    results = []
    for name, model in candidates().items():
        t0 = time.perf_counter()
        model.fit(train["clean_text"], train["category"])
        fit_sec = time.perf_counter() - t0
        row = {
            "model": name,
            "unseen_merchant_macro_f1": float(f1_score(test["category"], model.predict(test["clean_text"]), average="macro")),
            "gold_macro_f1": float(f1_score(gold["category"], model.predict(gold_clean), average="macro")),
            "gold_accuracy": float((model.predict(gold_clean) == gold["category"]).mean()),
            "fit_sec": float(fit_sec),
            "pickle_mb": len(pickle.dumps(model)) / 1e6,
            **measure_latency(model, "migros atasehir <num>"),
        }
        results.append(row)
        logger.info("%-40s unseen-F1 %.3f | gold-F1 %.3f | %.2f ms tekil | %.3f ms/satır batch | %.1f MB",
                    name, row["unseen_merchant_macro_f1"], row["gold_macro_f1"], row["single_ms_median"],
                    row["batch_ms_per_row"], row["pickle_mb"])

    payload = {
        "protocol": "GroupShuffleSplit(test_size=0.2, seed=42) + elle yazılmış altın set; abstain uygulanmadı",
        "not_measured": "Türkçe BERT bu ortamda ölçülmedi (torch/transformers bağımlılığı 0 TL / CPU hedefinin dışında).",
        "results": results,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("Yazıldı: %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()

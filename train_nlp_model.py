"""
FinWise-AI: Model Eğitim ve Doğrulama Betiği
============================================
Sentetik veri setini üretir, LightGBM modelini sızıntısız protokolle değerlendirir,
altın set metriklerini hesaplar ve hem modeli hem metrik raporunu diske yazar.

Kullanım:  PYTHONPATH=. python train_nlp_model.py
"""

import json
import logging
import os

from src.nlp.dataset_generator import generate_pos_dataset
from src.nlp.models import DEFAULT_GOLD_PATH, DEFAULT_MODEL_PATH, POSClassifierPipeline

logger = logging.getLogger("train")
METRICS_PATH = "data/models/pos_model_metrics.json"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    logger.info("1. Sentetik Türkçe POS veri seti üretiliyor...")
    df = generate_pos_dataset(num_samples=30000, random_seed=42)
    os.makedirs("data", exist_ok=True)
    df.to_csv("data/synthetic_pos_dataset.csv", index=False)

    logger.info("2. Model eğitiliyor ve sızıntısız protokolle değerlendiriliyor...")
    pipeline = POSClassifierPipeline()
    metrics = pipeline.train(df, test_size=0.20)
    gold = pipeline.evaluate_gold(DEFAULT_GOLD_PATH)

    seen, unseen = metrics["seen_merchant"], metrics["unseen_merchant"]
    logger.info("\n--- MODEL PERFORMANS RAPORU ---")
    logger.info("Silinen kopya satır            : %d", metrics["duplicates_removed"])
    logger.info("Görülmüş marka  (iyimser)      : macro-F1 %.3f | acc %.3f | n=%d", seen["macro_f1"], seen["accuracy"], seen["n"])
    logger.info("Görülmemiş işyeri (sızıntısız) : macro-F1 %.3f | acc %.3f | n=%d", unseen["macro_f1"], unseen["accuracy"], unseen["n"])
    for src, m in unseen["by_source"].items():
        logger.info("    └ kaynak=%-8s acc %.3f (n=%d)", src, m["accuracy"], m["n"])
    logger.info("ALTIN SET (manşet metrik)      : macro-F1 %.3f | acc %.3f | n=%d", gold["macro_f1"], gold["accuracy"], gold["n"])
    logger.info("    kapsama %.1f%% | cevaplananlarda doğruluk %.3f | emin-ama-yanlış %.1f%% | ECE %.3f",
                gold["coverage"] * 100, gold["selective_accuracy"], gold["confident_wrong_rate"] * 100, gold["ece"])
    for level, m in gold["by_difficulty"].items():
        logger.info("    └ %-6s acc %.3f (n=%d)", level, m["accuracy"], m["n"])
    logger.info("\nAltın set sınıf raporu:\n%s", gold["report"])

    logger.info("3. Model ve metrikler diske yazılıyor...")
    pipeline.save(DEFAULT_MODEL_PATH)
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(pipeline.metadata, f, ensure_ascii=False, indent=2)
    logger.info("Metrik raporu: %s", METRICS_PATH)


if __name__ == "__main__":
    main()

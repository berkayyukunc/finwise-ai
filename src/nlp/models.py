"""
FinWise-AI: Türkçe POS Sınıflandırma Modeli (TF-IDF char_wb + LightGBM)
=======================================================================
Banka ekstrelerindeki POS açıklamalarını 11 harcama kategorisine ayırır;
emin olamadığında "Diğer / Belirsiz" der (abstain).

Değerlendirme Protokolü (Mülakat İçin):
Sentetik veride `train_test_split(shuffle=True)` yapmak şablon sızıntısına
(template leakage) yol açar: aynı marka hem eğitimde hem testte bulunur ve F1 ~%100
çıkar. Bu modülde üç ayrı metrik raporlanır ve hiçbiri diğerinin yerine kullanılmaz:

1. `seen_merchant`   : Kopyalar silindikten sonra rastgele ayrım. "Bildiğim markanın
                       yeni yazımını tanıyor muyum?" sorusunu ölçer (iyimser üst sınır).
2. `unseen_merchant` : GroupShuffleSplit. Test markaları eğitimde hiç yoktur.
                       "Genelleme" budur; alt kırılımları (brand / generic / other) ayrıca verilir.
3. `gold`            : Elle yazılmış, eğitimde hiç kullanılmamış altın set. Manşet metrik budur.

Neden char_wb + LightGBM?
char_wb n-gram'ları kısaltma ve kırpılmış POS metinlerine ('MGRS', 'CARREFOURSA KADIK')
kelime tabanlı modellerden dayanıklıdır. LightGBM CPU'da ~1 ms'de çalışır ve
TreeExplainer ile tam (exact) Shapley değeri verir. `colsample_bytree` bilinçli olarak
düşüktür: ağaçları tek bir n-gram'a yaslanmak yerine çok sayıda n-gram'a yayar.
"""

import logging
import os
import platform
import time
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split

from .dataset_generator import OTHER_CATEGORY
from .preprocessor import TurkishFinancialNLPPreprocessor

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = "data/models/pos_lightgbm.joblib"
DEFAULT_GOLD_PATH = "data/gold/pos_gold_set.csv"


def build_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), max_features=20000, sublinear_tf=True, min_df=2)


def build_lightgbm() -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.1,
        num_leaves=15,
        min_child_samples=10,
        colsample_bytree=0.3,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )


def expected_calibration_error(confidences: np.ndarray, correct: np.ndarray, n_bins: int = 10) -> float:
    """ECE: güven skoru ile gerçekleşen doğruluk arasındaki ağırlıklı ortalama fark."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (confidences > lo) & (confidences <= hi)
        if mask.any():
            ece += mask.mean() * abs(correct[mask].mean() - confidences[mask].mean())
    return float(ece)


class POSClassifierPipeline:
    """Türkçe POS harcama sınıflandırma boru hattı."""

    def __init__(self, abstain_threshold: float = 0.55):
        self.vectorizer = build_vectorizer()
        self.model = build_lightgbm()
        self.abstain_threshold = abstain_threshold
        self.classes_: List[str] = []
        self.is_trained: bool = False
        self.metadata: Dict[str, Any] = {}

    # ------------------------------------------------------------------ eğitim
    def _fit(self, texts: np.ndarray, labels: np.ndarray) -> None:
        X = self.vectorizer.fit_transform(texts)
        self.model.fit(X, labels)
        self.classes_ = list(self.model.classes_)
        self.is_trained = True

    def _score(self, texts: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
        probs = self.model.predict_proba(self.vectorizer.transform(texts))
        pred = np.asarray(self.classes_, dtype=object)[probs.argmax(axis=1)]
        return {
            "accuracy": float(accuracy_score(labels, pred)),
            "macro_f1": float(f1_score(labels, pred, average="macro")),
            "n": int(len(labels)),
        }

    def train(self, df: pd.DataFrame, test_size: float = 0.20) -> Dict[str, Any]:
        """
        Sızıntısız protokolle değerlendirir, ardından nihai modeli tüm veriyle eğitir.

        Args:
            df: ['clean_text', 'category'] (+ önerilen 'group', 'source') sütunları.
        Returns:
            Dict: 'seen_merchant', 'unseen_merchant' (+ kaynak kırılımı), 'duplicates_removed' ...
        """
        n_raw = len(df)
        df = df.drop_duplicates(subset="clean_text").reset_index(drop=True)
        duplicates_removed = n_raw - len(df)
        if "group" not in df.columns:
            logger.warning("'group' sütunu yok: sızıntısız (unseen_merchant) değerlendirme yapılamaz.")
            df = df.assign(group=df["clean_text"])
        if "source" not in df.columns:
            df = df.assign(source="unknown")

        texts = df["clean_text"].to_numpy(dtype=object)
        labels = df["category"].to_numpy(dtype=object)
        start = time.perf_counter()

        # 1. Görülmüş marka, görülmemiş yazım (iyimser)
        tr_idx, te_idx = train_test_split(np.arange(len(df)), test_size=test_size, random_state=42, stratify=labels)
        self._fit(texts[tr_idx], labels[tr_idx])
        seen = self._score(texts[te_idx], labels[te_idx])

        # 2. Görülmemiş işyeri (grup bazlı, sızıntısız)
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=42)
        tr_idx, te_idx = next(gss.split(texts, labels, groups=df["group"]))
        assert not set(df["group"].iloc[tr_idx]) & set(df["group"].iloc[te_idx]), "Grup sızıntısı!"
        self._fit(texts[tr_idx], labels[tr_idx])
        unseen = self._score(texts[te_idx], labels[te_idx])
        unseen["by_source"] = {
            src: self._score(texts[te_idx][mask], labels[te_idx][mask])
            for src in sorted(df["source"].unique())
            if (mask := (df["source"].iloc[te_idx] == src).to_numpy()).any()
        }
        unseen["report"] = classification_report(labels[te_idx], self.predict_labels(texts[te_idx]), zero_division=0)

        # 3. Nihai üretim modeli: tüm veri
        self._fit(texts, labels)
        train_time = time.perf_counter() - start

        metrics = {
            "seen_merchant": seen,
            "unseen_merchant": unseen,
            "duplicates_removed": int(duplicates_removed),
            "train_samples": int(len(df)),
            "num_features": int(len(self.vectorizer.get_feature_names_out())),
            "train_time_sec": float(train_time),
        }
        self.metadata = {
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "sklearn": sklearn.__version__,
            "lightgbm": lgb.__version__,
            "abstain_threshold": self.abstain_threshold,
            "metrics": {k: v for k, v in metrics.items() if k != "unseen_merchant"} | {
                "unseen_merchant": {k: v for k, v in unseen.items() if k != "report"}
            },
        }
        return metrics

    def predict_labels(self, clean_texts: np.ndarray) -> np.ndarray:
        """Abstain uygulanmamış ham argmax etiketleri (yalnızca değerlendirme için)."""
        probs = self.model.predict_proba(self.vectorizer.transform(clean_texts))
        return np.asarray(self.classes_, dtype=object)[probs.argmax(axis=1)]

    # ---------------------------------------------------------------- çıkarım
    def predict(self, raw_text: Union[str, List[str]]) -> List[Tuple[str, float]]:
        """
        POS metinlerinin kategorisini ve güven skorunu döner.
        Güven < abstain_threshold ya da temizlenmiş metin boş ise "Diğer / Belirsiz" döner;
        model zorla bir kategoriye atama yapmaz.

        Returns:
            List[Tuple[str, float]]: [(kategori, güven_0_1), ...]
        """
        return [(d["category"], d["confidence"]) for d in self.predict_detailed(raw_text)]

    def predict_detailed(self, raw_text: Union[str, List[str]]) -> List[Dict[str, Any]]:
        """predict() + ham en iyi sınıf ve abstain bayrağı."""
        if not self.is_trained:
            raise RuntimeError("Model henüz eğitilmedi veya yüklenmedi!")

        texts = [raw_text] if isinstance(raw_text, str) else list(raw_text)
        if not texts:
            return []
        clean_texts = [TurkishFinancialNLPPreprocessor.clean_pos_text(t) for t in texts]
        probs = self.model.predict_proba(self.vectorizer.transform(clean_texts))  # tek geçiş
        top_idx = probs.argmax(axis=1)

        results = []
        for i, clean in enumerate(clean_texts):
            top_class, conf = self.classes_[top_idx[i]], float(probs[i, top_idx[i]])
            if not clean:
                results.append({"category": OTHER_CATEGORY, "confidence": 0.0, "top_class": None, "abstained": True})
                continue
            abstained = conf < self.abstain_threshold and top_class != OTHER_CATEGORY
            results.append({
                "category": OTHER_CATEGORY if abstained else top_class,
                "confidence": conf,
                "top_class": top_class,
                "abstained": abstained,
            })
        return results

    def evaluate_gold(self, gold_path: str = DEFAULT_GOLD_PATH) -> Dict[str, Any]:
        """Elle etiketlenmiş altın set üzerinde uçtan uca (ham metin -> kategori) değerlendirme."""
        gold = pd.read_csv(gold_path)
        detailed = self.predict_detailed(gold["raw_text"].tolist())
        pred = np.array([d["category"] for d in detailed], dtype=object)
        conf = np.array([d["confidence"] for d in detailed])
        y = gold["category"].to_numpy(dtype=object)
        correct = pred == y
        answered = np.array([not d["abstained"] for d in detailed]) & (pred != OTHER_CATEGORY)

        out = {
            "n": int(len(gold)),
            "accuracy": float(correct.mean()),
            "macro_f1": float(f1_score(y, pred, average="macro")),
            "coverage": float(answered.mean()),
            "selective_accuracy": float(correct[answered].mean()) if answered.any() else 0.0,
            "confident_wrong_rate": float(((~correct) & (conf >= 0.90) & answered).mean()),
            "ece": expected_calibration_error(conf, correct.astype(float)),
            "by_difficulty": {
                str(level): {"n": int(m.sum()), "accuracy": float(correct[m].mean())}
                for level in ["easy", "medium", "hard", "field"]
                if (m := (gold["difficulty"] == level).to_numpy()).any()
            },
            "report": classification_report(y, pred, zero_division=0),
        }
        self.metadata.setdefault("metrics", {})["gold"] = {k: v for k, v in out.items() if k != "report"}
        return out

    # --------------------------------------------------------------- kalıcılık
    def save(self, filepath: str = DEFAULT_MODEL_PATH) -> None:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        joblib.dump({
            "vectorizer": self.vectorizer,
            "model": self.model,
            "classes_": self.classes_,
            "is_trained": self.is_trained,
            "abstain_threshold": self.abstain_threshold,
            "metadata": self.metadata,
        }, filepath, compress=3)
        logger.info("Model kaydedildi: %s", filepath)

    def load(self, filepath: str = DEFAULT_MODEL_PATH) -> "POSClassifierPipeline":
        data = joblib.load(filepath)
        self.vectorizer = data["vectorizer"]
        self.model = data["model"]
        self.classes_ = list(data["classes_"])
        self.is_trained = data["is_trained"]
        self.abstain_threshold = data.get("abstain_threshold", self.abstain_threshold)
        self.metadata = data.get("metadata", {})
        trained_with = self.metadata.get("sklearn")
        if trained_with and trained_with != sklearn.__version__:
            logger.warning("Model scikit-learn %s ile eğitilmiş, çalışan sürüm %s.", trained_with, sklearn.__version__)
        return self


def load_pipeline(filepath: Optional[str] = None) -> POSClassifierPipeline:
    return POSClassifierPipeline().load(filepath or DEFAULT_MODEL_PATH)

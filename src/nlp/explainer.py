"""
FinWise-AI: Açıklanabilir Yapay Zeka (XAI) ve SHAP Karar Açıklayıcısı
=====================================================================
LightGBM POS sınıflandırıcısının kararını SHAP TreeExplainer ile açıklar.

Tasarım İlkeleri (Mülakat İçin):
1. Toplanabilirlik (additivity) korunur:
       margin(sınıf) = base_value + Σ aktif n-gram katkıları + Σ metinde OLMAYAN n-gram katkıları
   Ağaç modellerinde "bu n-gram metinde yok" bilgisi de karar verdirir. Bu katkı
   gizlenmez; `absent_contribution` olarak ayrıca raporlanır ve arayüzde gösterilir.
2. N-gram -> kelime eşlemesi alt dize (substring) aramasıyla DEĞİL, char_wb
   analizörünün birebir kopyasıyla yapılır. Bir n-gram birden çok kelimede geçiyorsa
   SHAP değeri geçiş sayısıyla orantılı paylaştırılır; böylece kelime skorlarının toplamı
   aktif n-gram katkılarının toplamına tam olarak eşittir (çift sayım yok).
3. Isı haritası modelin GÖRDÜĞÜ temizlenmiş metin üzerinde çizilir. Ön işlemede silinen
   kelimeler ('TIC', 'A.S.') kararı etkilemediği için renklendirilmez.
4. Değerler log-odds (ham margin) uzayındadır; renk yoğunluğu metin içi en büyük
   mutlak katkıya göre normalize edilir (keyfi sabit eşik yoktur).
"""

import html
from collections import Counter
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import shap

from .models import DEFAULT_MODEL_PATH
from .preprocessor import TurkishFinancialNLPPreprocessor


def char_wb_ngrams(word: str, ngram_range: Tuple[int, int]) -> List[str]:
    """sklearn TfidfVectorizer(analyzer='char_wb') analizörünün tek kelime için birebir karşılığı."""
    padded = f" {word} "
    grams: List[str] = []
    for n in range(ngram_range[0], ngram_range[1] + 1):
        offset = 0
        grams.append(padded[offset:offset + n])
        while offset + n < len(padded):
            offset += 1
            grams.append(padded[offset:offset + n])
        if offset == 0:  # kelime n'den kısaysa yalnızca bir kez sayılır
            break
    return grams


class POSShapExplainer:
    """LightGBM POS Sınıflandırıcı için SHAP Açıklayıcısı."""

    def __init__(self, model_path: str = DEFAULT_MODEL_PATH):
        data = joblib.load(model_path)
        self.vectorizer = data["vectorizer"]
        self.model = data["model"]
        self.classes_ = list(data["classes_"])
        self.abstain_threshold = data.get("abstain_threshold", 0.55)
        self.vocabulary = self.vectorizer.vocabulary_
        self.feature_names = self.vectorizer.get_feature_names_out()
        self.explainer = shap.TreeExplainer(self.model)

    def _class_shap(self, dense_vec: np.ndarray, class_idx: int) -> Tuple[np.ndarray, float]:
        raw = self.explainer.shap_values(dense_vec)
        if isinstance(raw, list):
            values = raw[class_idx][0]
        elif raw.ndim == 3:
            values = raw[0, :, class_idx]
        else:
            values = raw[0]
        base = np.atleast_1d(self.explainer.expected_value)
        return values, float(base[class_idx] if len(base) > 1 else base[0])

    def word_attributions(self, clean_text: str, class_shap: np.ndarray) -> List[Tuple[str, float]]:
        """Aktif n-gram SHAP değerlerini kelimelere geçiş sayısıyla orantılı dağıtır."""
        words = clean_text.split()
        per_word = [Counter(g for g in char_wb_ngrams(w, self.vectorizer.ngram_range) if g in self.vocabulary) for w in words]
        totals: Counter = Counter()
        for counts in per_word:
            totals.update(counts)
        return [
            (w, float(sum(class_shap[self.vocabulary[g]] * c / totals[g] for g, c in counts.items())))
            for w, counts in zip(words, per_word)
        ]

    def explain(self, raw_text: str, top_k: int = 8) -> Dict[str, Any]:
        """
        Returns:
            Dict: raw_text, clean_text, predicted_class, confidence, abstained,
                  top_features [(ngram, shap)], word_attributions [(kelime, shap)],
                  base_value, active_contribution, absent_contribution, margin,
                  html_explanation
        """
        clean_text = TurkishFinancialNLPPreprocessor.clean_pos_text(raw_text)
        vec = self.vectorizer.transform([clean_text])
        probs = self.model.predict_proba(vec)[0]
        class_idx = int(np.argmax(probs))
        conf = float(probs[class_idx])

        class_shap, base_value = self._class_shap(vec.toarray(), class_idx)
        active_cols = vec.nonzero()[1]
        active = sorted(((str(self.feature_names[c]), float(class_shap[c])) for c in active_cols), key=lambda x: abs(x[1]), reverse=True)
        active_sum = float(class_shap[active_cols].sum())
        absent_sum = float(class_shap.sum() - active_sum)

        words = self.word_attributions(clean_text, class_shap)
        return {
            "raw_text": raw_text,
            "clean_text": clean_text,
            "predicted_class": self.classes_[class_idx],
            "confidence": conf,
            "abstained": (not clean_text) or conf < self.abstain_threshold,
            "top_features": active[:top_k],
            "word_attributions": words,
            "base_value": base_value,
            "active_contribution": active_sum,
            "absent_contribution": absent_sum,
            "margin": base_value + active_sum + absent_sum,
            "html_explanation": self.render_html(words),
        }

    @staticmethod
    def render_html(words: List[Tuple[str, float]]) -> str:
        """XSS korumalı kelime ısı haritası. Yeşil: sınıfı destekler, kırmızı: uzaklaştırır."""
        scale = max((abs(s) for _, s in words), default=0.0)
        spans = []
        for word, score in words:
            w_esc = html.escape(word)
            strength = abs(score) / scale if scale > 0 else 0.0
            if strength < 0.10:
                spans.append(f"<span style='padding: 3px 4px; margin: 2px; color: #64748b;'>{w_esc}</span>")
                continue
            rgb = "34, 197, 94" if score > 0 else "239, 68, 68"
            spans.append(
                f"<span title='SHAP {score:+.3f}' style='background-color: rgba({rgb}, {0.15 + 0.70 * strength:.2f}); "
                f"padding: 3px 6px; margin: 2px; border-radius: 4px; font-weight: 600;'>{w_esc}</span>"
            )
        return " ".join(spans)

"""
FinWise-AI: Harcama Anomali Tespiti (Sağlam İstatistik + Kurallar + Isolation Forest)
====================================================================================
Tasarım İlkeleri (Mülakat İçin):
1. "Her ekstrede anomali vardır" varsayımı YOKTUR. Eski sürümdeki sabit `contamination`
   parametresi, tamamen normal bir ekstrede bile işlemlerin %5-8'ini işaretliyordu.
   Artık işaretleme mutlak eşiklerle yapılır; anomalisiz sentetik ekstrelerde ölçülen yanlış alarm
   oranı %1'in altındadır (bkz. tests/test_predictive.py::test_anomaly_false_alarm_rate).
2. Aykırı değer, ölçütü kirletmez: ortalama/standart sapma yerine medyan/MAD kullanılır
   (Iglewicz-Hoaglin düzeltilmiş z-skoru, eşik 3.5). Klasik z-skorda 25.000 TL'lik işlem
   kendi standart sapmasını şişirip kendini gizler (masking); n=5'te |z| en fazla 1.79 olabilir.
3. Tutarlar sağa çarpıktır; z-skoru log1p(tutar) üzerinde hesaplanır ve yalnızca YÜKSEK
   yön işaretlenir (ucuz bir kahve risk değildir).
4. Alan kuralları istatistikten önce gelir: mükerrer çekim (aynı işyeri + aynı tutar, 3 gün içinde)
   ve geçmiş verildiğinde ilk kez görülen işyerinde yüksek tutar.
5. Isolation Forest karar verici değil, destekleyici kanıttır: yalnızca yeterli veri varsa (n >= 30)
   çalışır, sabit skor eşiği kullanır ve tek başına (robust z < 3.0 iken) işaretleme yapamaz.
   Ekstrelerde saat bilgisi olmadığı için "gece harcaması" gibi iddialarda bulunulmaz.
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor

logger = logging.getLogger(__name__)

SPEND_TYPES = {"EXPENSE", "INSTALLMENT", "FINANCING_FEE"}
FEATURE_NAMES = ["log_amount", "robust_z", "merchant_frequency", "dow_sin", "dow_cos"]


def modified_z_scores(values: np.ndarray) -> np.ndarray:
    """Iglewicz-Hoaglin: 0.6745 * (x - medyan) / MAD. MAD = 0 ise ortalama mutlak sapmaya düşer."""
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad > 0:
        return 0.6745 * (values - median) / mad
    mean_ad = np.mean(np.abs(values - median))
    if mean_ad > 0:
        return (values - median) / (1.253314 * mean_ad)
    return np.zeros_like(values, dtype=float)


class TransactionAnomalyDetector:
    """İşlem düzeyinde anomali tespit motoru."""

    ROBUST_Z_THRESHOLD = 3.5
    MIN_RATIO_TO_MEDIAN = 2.0       # istatistiksel olarak aykırı ama maddi olarak önemsiz farkları eler
    MIN_GROUP_SIZE = 5
    DUPLICATE_WINDOW_DAYS = 3
    IFOREST_MIN_SAMPLES = 30
    IFOREST_SCORE_THRESHOLD = 0.65  # -score_samples; 0.5 = "ortalama patika", ~1.0 = kolayca izole
    IFOREST_MIN_ROBUST_Z = 3.0

    def __init__(self, contamination: Optional[float] = None, random_state: int = 42):
        """`contamination` yalnızca geriye dönük uyumluluk içindir ve YOK SAYILIR (bkz. modül notu 1)."""
        if contamination is not None:
            logger.debug("contamination=%s yok sayıldı: işaretleme mutlak eşiklerle yapılır.", contamination)
        self.random_state = random_state

    def detect_anomalies(self, transactions_df: pd.DataFrame, history_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        'is_anomaly', 'anomaly_score' (robust z), 'anomaly_type', 'anomaly_reason', 'iforest_score'
        sütunları eklenmiş DataFrame döner. Satır sırası ve indeks korunur.
        """
        df = transactions_df.copy()
        defaults = {"category": "Genel", "amount": 0.0, "date": pd.NaT, "clean_description": "", "transaction_type": "EXPENSE", "installment_no": None}
        for col, default in defaults.items():
            if col not in df.columns:
                df[col] = default
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        df["category"] = df["category"].fillna("Genel").astype(str)

        df["is_anomaly"], df["anomaly_score"], df["iforest_score"] = False, 0.0, 0.0
        df["anomaly_type"], df["anomaly_reason"] = "", "Normal harcama"

        spend_mask = (df["amount"] > 0) & df["transaction_type"].astype(str).isin(SPEND_TYPES)
        df.loc[~spend_mask, "anomaly_reason"] = "Harcama değil (iade / ödeme / puan)"
        if spend_mask.sum() < self.MIN_GROUP_SIZE:
            df.loc[spend_mask, "anomaly_reason"] = "Yetersiz veri"
            return df

        spend = df[spend_mask].copy()
        spend["_dt"] = pd.to_datetime(spend["date"], errors="coerce")
        # Şube numarası korunur: 'MIGROS 042' ile 'MIGROS 117' farklı işyeridir (mükerrer çekim aynı şubede olur)
        spend["_merchant"] = spend["clean_description"].astype(str).map(lambda t: TurkishFinancialNLPPreprocessor.clean_pos_text(t, mask_numbers=False))
        spend["_log"] = np.log1p(spend["amount"])

        # 1. Sağlam tutar aykırılığı: kategori içinde (n >= 5), değilse tüm ekstrede
        global_z = modified_z_scores(spend["_log"].to_numpy())
        global_median = float(spend["amount"].median())
        spend["_z"], spend["_median"], spend["_scope"] = global_z, global_median, "tüm harcamalarınızın"
        for category, idx in spend.groupby("category").groups.items():
            if len(idx) >= self.MIN_GROUP_SIZE:
                spend.loc[idx, "_z"] = modified_z_scores(spend.loc[idx, "_log"].to_numpy())
                spend.loc[idx, "_median"] = float(spend.loc[idx, "amount"].median())
                spend.loc[idx, "_scope"] = f"'{category}' kategorisindeki harcamalarınızın"
        spend["_ratio"] = spend["amount"] / spend["_median"].clip(lower=1.0)
        amount_flag = (spend["_z"] > self.ROBUST_Z_THRESHOLD) & (spend["_ratio"] >= self.MIN_RATIO_TO_MEDIAN)

        # 2. Mükerrer çekim
        duplicate_flag = self._duplicate_mask(spend)

        # 3. Geçmişe göre yeni işyeri + yüksek tutar
        novelty_flag = self._novelty_mask(spend, history_df)

        # 4. Isolation Forest (destekleyici)
        spend["_if"] = self._iforest_scores(spend)
        if_flag = (spend["_if"] > self.IFOREST_SCORE_THRESHOLD) & (spend["_z"] > self.IFOREST_MIN_ROBUST_Z) & (spend["_ratio"] >= self.MIN_RATIO_TO_MEDIAN)

        types: List[str] = []
        reasons: List[str] = []
        for i, row in spend.iterrows():
            t, r = [], []
            if duplicate_flag[i]:
                t.append("duplicate_charge")
                r.append(f"Olası mükerrer çekim: aynı işyerinden {self.DUPLICATE_WINDOW_DAYS} gün içinde aynı tutar ({row['amount']:,.2f} TL) ikinci kez çekilmiş.")
            if amount_flag[i]:
                t.append("amount_outlier")
                r.append(f"Bu harcama ({row['amount']:,.2f} TL), {row['_scope']} medyanının ({row['_median']:,.2f} TL) {row['_ratio']:.1f} katı.")
            if novelty_flag[i]:
                t.append("new_merchant_high_amount")
                r.append("Geçmiş ekstrelerinizde hiç görülmeyen bir işyerinde, geçmiş harcamalarınızın %90'ından yüksek tutar.")
            if if_flag[i] and not amount_flag[i]:
                t.append("multivariate")
                r.append(f"Tutar, işyeri sıklığı ve gün örüntüsü birlikte alışılmışın dışında (izolasyon skoru {row['_if']:.2f}).")
            types.append("+".join(t))
            reasons.append(" ".join(r) if r else "Normal harcama")

        df.loc[spend.index, "anomaly_type"] = types
        df.loc[spend.index, "anomaly_reason"] = reasons
        df.loc[spend.index, "is_anomaly"] = [bool(t) for t in types]
        df.loc[spend.index, "anomaly_score"] = spend["_z"].clip(lower=0.0).round(3)
        df.loc[spend.index, "iforest_score"] = spend["_if"].round(3)
        return df

    # ------------------------------------------------------------------ kurallar
    def _duplicate_mask(self, spend: pd.DataFrame) -> pd.Series:
        """Aynı işyeri + aynı tutar, pencere içinde tekrar. Taksit ve abonelikler mükerrer sayılmaz."""
        flag = pd.Series(False, index=spend.index)
        eligible = spend[spend["installment_no"].isna() & ~spend["category"].str.contains("Abonelik|Finans", case=False) & (spend["_merchant"] != "")]
        for _, grp in eligible.groupby(["_merchant", eligible["amount"].round(2)]):
            if len(grp) < 2:
                continue
            grp = grp.sort_values("_dt")
            gaps = grp["_dt"].diff().dt.days
            flag.loc[grp.index[(gaps <= self.DUPLICATE_WINDOW_DAYS).to_numpy()]] = True
        return flag

    def _novelty_mask(self, spend: pd.DataFrame, history_df: Optional[pd.DataFrame]) -> pd.Series:
        flag = pd.Series(False, index=spend.index)
        if history_df is None or len(history_df) < 20 or "clean_description" not in history_df.columns:
            return flag
        hist_amounts = pd.to_numeric(history_df["amount"], errors="coerce")
        hist_amounts = hist_amounts[hist_amounts > 0]
        if hist_amounts.empty:
            return flag
        known = set(history_df["clean_description"].astype(str).map(lambda t: TurkishFinancialNLPPreprocessor.clean_pos_text(t, mask_numbers=False)))
        return ~spend["_merchant"].isin(known) & (spend["amount"] > float(hist_amounts.quantile(0.90)))

    def _iforest_scores(self, spend: pd.DataFrame) -> pd.Series:
        if len(spend) < self.IFOREST_MIN_SAMPLES:
            return pd.Series(0.0, index=spend.index)
        dow = spend["_dt"].dt.dayofweek.fillna(0).to_numpy()
        X = np.column_stack([
            spend["_log"].to_numpy(),
            spend["_z"].to_numpy(),
            spend["_merchant"].map(spend["_merchant"].value_counts(normalize=True)).to_numpy(),
            np.sin(2 * np.pi * dow / 7),
            np.cos(2 * np.pi * dow / 7),
        ])
        assert X.shape[1] == len(FEATURE_NAMES)
        model = IsolationForest(n_estimators=200, contamination="auto", random_state=self.random_state, n_jobs=1)
        return pd.Series(-model.fit(X).score_samples(X), index=spend.index)

"""
FinWise-AI: Alan Bilgisiyle Kurulmuş Harcama Tahmini (Domain-Informed Forecasting)
==================================================================================
Kredi kartında gelecek ayın borcu tek bir zaman serisi değildir; üç bileşenin toplamıdır:

    gelecek 30 gün = (A) bilinen taksitler  +  (B) tespit edilen abonelikler  +  (C) isteğe bağlı harcama
                     └──── deterministik ────┘  └──── yarı deterministik ────┘   └──── stokastik ────┘

(A) ve (B) ekstrede yazılıdır; bunları bir zaman serisi modeline "tahmin ettirmek" bilgiyi çöpe atmaktır.
Yalnızca (C) istatistiksel olarak modellenir.

Metodoloji (Mülakat İçin):
1. Veri sızıntısı yok: adaylar genişleyen pencereli walk-forward ile, GERÇEKTEN kullanılan modeller
   (ortalama, haftanın-günü ortalaması, Holt-Winters) üzerinde değerlendirilir; en düşük günlük
   RMSE'ye sahip aday seçilir. Holt-Winters ancak >= 56 gün (8 tam hafta) veri varsa adaydır.
2. Aylık %95 aralığı günlük sınırların toplamı DEĞİLDİR (bu, hataların tam korelasyonlu olduğunu
   varsayar ve bandı ~sqrt(30) kat şişirir). Aralık, artıkların 7 günlük hareketli blok bootstrap'i
   ile 30 günlük toplamın simülasyonundan elde edilir; ampirik kapsama testle doğrulanır.
3. Yetersiz veri dürüstçe bildirilir: < 14 gün veride sayı UYDURULMAZ, status='insufficient_data' döner.
"""

import logging
import warnings
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.holtwinters import ExponentialSmoothing

logger = logging.getLogger(__name__)

HORIZON_DAYS = 30
NON_SPEND_TYPES = {"REFUND", "PAYMENT", "POINT_REDEMPTION"}


# ----------------------------------------------------------------- aday modeller
def _predict_mean(train: pd.Series, horizon: int) -> np.ndarray:
    return np.full(horizon, float(train.mean()))


def _predict_dow_mean(train: pd.Series, horizon: int) -> np.ndarray:
    dow_mean = train.groupby(train.index.dayofweek).mean()
    future = pd.date_range(train.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")
    return np.array([dow_mean.get(d, train.mean()) for d in future.dayofweek], dtype=float)


def _fit_holt_winters(train: pd.Series):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        # Trend bileşeni bilinçli olarak yok: 30 günlük ufukta ekstrapole edilen doğrusal trend
        # kısa serilerde negatife ya da gerçek dışı değerlere kaçar.
        return ExponentialSmoothing(train, trend=None, seasonal="add", seasonal_periods=7, initialization_method="estimated").fit()


def _predict_holt_winters(train: pd.Series, horizon: int) -> np.ndarray:
    return np.clip(np.asarray(_fit_holt_winters(train).forecast(horizon), dtype=float), 0.0, None)


class SpendingForecaster:
    """Bileşen tabanlı 30 günlük harcama tahminleyicisi."""

    MIN_DAYS_ANY = 14           # bunun altında tahmin üretilmez
    MIN_DAYS_DOW = 28           # haftanın-günü ortalaması için en az 4 hafta
    MIN_DAYS_SEASONAL = 56      # Holt-Winters için en az 8 hafta
    SUBSCRIPTION_AMOUNT_TOLERANCE = 0.10
    N_BOOTSTRAP = 2000
    BLOCK_DAYS = 7

    def __init__(self, transactions_df: pd.DataFrame, random_state: int = 42):
        self._rng = np.random.default_rng(random_state)
        self.installments = pd.DataFrame()
        self.subscriptions = pd.DataFrame()
        self.daily_series = pd.Series(dtype=float)

        df = transactions_df.copy() if transactions_df is not None else pd.DataFrame()
        if len(df) and {"date", "amount"} <= set(df.columns):
            self._prepare(df)
        self.data_points = len(self.daily_series)

    # ---------------------------------------------------------------- hazırlık
    def _prepare(self, df: pd.DataFrame) -> None:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "amount"])
        if "transaction_type" in df.columns:
            df = df[~df["transaction_type"].isin(NON_SPEND_TYPES)]
        df = df[df["amount"] > 0]
        if df.empty:
            return
        for col in ("installment_no", "installment_total", "category", "clean_description", "transaction_type"):
            if col not in df.columns:
                df[col] = None

        is_installment = df["installment_no"].notna()
        self.installments = df[is_installment]

        rest = df[~is_installment]
        is_subscription = self._subscription_mask(rest)
        self.subscriptions = rest[is_subscription]

        is_fee = rest["transaction_type"].eq("FINANCING_FEE")  # faiz/vergi: tekrarlayacağı varsayılmaz
        discretionary = rest[~is_subscription & ~is_fee]

        # Seri, ekstrenin tamamını kapsar: harcamasız günler gerçek 0'dır
        full_index = pd.date_range(df["date"].min().normalize(), df["date"].max().normalize(), freq="D")
        self.daily_series = discretionary.groupby(discretionary["date"].dt.normalize())["amount"].sum().reindex(full_index, fill_value=0.0)

    def _subscription_mask(self, df: pd.DataFrame) -> pd.Series:
        """
        Abonelik = 'Abonelik' kategorisi YA DA aylık ritimle tekrarlayan işyeri:
        ardışık işlemler arası 25-35 gün ve tutar farkı <= %10. (Aynı markete iki farklı takvim
        ayında gitmek abonelik değildir; bu yüzden takvim ayı değil GÜN ARALIĞI ölçülür.)
        """
        by_category = df["category"].astype(str).str.contains("Abonelik", case=False, na=False)
        key = df["clean_description"].astype(str)
        recurring_keys = []
        for name, grp in df.assign(_key=key).sort_values("date").groupby("_key"):
            if len(grp) < 2:
                continue
            gaps = grp["date"].diff().dt.days.dropna()
            stable = (grp["amount"].max() - grp["amount"].min()) <= self.SUBSCRIPTION_AMOUNT_TOLERANCE * grp["amount"].max()
            if stable and gaps.between(25, 35).all():
                recurring_keys.append(name)
        return by_category | key.isin(recurring_keys)

    INSTALLMENT_RECENCY_DAYS = 35

    def _latest_installments(self) -> pd.DataFrame:
        """
        Birden çok ekstre birleştirildiğinde aynı alışveriş 1/4, 2/4, 3/4 olarak tekrar görünür.
        Gelecek ayın yükümlülüğü yalnızca SON dönemde görülen taksitlerden doğar; her alışveriş için
        (taksit numarası çıkarılmış açıklama + toplam taksit + tutar) anahtarıyla en ileri taksit tutulur.
        """
        inst = self.installments
        if inst.empty:
            return inst
        recent = inst[inst["date"] >= inst["date"].max() - pd.Timedelta(days=self.INSTALLMENT_RECENCY_DAYS)].copy()
        recent["_key"] = (recent["clean_description"].astype(str).str.replace(r"\d+", "#", regex=True) + "|"
                          + recent["installment_total"].astype(str) + "|" + recent["amount"].round(0).astype(str))
        return recent.sort_values("installment_no").groupby("_key", sort=False).tail(1)

    # ---------------------------------------------------- bilinen yükümlülükler
    def known_obligations(self) -> Dict[str, Any]:
        """Gelecek 30 günde kesinleşmiş / beklenen ödemeler."""
        inst_items = []
        for _, r in self._latest_installments().iterrows():
            no = int(r["installment_no"])
            total = int(r["installment_total"]) if pd.notna(r["installment_total"]) else None
            if total is not None and no >= total:
                continue  # son taksit ödendi
            inst_items.append({
                "description": str(r["clean_description"]), "amount": float(r["amount"]),
                "next_installment": no + 1, "installment_total": total,
                "remaining_after_next": (total - no - 1) if total else None,
            })
        sub_items = []
        if len(self.subscriptions):
            latest = self.subscriptions.sort_values("date").groupby(self.subscriptions["clean_description"].astype(str)).tail(1)
            sub_items = [{"description": str(r["clean_description"]), "amount": float(r["amount"])} for _, r in latest.iterrows()]
        return {
            "installments": inst_items,
            "subscriptions": sub_items,
            "installments_total": float(sum(i["amount"] for i in inst_items)),
            "subscriptions_total": float(sum(s["amount"] for s in sub_items)),
        }

    # ------------------------------------------------------------ model seçimi
    def _candidates(self, n_days: int) -> Dict[str, Callable[[pd.Series, int], np.ndarray]]:
        cands: Dict[str, Callable[[pd.Series, int], np.ndarray]] = {"Günlük ortalama (run-rate)": _predict_mean}
        if n_days >= self.MIN_DAYS_DOW:
            cands["Haftanın günü ortalaması"] = _predict_dow_mean
        if n_days >= self.MIN_DAYS_SEASONAL:
            cands["Holt-Winters (haftalık mevsimsellik)"] = _predict_holt_winters
        return cands

    def evaluate_walk_forward(self, n_splits: int = 3, horizon: int = 14) -> Dict[str, Any]:
        """
        Genişleyen pencereli walk-forward. Her katmanda model YALNIZCA geçmişle eğitilir,
        sonraki `horizon` gün tahmin edilir. Ölçülen modeller, üretimde kullanılan modellerdir.
        """
        y = self.daily_series
        min_train = max(self.MIN_DAYS_ANY, 2 * 7)
        while n_splits > 0 and len(y) < min_train + n_splits * horizon:
            n_splits -= 1
        if n_splits == 0 and len(y) >= min_train + 7:
            n_splits, horizon = 1, 7
        if n_splits == 0:
            return {"status": "insufficient_data", "candidates": {}, "selected": None, "n_splits": 0}

        scores: Dict[str, Dict[str, List[float]]] = {}
        for k in range(n_splits, 0, -1):
            cut = len(y) - k * horizon
            train, test = y.iloc[:cut], y.iloc[cut:cut + horizon]
            for name, fn in self._candidates(len(train)).items():
                try:
                    pred = fn(train, len(test))
                except Exception as exc:  # statsmodels yakınsama / tekillik hataları adayı eler
                    logger.warning("Aday '%s' katman %d'de başarısız: %s", name, k, exc)
                    continue
                s = scores.setdefault(name, {"daily_mae": [], "daily_rmse": [], "total_ape": []})
                s["daily_mae"].append(float(np.mean(np.abs(test.values - pred))))
                s["daily_rmse"].append(float(np.sqrt(np.mean((test.values - pred) ** 2))))
                s["total_ape"].append(float(abs(test.sum() - pred.sum()) / max(test.sum(), 1.0)))

        # Yalnızca tüm katmanlarda değerlendirilebilen adaylar adil karşılaştırılır
        full = {n: s for n, s in scores.items() if len(s["total_ape"]) == n_splits} or scores
        summary = {n: {"daily_rmse": float(np.mean(s["daily_rmse"])), "daily_mae": float(np.mean(s["daily_mae"])),
                       "horizon_total_ape": float(np.mean(s["total_ape"])), "folds": len(s["total_ape"])} for n, s in scores.items()}
        # Seçim ölçütü günlük RMSE'dir. (a) Ufuk toplamı hatası tam haftalarda mevsimselliği göremez (haftalık
        # örüntü toplamda sıfırlanır). (b) MAE'nin optimumu medyandır; seyrek/sıfır-şişkin harcama serisinde
        # toplamı sistematik düşük tahmin eder. RMSE'nin optimumu ortalamadır -> aylık toplam için yansız.
        selected = min(full, key=lambda n: np.mean(full[n]["daily_rmse"]))
        return {"status": "ok", "candidates": summary, "selected": selected, "n_splits": n_splits, "horizon": horizon}

    # ----------------------------------------------------------------- tahmin
    def _block_bootstrap_totals(self, daily_pred: np.ndarray, residuals: np.ndarray) -> np.ndarray:
        """Artıkların hareketli blok bootstrap'i ile 30 günlük toplamın dağılımı."""
        n, h, b = len(residuals), len(daily_pred), min(self.BLOCK_DAYS, len(residuals))
        n_blocks = int(np.ceil(h / b))
        starts = self._rng.integers(0, n - b + 1, size=(self.N_BOOTSTRAP, n_blocks))
        idx = (starts[:, :, None] + np.arange(b)[None, None, :]).reshape(self.N_BOOTSTRAP, -1)[:, :h]
        sims = np.clip(daily_pred[None, :] + residuals[idx], 0.0, None)
        return sims.sum(axis=1)

    def forecast_next_30_days(self) -> Dict[str, Any]:
        """
        Returns:
            Dict: status ('ok' | 'insufficient_data'), method_used, forecast_dates, forecast_daily_mean,
                  forecast_lower_95, forecast_upper_95, total_expected_monthly, risk_lower_monthly,
                  risk_upper_monthly, components, obligations, validation, historical_daily_*.
            status='insufficient_data' iken sayısal alanlar None'dır; sayı uydurulmaz.
        """
        obligations = self.known_obligations()
        committed = obligations["installments_total"] + obligations["subscriptions_total"]
        history = {
            "historical_daily_dates": [d.strftime("%Y-%m-%d") for d in self.daily_series.index],
            "historical_daily_amounts": [float(x) for x in self.daily_series.values],
        }

        if self.data_points < self.MIN_DAYS_ANY:
            return {
                "status": "insufficient_data",
                "method_used": f"Yetersiz veri ({self.data_points} gün; en az {self.MIN_DAYS_ANY} gün gerekir)",
                "forecast_dates": [], "forecast_daily_mean": [], "forecast_lower_95": [], "forecast_upper_95": [],
                "total_expected_monthly": None, "risk_lower_monthly": None, "risk_upper_monthly": None,
                "components": {"installments": obligations["installments_total"], "subscriptions": obligations["subscriptions_total"], "discretionary": None},
                "obligations": obligations, "validation": {"status": "insufficient_data"}, **history,
            }

        validation = self.evaluate_walk_forward()
        candidates = self._candidates(self.data_points)
        selected = validation["selected"] if validation.get("selected") in candidates else "Günlük ortalama (run-rate)"

        y = self.daily_series
        try:
            daily_pred = candidates[selected](y, HORIZON_DAYS)
        except Exception as exc:
            logger.warning("Seçilen model '%s' başarısız, run-rate'e düşüldü: %s", selected, exc)
            selected, daily_pred = "Günlük ortalama (run-rate)", _predict_mean(y, HORIZON_DAYS)
        daily_pred = np.clip(daily_pred, 0.0, None)

        # Artıklar: seçilen modelin örneklem içi hatası (blok bootstrap'in ham maddesi)
        if selected == "Haftanın günü ortalaması":
            fitted = y.groupby(y.index.dayofweek).transform("mean").values
        elif selected.startswith("Holt-Winters"):
            fitted = np.asarray(_fit_holt_winters(y).fittedvalues, dtype=float)
        else:
            fitted = np.full(len(y), y.mean())
        residuals = y.values - fitted

        totals = self._block_bootstrap_totals(daily_pred, residuals)
        lo_q, hi_q = np.quantile(residuals, [0.025, 0.975])
        future = pd.date_range(y.index[-1] + pd.Timedelta(days=1), periods=HORIZON_DAYS, freq="D")
        discretionary = float(daily_pred.sum())

        return {
            "status": "ok",
            "method_used": selected,
            "forecast_dates": [d.strftime("%Y-%m-%d") for d in future],
            "forecast_daily_mean": [float(x) for x in daily_pred],
            "forecast_lower_95": [float(max(0.0, x + lo_q)) for x in daily_pred],
            "forecast_upper_95": [float(x + hi_q) for x in daily_pred],
            "total_expected_monthly": committed + discretionary,
            "risk_lower_monthly": committed + float(np.quantile(totals, 0.025)),
            "risk_upper_monthly": committed + float(np.quantile(totals, 0.975)),
            "components": {"installments": obligations["installments_total"], "subscriptions": obligations["subscriptions_total"], "discretionary": discretionary},
            "obligations": obligations,
            "validation": validation,
            **history,
        }


def empirical_interval_coverage(n_sim: int = 100, n_history: int = 90, seed: int = 0) -> Optional[float]:
    """Aylık %95 aralığının ampirik kapsaması (haftalık mevsimsellik + gürültülü sentetik süreçte)."""
    hits = []
    for s in range(n_sim):
        rng = np.random.default_rng(seed + s)
        n = n_history + HORIZON_DAYS
        amounts = np.clip(300 + 80 * np.sin(2 * np.pi * np.arange(n) / 7) + rng.normal(0, 120, n), 0, None)
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        hist = pd.DataFrame({"date": dates[:n_history], "amount": amounts[:n_history], "transaction_type": "EXPENSE"})
        fc = SpendingForecaster(hist, random_state=s).forecast_next_30_days()
        actual = amounts[n_history:].sum()
        hits.append(fc["risk_lower_monthly"] <= actual <= fc["risk_upper_monthly"])
    return float(np.mean(hits)) if hits else None

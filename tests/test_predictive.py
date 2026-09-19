"""Tahmin, anomali ve servet simülatörü: davranış, istatistiksel geçerlilik ve uç durumlar."""

import numpy as np
import pandas as pd
import pytest

from src.predictive.anomaly import TransactionAnomalyDetector, modified_z_scores
from src.predictive.forecasting import SpendingForecaster, empirical_interval_coverage
from src.predictive.wealth_simulator import DISCLAIMER, MarketAssumptions, WealthSimulator


def daily_frame(n_days: int, seed: int = 1, weekly: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    season = 80 * np.sin(2 * np.pi * np.arange(n_days) / 7) if weekly else 0
    return pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n_days), "amount": np.clip(300 + season + rng.normal(0, 60, n_days), 1, None), "transaction_type": "EXPENSE"})


# ===================================================================== TAHMİN
@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"date": [], "amount": []}), pd.DataFrame({"x": [1]}),
                                   pd.DataFrame({"date": ["2026-01-01"], "amount": [100.0]})])
def test_insufficient_data_never_fabricates_numbers(frame):
    fc = SpendingForecaster(frame).forecast_next_30_days()
    assert fc["status"] == "insufficient_data"
    assert fc["total_expected_monthly"] is None and fc["risk_upper_monthly"] is None and fc["forecast_daily_mean"] == []


def test_refunds_payments_and_points_are_excluded_exactly():
    base = daily_frame(20, weekly=False)
    noise = pd.DataFrame({"date": ["2026-01-05", "2026-01-06", "2026-01-07"], "amount": [-5000.0, -9000.0, 0.0], "transaction_type": ["REFUND", "PAYMENT", "POINT_REDEMPTION"]})
    clean = SpendingForecaster(base).forecast_next_30_days()
    noisy = SpendingForecaster(pd.concat([base, noise])).forecast_next_30_days()
    assert noisy["total_expected_monthly"] == pytest.approx(clean["total_expected_monthly"])
    assert clean["total_expected_monthly"] == pytest.approx(base["amount"].mean() * 30)


def test_known_obligations_are_separated_from_stochastic_part():
    df = daily_frame(30, weekly=False).assign(clean_description="MARKET", category="Market / Bakkal", installment_no=None, installment_total=None)
    extra = pd.DataFrame({
        "date": ["2026-01-10", "2026-01-11", "2026-01-12"], "amount": [1250.0, 900.0, 199.99], "transaction_type": ["INSTALLMENT", "INSTALLMENT", "EXPENSE"],
        "clean_description": ["MEDIAMARKT (03/06)", "VATAN (06/06)", "NETFLIX"], "category": ["Elektronik / Teknoloji"] * 2 + ["Dijital Servis / Abonelik"],
        "installment_no": [3, 6, None], "installment_total": [6, 6, None]})
    fc = SpendingForecaster(pd.concat([df, extra], ignore_index=True)).forecast_next_30_days()

    ob = fc["obligations"]
    assert [i["description"] for i in ob["installments"]] == ["MEDIAMARKT (03/06)"], "Son taksiti (06/06) ödenen kalem gelecek aya taşınmaz"
    assert ob["installments"][0]["next_installment"] == 4 and ob["installments"][0]["remaining_after_next"] == 2
    assert ob["subscriptions_total"] == pytest.approx(199.99)
    c = fc["components"]
    assert fc["total_expected_monthly"] == pytest.approx(c["installments"] + c["subscriptions"] + c["discretionary"])
    assert c["discretionary"] == pytest.approx(df["amount"].mean() * 30, rel=0.02), "Taksit/abonelik stokastik seriye karışmamalı"


def test_same_merchant_in_two_calendar_months_is_not_a_subscription():
    df = daily_frame(30, weekly=False).assign(clean_description="X", category="Market / Bakkal")
    visits = pd.DataFrame({"date": ["2026-01-28", "2026-02-03"], "amount": [465.0, 470.0], "transaction_type": "EXPENSE", "clean_description": "CARREFOURSA", "category": "Market / Bakkal"})
    assert SpendingForecaster(pd.concat([df, visits])).known_obligations()["subscriptions"] == []
    monthly = visits.assign(date=["2026-01-03", "2026-02-02"], clean_description="SPOR SALONU UYELIK", category="Eğlence / Hobi / Kitap")
    assert len(SpendingForecaster(pd.concat([daily_frame(40, weekly=False).assign(clean_description="X", category="M"), monthly])).known_obligations()["subscriptions"]) == 1


def test_walk_forward_evaluates_the_models_that_are_actually_used():
    fc = SpendingForecaster(daily_frame(120)).forecast_next_30_days()
    val = fc["validation"]
    assert any(name.startswith("Holt-Winters") for name in val["candidates"]), "HW doğrulanmadan kullanılamaz"
    assert fc["method_used"] == val["selected"] and fc["method_used"] in val["candidates"]
    best = min(v["daily_rmse"] for v in val["candidates"].values() if v["folds"] == val["n_splits"])
    assert val["candidates"][val["selected"]]["daily_rmse"] == pytest.approx(best)


def test_holt_winters_is_not_a_candidate_below_eight_weeks():
    val = SpendingForecaster(daily_frame(40)).forecast_next_30_days()["validation"]
    assert not any(name.startswith("Holt-Winters") for name in val["candidates"])


def test_weekly_seasonality_is_learned():
    fc = SpendingForecaster(daily_frame(120, seed=3)).forecast_next_30_days()
    assert fc["method_used"] != "Günlük ortalama (run-rate)"
    assert np.std(fc["forecast_daily_mean"]) > 20


def test_monthly_interval_is_calibrated_and_not_sum_of_daily_bounds():
    fc = SpendingForecaster(daily_frame(90)).forecast_next_30_days()
    naive_upper = sum(fc["forecast_upper_95"])  # eski (hatalı) yöntem: tam korelasyon varsayımı
    assert fc["risk_lower_monthly"] < fc["total_expected_monthly"] < fc["risk_upper_monthly"] < naive_upper
    assert (fc["risk_upper_monthly"] - fc["total_expected_monthly"]) < 0.5 * (naive_upper - fc["total_expected_monthly"])
    coverage = empirical_interval_coverage(n_sim=60)
    assert 0.85 <= coverage <= 0.99, f"Nominal %95 aralığın ampirik kapsaması: {coverage:.2%}"


def test_forecast_is_deterministic():
    a, b = (SpendingForecaster(daily_frame(90)).forecast_next_30_days() for _ in range(2))
    assert a["risk_upper_monthly"] == b["risk_upper_monthly"]


# ===================================================================== ANOMALİ
def clean_statement(seed: int, n: int = 60) -> pd.DataFrame:
    r = np.random.default_rng(seed)
    return pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n, freq="12h").strftime("%Y-%m-%d"), "category": r.choice(["Market", "Restoran", "Akaryakıt", "Giyim"], n),
                         "clean_description": [f"ISYERI {i}" for i in r.integers(0, 15, n)], "amount": np.round(r.lognormal(5.3, 0.45, n), 2)})


def test_anomaly_false_alarm_rate_on_clean_statements_is_below_two_percent():
    """Eski sürüm sabit contamination ile HER ekstrede %5-8 işaretliyordu."""
    flagged = [TransactionAnomalyDetector().detect_anomalies(clean_statement(s))["is_anomaly"].sum() for s in range(40)]
    assert np.mean(flagged) / 60 < 0.02
    assert np.mean(np.array(flagged) == 0) > 0.5, "Temiz ekstrelerin çoğunda hiç alarm olmamalı"


def test_contamination_argument_is_ignored():
    df = clean_statement(0)
    a = TransactionAnomalyDetector(contamination=0.30).detect_anomalies(df)["is_anomaly"].sum()
    assert a == TransactionAnomalyDetector().detect_anomalies(df)["is_anomaly"].sum()


def test_injected_outlier_is_found_with_correct_explanation():
    df = clean_statement(7)
    df.loc[10, ["category", "amount", "clean_description"]] = ["Restoran", 9500.0, "LUKS RESTORAN"]
    out = TransactionAnomalyDetector().detect_anomalies(df)
    row = out.loc[10]
    assert row["is_anomaly"] and "amount_outlier" in row["anomaly_type"]
    assert "medyan" in row["anomaly_reason"] and "9,500.00" in row["anomaly_reason"]
    assert out["is_anomaly"].sum() <= 2


def test_masking_effect_small_sample():
    """Klasik z-skoru n=6'da 25.000 TL'yi gizleyebilir (|z|max = 2.04); medyan/MAD gizlemez."""
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=6).strftime("%Y-%m-%d"), "clean_description": list("ABCDEF"),
                       "category": ["Restoran", "Market", "Restoran", "Market", "Restoran", "Restoran"], "amount": [80.0, 250.0, 95.0, 310.0, 25000.0, 75.0]})
    classic_z = (df["amount"] - df["amount"].mean()) / df["amount"].std()
    assert classic_z.max() < 2.1
    out = TransactionAnomalyDetector().detect_anomalies(df)
    assert out["is_anomaly"].tolist() == [False, False, False, False, True, False]


def test_duplicate_charge_rule_and_its_exemptions():
    df = clean_statement(11)
    df.loc[20, ["clean_description", "amount", "date"]] = ["ZARA ISTINYE 042", 1499.90, "2026-01-11"]
    df.loc[21, ["clean_description", "amount", "date"]] = ["ZARA ISTINYE 042", 1499.90, "2026-01-12"]
    df.loc[30, ["clean_description", "amount", "date"]] = ["ZARA ISTINYE 117", 1499.90, "2026-01-12"]      # farklı şube: mükerrer değil
    df.loc[40, ["clean_description", "amount", "date", "category"]] = ["NETFLIX", 199.99, "2026-01-02", "Dijital Servis / Abonelik"]
    df.loc[41, ["clean_description", "amount", "date", "category"]] = ["NETFLIX", 199.99, "2026-01-03", "Dijital Servis / Abonelik"]
    out = TransactionAnomalyDetector().detect_anomalies(df)
    assert "duplicate_charge" in out.loc[21, "anomaly_type"]
    assert "duplicate_charge" not in out.loc[20, "anomaly_type"], "İlk çekim değil, tekrarı işaretlenir"
    assert "duplicate_charge" not in out.loc[30, "anomaly_type"]
    assert not out.loc[[40, 41], "is_anomaly"].any(), "Abonelikler mükerrer sayılmaz"


def test_refunds_and_payments_are_never_anomalies_and_index_is_preserved():
    df = clean_statement(5)
    df.loc[3, "amount"], df.loc[4, "amount"] = -99999.0, 0.0
    df.index = df.index + 100
    out = TransactionAnomalyDetector().detect_anomalies(df)
    assert list(out.index) == list(df.index) and len(out) == len(df)
    assert not out.loc[[103, 104], "is_anomaly"].any()


def test_new_merchant_high_amount_requires_history():
    history = clean_statement(1)
    current = clean_statement(2)
    current.loc[0, ["clean_description", "amount"]] = ["HIC GORULMEMIS KUYUMCU", float(history["amount"].quantile(0.95))]
    det = TransactionAnomalyDetector()
    assert "new_merchant" not in det.detect_anomalies(current).loc[0, "anomaly_type"]
    assert "new_merchant_high_amount" in det.detect_anomalies(current, history_df=history).loc[0, "anomaly_type"]


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"amount": [10.0, 20.0]}), pd.DataFrame({"amount": ["x", None, "5"], "date": ["bad", None, "2026-01-01"]})])
def test_anomaly_degenerate_inputs(frame):
    out = TransactionAnomalyDetector().detect_anomalies(frame)
    assert len(out) == len(frame) and not out["is_anomaly"].any()


def test_modified_z_handles_zero_mad():
    z = modified_z_scores(np.array([5.0, 5.0, 5.0, 5.0, 50.0]))
    assert np.isfinite(z).all() and z[-1] > 3.5
    assert (modified_z_scores(np.array([5.0] * 5)) == 0).all()


# ===================================================================== SERVET
def test_fisher_identity_and_zero_real_return():
    assert WealthSimulator.calculate_real_rate(0.30, 0.30) == pytest.approx(0.0)
    assert (1 + WealthSimulator.calculate_real_rate(0.52, 0.30)) * 1.30 == pytest.approx(1.52)
    flat = MarketAssumptions(annual_inflation=0.30, nominal_annual_returns={"Para Piyasası / Mevduat Fonu": 0.30})
    sim = WealthSimulator.simulate_dca(1000.0, 2, assumptions=flat)
    assert sim["results_by_asset"]["Para Piyasası / Mevduat Fonu"]["final_real_value"] == pytest.approx(24000.0, abs=0.05)


@pytest.mark.parametrize("years", [1, 3, 7])
def test_headline_reflects_actual_horizon(years):
    sim = WealthSimulator.simulate_dca(2000.0, years, monthly_expense_tl=30000.0)
    assert f"{years} yıl" in sim["freedom_metric"]["headline"]
    assert len(sim["results_by_asset"]["BIST 100 Hisse Fonu"]["real_curve"]) == years * 12
    assert sim["disclaimer"] == DISCLAIMER and "tavsiyesi değildir" in DISCLAIMER


def test_freedom_days_not_invented_without_expense_input():
    assert WealthSimulator.simulate_dca(2000.0, 3)["freedom_metric"]["life_days_bought_per_month"] is None


@pytest.mark.parametrize("saving,years", [(-1.0, 3), (1000.0, 0)])
def test_simulator_rejects_invalid_input(saving, years):
    with pytest.raises(ValueError):
        WealthSimulator.simulate_dca(saving, years)

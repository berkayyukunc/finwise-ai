"""Uçtan uca: PDF -> Document AI -> NLP -> XAI -> tahmin -> anomali -> kümeleme -> copilot. Fazlar arası sözleşmeyi doğrular."""

import pytest

from src.copilot.agent import FinancialCopilot
from src.nlp.dataset_generator import OTHER_CATEGORY
from src.predictive.forecasting import SpendingForecaster
from src.utils.statement_loader import process_statement
from tests.conftest import BANK_KEYS


@pytest.mark.parametrize("idx", range(len(BANK_KEYS)), ids=BANK_KEYS)
def test_full_pipeline_on_every_layout(bank_samples, classifier, idx):
    _, pdf_bytes, truth = bank_samples[idx]
    stmt, df = process_statement(pdf_bytes, classifier=classifier)

    # Faz 1 -> 2 sözleşmesi
    assert len(df) == truth["transaction_count"] and stmt.checksum_valid is True
    assert df["category"].notna().all() and df["confidence"].between(0, 1).all()
    spend = df[df["amount"] > 0]
    assert (spend["category"] != OTHER_CATEGORY).mean() > 0.80, "Bilinen işyerlerinin çoğu kategorilenmeli"
    merchants = dict(zip(df["raw_description"].str[:6], df["category"]))
    assert merchants.get("NETFLI", "Dijital Servis / Abonelik") == "Dijital Servis / Abonelik"

    # Faz 3: anomali sütunları, iadeler işaretlenmez
    assert {"is_anomaly", "anomaly_type", "anomaly_reason"} <= set(df.columns)
    assert not df.loc[df["amount"] <= 0, "is_anomaly"].any()

    # Faz 4: tahmin = bileşenlerin toplamı; ekstredeki (3/6) taksit gelecek aya taşınır
    fc = SpendingForecaster(df).forecast_next_30_days()
    assert fc["status"] == "ok" and fc["obligations"]["installments_total"] == pytest.approx(1250.0)
    assert fc["total_expected_monthly"] == pytest.approx(sum(fc["components"].values()))
    assert fc["risk_lower_monthly"] <= fc["total_expected_monthly"] <= fc["risk_upper_monthly"]

    # Faz 5: copilot, ayrıştırılan borcu ve toplamı birebir raporlar
    copilot = FinancialCopilot(df, total_debt=stmt.total_debt, bank_name=stmt.bank_name, min_payment=stmt.min_payment)
    copilot.ask("özet")
    assert copilot.last_result.data["total_debt"] == pytest.approx(truth["total_debt"])
    assert copilot.last_result.data["total_spend"] == pytest.approx(spend["amount"].sum())
    assert copilot.last_result.data["total_refunds"] == pytest.approx(truth["refund_total"])

    # Gizlilik: hattın hiçbir çıktısında PII yok
    blob = df.to_csv() + copilot.ask("En çok nereye harcadım?")
    assert truth["card_holder"] not in blob and truth["tc"] not in blob


def test_multiple_statements_are_merged_and_duplicates_skipped(bank_samples, classifier):
    from src.utils.statement_loader import process_statements
    files = [(f"{key}.pdf", data) for key, data, _ in bank_samples[:3]]
    statements, df, errors = process_statements(files + [files[0], ("bozuk.pdf", b"pdf degil")], classifier=classifier)
    assert len(statements) == 3 and all(s.checksum_valid for s in statements)
    assert len(df) == sum(len(s.transactions) for s in statements) and df["statement"].nunique() == 3
    assert len(errors) == 2 and any("ikinci kez" in e for e in errors) and any("bozuk.pdf" in e for e in errors)
    assert df["date"].is_monotonic_increasing and "is_anomaly" in df.columns

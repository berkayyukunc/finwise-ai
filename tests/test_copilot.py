"""Copilot: niyet yönlendirme, Türkçe büyük/küçük harf, dürüst 'bilmiyorum' ve sayıların kaynağı."""

import re

import pandas as pd
import pytest

from src.copilot.agent import FinancialCopilot, tl


@pytest.fixture
def copilot(sample_transactions_df) -> FinancialCopilot:
    return FinancialCopilot(sample_transactions_df, total_debt=16049.99, bank_name="Garanti BBVA", min_payment=3210.0)


@pytest.mark.parametrize("question,intent", [
    ("Bu ay markete ne kadar harcadım?", "category_spend"),
    ("GIDA harcamam ne kadar?", "category_spend"),              # 'GIDA'.lower() == 'gida' ≠ 'gıda' tuzağı
    ("DIŞARIDA ne kadar yedim?", "category_spend"),             # 'DIŞARI'.lower() == 'dişari' tuzağı
    ("Akaryakıta ne kadar gitti?", "category_spend"),
    ("TAKSİYE ne kadar verdim?", "category_spend"),
    ("Gelecek ay taksitlerim ne kadar?", "obligations"),        # 'taksit' ≠ 'taksi'
    ("En çok markete mi restorana mı harcadım?", "compare_categories"),
    ("Starbucks'a ne kadar gitti?", "merchant_spend"),
    ("En çok nereye para kaçırdım?", "top_merchants"),
    ("Şüpheli işlem var mı?", "anomalies"),
    ("İadelerim ne kadar?", "refunds"),
    ("Bana tasarruf önerisi ver", "savings"),
    ("Genel durum özeti", "summary"),
    ("Geçen aya göre market harcamam arttı mı?", "unsupported_temporal"),
    ("Bugün hava nasıl?", "unknown"),
    ("", "unknown"),
])
def test_routing(copilot, question, intent):
    assert copilot.route(question)[0] == intent


def test_category_answer_is_exact_and_separates_refunds(copilot):
    answer = copilot.ask("Giyime ne kadar harcadım?")
    data = copilot.last_result.data
    assert data["gross_spend"] == 1850.0 and data["refunds"] == 350.0 and data["net_spend"] == 1500.0
    assert "1.850,00 TL" in answer and "350,00 TL" in answer and "1.500,00 TL" in answer


def test_market_and_food_totals(copilot):
    assert "770,00 TL" in copilot.ask("Bu ay markete ne kadar harcadım?")
    assert "230,00 TL" in copilot.ask("Kahveye ve yemeğe ne kadar gitti?")
    assert copilot.last_result.data["transaction_count"] == 2


def test_comparison_orders_and_diffs(copilot):
    copilot.ask("Markete mi restorana mı daha çok harcadım?")
    data = copilot.last_result.data
    assert [i["gross_spend"] for i in data["items"]] == [770.0, 230.0] and data["difference_top_two"] == 540.0


def test_summary_total_is_consistent_with_category_totals(copilot, sample_transactions_df):
    copilot.ask("özet")
    total = copilot.last_result.data["total_spend"]
    assert total == pytest.approx(sample_transactions_df.loc[sample_transactions_df["amount"] > 0, "amount"].sum())
    assert copilot.last_result.data["total_refunds"] == 350.0


def test_obligations_come_from_forecaster(copilot):
    answer = copilot.ask("Gelecek ay taksitlerim ne kadar?")
    data = copilot.last_result.data
    assert data["installments_total"] == 12500.0 and data["subscriptions_total"] == 199.99
    assert "APPLE STORE" in answer and "4/6" in answer


def test_unanswerable_questions_are_refused_not_guessed(copilot):
    for q in ["Geçen aya göre artış var mı?", "Bugün hava nasıl?"]:
        answer = copilot.ask(q)
        assert "yanıtlayamıyorum" in answer or "anlayamadım" in answer
        assert not re.search(r"\d+,\d{2} TL", answer), "Anlaşılmayan soruya tutar içeren yanıt verilmemeli"


ALL_QUESTIONS = ["özet", "Markete ne kadar?", "Markete mi restorana mı?", "Starbucks'a ne kadar gitti?", "En çok nereye?", "İadelerim?",
                 "Gelecek ay taksitlerim?", "Tasarruf önerisi ver", "Şüpheli işlem var mı?"]


@pytest.mark.parametrize("question", ALL_QUESTIONS)
def test_every_amount_in_answer_comes_from_tool_result(copilot, question):
    """'Sıfır halüsinasyon' iddiasının testi: şablon katmanı sayı üretemez."""
    answer = copilot.ask(question)
    known = {tl(v) for v in copilot.last_result.amounts()}
    for amount in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2} TL", answer):
        assert amount in known, f"{amount!r} araç çıktısında yok"


def test_anomaly_tool_reports_flagged_rows(sample_transactions_df):
    df = sample_transactions_df.assign(is_anomaly=[False] * 5 + [True] + [False] * 3, anomaly_reason="test nedeni")
    answer = FinancialCopilot(df, 0.0).ask("Şüpheli işlem var mı?")
    assert "APPLE STORE" in answer and "test nedeni" in answer
    assert "çalıştırılmamış" in FinancialCopilot(sample_transactions_df, 0.0).ask("anomali var mı")


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"amount": [100.0]}), pd.DataFrame({"raw_description": ["X"], "amount": ["bozuk"]})])
def test_degenerate_frames_do_not_crash(frame):
    cp = FinancialCopilot(frame, total_debt=0.0)
    for q in ALL_QUESTIONS:
        assert isinstance(cp.ask(q), str)


def test_tl_formatting():
    assert tl(1234567.891) == "1.234.567,89 TL" and tl(0) == "0,00 TL"

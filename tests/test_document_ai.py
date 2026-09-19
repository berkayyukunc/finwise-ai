"""Document AI: ayrıştırma doğruluğu (truth.json'a karşı), sağlama, parmak izi, PII sızıntı testleri."""

import random

import pymupdf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.document_ai.bank_parsers.banks import select_parser
from src.document_ai.bank_parsers.base_parser import TransactionType
from src.document_ai.bank_parsers.generic_parser import GenericBankParser as G
from src.document_ai.parser_engine import DocumentAIEngine
from src.document_ai.redactor import PIIRedactor
from tests.conftest import BANK_KEYS, build_pdf, pdf_text

EXPECTED_BANK = {"garanti": "Garanti BBVA", "isbank": "Türkiye İş Bankası", "ziraat": "Ziraat Bankası", "yapikredi": "Yapı Kredi Bankası",
                 "akbank": "Akbank", "vakifbank": "Vakıfbank", "qnb": "QNB Finansbank", "enpara": "Enpara.com"}


# ------------------------------------------------------------ doğrulayıcılar (özellik testleri)
def make_tc(rng: random.Random) -> str:
    d = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    d.append(((sum(d[0::2]) * 7) - sum(d[1::2])) % 10)
    d.append(sum(d) % 10)
    return "".join(map(str, d))


def make_pan(rng: random.Random, length: int = 16) -> str:
    body = [rng.randint(0, 9) for _ in range(length - 1)]
    total = sum((x * 2 - 9 if x * 2 > 9 else x * 2) if i % 2 == 0 else x for i, x in enumerate(reversed(body)))
    return "".join(map(str, body)) + str((10 - total % 10) % 10)


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=0, max_value=10), st.integers(min_value=1, max_value=9))
def test_tc_validator_accepts_valid_and_rejects_single_digit_mutation(seed, pos, delta):
    tc = make_tc(random.Random(seed))
    assert PIIRedactor.is_valid_tc(tc)
    mutated = tc[:pos] + str((int(tc[pos]) + delta) % 10) + tc[pos + 1:]
    if pos in (9, 10):  # sağlama hanelerindeki her değişiklik yakalanmalı
        assert not PIIRedactor.is_valid_tc(mutated)


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=0, max_value=10**9), st.sampled_from([13, 15, 16, 19]), st.integers(min_value=1, max_value=9))
def test_luhn_detects_every_single_digit_error(seed, length, delta):
    rng = random.Random(seed)
    pan = make_pan(rng, length)
    assert PIIRedactor.is_luhn_valid(pan)
    pos = rng.randrange(length)
    assert not PIIRedactor.is_luhn_valid(pan[:pos] + str((int(pan[pos]) + delta) % 10) + pan[pos + 1:])


@pytest.mark.parametrize("bad", ["01234567890", "12345", "abcdefghijk", "", "1234567890123456789012"])
def test_validators_reject_malformed(bad):
    assert not PIIRedactor.is_valid_tc(bad)
    assert not PIIRedactor.is_luhn_valid(bad)


def test_mask_text_covers_all_pii_kinds():
    raw = "TC: 50206295212 Kart: 4539 1488 0343 6467 IBAN: TR12 3456 7890 1234 5678 9012 34 Mail: ahmet@example.com Tel: 0532 123 45 67"
    masked = PIIRedactor.mask_text(raw)
    for secret in ["50206295212", "4539 1488 0343 6467", "3456 7890", "ahmet@example.com", "0532 123 45 67"]:
        assert secret not in masked
    assert "50*******12" in masked and "4539 **** **** 6467" in masked and "[E-POSTA]" in masked and "[TELEFON]" in masked


def test_mask_text_does_not_touch_non_pii_numbers():
    text = "MIGROS 042 REF 12345678901 TUTAR 1.250,50"  # 11 haneli ama TC sağlaması tutmuyor
    assert PIIRedactor.mask_text(text) == text


@pytest.mark.parametrize("raw,expected", [
    ("HAVALE AHMET YILMAZ", "HAVALE [KİŞİ]"),
    ("FAST GÖNDERİM ZEYNEP KAYA 482913", "FAST GÖNDERİM [KİŞİ] 482913"),
    ("HAVALE EFT UCRETI", "HAVALE EFT UCRETI"),          # ücret satırı isim değildir
    ("MIGROS TICARET", "MIGROS TICARET"),
])
def test_transfer_counterparty_masking(raw, expected):
    assert PIIRedactor.mask_text(raw) == expected


def test_known_cardholder_name_is_masked_case_and_diacritic_insensitively():
    assert "DEMİRTAŞ" not in PIIRedactor.mask_text("ODEME AYŞE DEMİRTAŞ ADINA", known_names=["Ayse Demirtas"])


# ----------------------------------------------------------------- satır ayrıştırma
@pytest.mark.parametrize("text,expected", [
    ("1.250,50", 1250.50), ("-450,00", -450.0), ("150,00 (A)", -150.0), ("150,00A", -150.0), ("550,00-", -550.0),
    ("550,00 +", -550.0), ("1250,50", 1250.50), ("12.500,00 B", 12500.0), ("99,90 TL", 99.90), ("1.000.000,00", 1_000_000.0),
])
def test_turkish_amount_formats(text, expected):
    assert G.clean_turkish_amount(text)[0] == pytest.approx(expected)


@pytest.mark.parametrize("desc,expected", [
    ("MEDIAMARKT (03/06)", (3, 6)), ("THY 2/6 TAKSİT", (2, 6)), ("TEKNOSA TAKSİT 3/12", (3, 12)), ("VATAN 3.TAKSİT", (3, None)),
    ("A101 24/7 MARKET", (None, None)),        # 24/7 taksit değildir
    ("BUFE (24/7)", (None, None)),             # no > toplam
    ("KURS (1/1)", (None, None)),              # tek taksit taksit değildir
    ("MIGROS", (None, None)),
])
def test_installment_extraction(desc, expected):
    assert G.extract_installment(desc) == expected


@pytest.mark.parametrize("line,date,amount,tx_type", [
    ("14.08.2024 MIGROS TIC A.S. 1.250,50", "2024-08-14", 1250.50, TransactionType.EXPENSE),
    ("14/08/2024 ZARA İPTAL / İADE 550,00-", "2024-08-14", -550.0, TransactionType.REFUND),
    ("14.08.24 ZARA IADE 550,00", "2024-08-14", -550.0, TransactionType.REFUND),            # işaretsiz iade düzeltilir
    ("2024-08-14 NETFLIX.COM 199,99 TL", "2024-08-14", 199.99, TransactionType.EXPENSE),
    ("14.08.2024 15.08.2024 SHELL 900,00", "2024-08-14", 900.0, TransactionType.EXPENSE),     # işlem + valör tarihi
    ("HEPSIBURADA 14/08/2024 2.499,00", "2024-08-14", 2499.0, TransactionType.EXPENSE),      # açıklama önce
    ("14.08.2024 AMAZON US 49,99 USD 1.650,00", "2024-08-14", 1650.0, TransactionType.EXPENSE),  # TL karşılığı sondadır
    ("14.08.2024 ÖDEME - TEŞEKKÜR EDERİZ -5.000,00", "2024-08-14", -5000.0, TransactionType.PAYMENT),
    ("14.08.2024 AKDİ FAİZ 180,40", "2024-08-14", 180.40, TransactionType.FINANCING_FEE),
    ("14.08.2024 CHIP-PARA KULLANIMI 0,00", "2024-08-14", 0.0, TransactionType.POINT_REDEMPTION),
])
def test_parse_line(line, date, amount, tx_type):
    tx = G.parse_line(line)
    assert tx is not None and tx.date == date and tx.amount == pytest.approx(amount) and tx.transaction_type == tx_type


@pytest.mark.parametrize("line", [
    "Dönem Borcu: 32.699,46 TL Son Ödeme Tarihi: 25.05.2024", "TOPLAM 15.05.2024 32.699,46", "Hesap Kesim Tarihi: 15.05.2024",
    "MIGROS 1.250,50", "31.02.2024 GECERSIZ TARIH 10,00", "",
])
def test_non_transaction_lines_are_rejected(line):
    assert G.parse_line(line) is None


# ---------------------------------------------------------------- uçtan uca (8 yerleşim)
@pytest.mark.parametrize("idx", range(len(BANK_KEYS)), ids=BANK_KEYS)
def test_bank_statement_matches_ground_truth(bank_samples, idx):
    key, pdf_bytes, truth = bank_samples[idx]
    stmt, _ = DocumentAIEngine.process_pdf(pdf_bytes)

    assert stmt.bank_name == EXPECTED_BANK[key] and stmt.fingerprint_score >= 2
    assert len(stmt.transactions) == truth["transaction_count"]
    assert stmt.stated_total_debt == pytest.approx(truth["total_debt"], abs=0.005)
    assert stmt.checksum_valid is True and abs(stmt.checksum_diff) <= 0.01
    assert stmt.min_payment == pytest.approx(truth["min_payment"], abs=0.005)
    assert stmt.card_last4 == truth["card_last_four"]
    assert stmt.period_end == truth["cutoff_date"] and stmt.due_date == truth["due_date"]

    refunds = [t for t in stmt.transactions if t.transaction_type == TransactionType.REFUND]
    assert -sum(t.amount for t in refunds) == pytest.approx(truth["refund_total"])
    assert [(t.installment_no, t.installment_total) for t in stmt.transactions if t.installment_no] == [(3, 6)]
    assert len(stmt.to_dataframe()) == len(stmt.transactions)


@pytest.mark.parametrize("idx", range(len(BANK_KEYS)), ids=BANK_KEYS)
def test_no_pii_survives_redaction_on_any_page(bank_samples, idx):
    _, pdf_bytes, truth = bank_samples[idx]
    original_text = pdf_text(pdf_bytes)
    assert truth["card_holder"] in original_text and truth["tc"] in original_text, "Test anlamlı olsun: PII girdide var olmalı"

    stmt, redacted = DocumentAIEngine.process_pdf(pdf_bytes)
    text = pdf_text(redacted)
    secrets = [truth["card_holder"], truth["tc"], *truth["third_party_names"], *truth["address_fragments"]]
    assert [s for s in secrets if s in text] == []

    # Karartma içerik akışından siler; üstünü örtmez (ham akışlarda da bulunmamalı)
    doc = pymupdf.open(stream=redacted, filetype="pdf")
    raw_streams = b"".join(doc.xref_stream(x) or b"" for x in range(1, doc.xref_length()))
    assert truth["tc"].encode() not in raw_streams
    assert not any(doc.metadata.get(k) for k in ("author", "title", "subject", "keywords"))

    joined = " ".join(t.raw_description + t.clean_description for t in stmt.transactions)
    assert [s for s in secrets if s in joined] == []
    assert stmt.pii_redaction_count > 0
    # Karartma ayrıştırmayı bozmamalı: işlem açıklamaları hâlâ okunur
    assert "MİGROS" in text or "MIGROS" in text


def test_pii_on_second_page_header_and_metadata_is_removed():
    pdf = build_pdf(
        header_lines=[(50, 65, "Kart Sahibi: AYŞE DEMİRTAŞ"), (350, 65, "Dönem Borcu: 869,00 TL")],
        rows=[("01.02.2026", "MIGROS", "450,00"), ("03.02.2026", "SOK MARKET", "320,00")],
        extra_pages=[[(50, 40, "Kart Sahibi: AYŞE DEMİRTAŞ   Kart No: 4539 1488 0343 6467"), (50, 300, "04.02.2026"), (130, 300, "BIM"), (470, 300, "99,00")]],
        metadata={"author": "AYŞE DEMİRTAŞ", "title": "Ekstre AYŞE DEMİRTAŞ"},
    )
    stmt, redacted = DocumentAIEngine.process_pdf(pdf)
    text = pdf_text(redacted)
    assert "DEMİRTAŞ" not in text and "4539 1488" not in text
    assert stmt.checksum_valid is True and len(stmt.transactions) == 3


def test_unlabelled_header_triggers_safety_zone():
    pdf = build_pdf(header_lines=[(70, 190, "MEHMET ÖZKAN"), (70, 204, "BAĞDAT CAD. NO:12 KADIKÖY")])
    _, redacted = DocumentAIEngine.process_pdf(pdf)
    text = pdf_text(redacted)
    assert "ÖZKAN" not in text and "BAĞDAT" not in text and "MIGROS" in text


# ---------------------------------------------------------------------- sağlama
def test_checksum_mismatch_is_reported_not_hidden():
    pdf = build_pdf(header_lines=[(350, 65, "Dönem Borcu: 1.000,00 TL")])  # işlemler toplamı 770,00
    stmt, _ = DocumentAIEngine.process_pdf(pdf)
    assert stmt.checksum_valid is False
    assert stmt.checksum_diff == pytest.approx(230.0) and stmt.total_debt == pytest.approx(1000.0) and stmt.computed_total == pytest.approx(770.0)
    assert any("Sağlama tutmadı" in w for w in stmt.warnings)


def test_missing_header_total_yields_unverified_not_valid():
    stmt, _ = DocumentAIEngine.process_pdf(build_pdf())
    assert stmt.checksum_valid is None and stmt.stated_total_debt is None
    assert stmt.min_payment is None and stmt.card_last4 is None, "Okunamayan alan uydurulmamalı"
    assert stmt.total_debt == pytest.approx(770.0)


def test_payments_are_excluded_from_checksum():
    pdf = build_pdf(header_lines=[(350, 65, "Dönem Borcu: 770,00 TL")],
                    rows=[("01.02.2026", "MIGROS", "450,00"), ("02.02.2026", "ÖDEME - TEŞEKKÜR EDERİZ", "-5.000,00"), ("03.02.2026", "SOK MARKET", "320,00")])
    stmt, _ = DocumentAIEngine.process_pdf(pdf)
    assert stmt.checksum_valid is True


# -------------------------------------------------------------------- parmak izi
@pytest.mark.parametrize("header,expected", [
    ("VAKIFBANK WORLD KREDİ KARTI EKSTRESİ", "Vakıfbank"),               # 'world' Yapı Kredi yapmamalı
    ("TÜRKİYE VAKIFLAR BANKASI T.A.O.", "Vakıfbank"),                    # 'türkiye ... bankası' İş Bankası yapmamalı
    ("TÜRKİYE HALK BANKASI PARAF", "Genel / Tanımlanamayan Banka"),
    ("DENİZBANK BONUS KART EKSTRESİ", "Genel / Tanımlanamayan Banka"),   # tek başına 'bonus' yetmez
    ("T. GARANTİ BANKASI A.Ş. BONUS", "Garanti BBVA"),
    ("YAPI VE KREDİ BANKASI WORLDCARD", "Yapı Kredi Bankası"),
    ("", "Genel / Tanımlanamayan Banka"),
])
def test_fingerprint_collisions(header, expected):
    assert select_parser(header)[0].profile.bank_name == expected


def test_fingerprint_ignores_transaction_descriptions():
    pdf = build_pdf(title="AKBANK AXESS HESAP ÖZETİ", rows=[("12.02.2026", "MAXIMUM AVM OTOPARK İŞ BANKASI ATM", "45,00"), ("13.02.2026", "WORLD OF SPORT YAPI KREDİ PLAZA", "1.250,00")])
    stmt, _ = DocumentAIEngine.process_pdf(pdf)
    assert stmt.bank_name == "Akbank"

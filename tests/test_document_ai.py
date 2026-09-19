"""Document AI: ayrıştırma doğruluğu (truth.json'a karşı), sağlama, parmak izi, PII sızıntı testleri."""

import random
from decimal import Decimal

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
    # Bazı bankalar ekstreyi İngilizce sayı biçimiyle basar (gerçek ekstrede görüldü)
    ("1,234.56", 1234.56), ("12.81", 12.81), ("1,000,000.00", 1_000_000.0), ("+4,750.00", -4750.0), ("+99.90", -99.90), ("10,250.40 TL", 10250.40),
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
    # İngilizce sayı biçimi + '+' alacak öneki
    ("15.08.2026 MIGROS TIC AS 1,234.56", "2026-08-15", 1234.56, TransactionType.EXPENSE),
    ("15.08.2026 ÖDEMENİZ İÇİN TEŞEKKÜR EDERİZ +5,000.00", "2026-08-15", -5000.0, TransactionType.PAYMENT),
    ("15.08.2026 İADE/ ZARA GİYİM +1,499.90", "2026-08-15", -1499.90, TransactionType.REFUND),
    ("15.08.2026 ALIŞVERİŞ FAİZİ (Oran:4.25) 85.40", "2026-08-15", 85.40, TransactionType.FINANCING_FEE),   # oran tutar sanılmamalı
    ("15.08.2026 KKDF 12.81", "2026-08-15", 12.81, TransactionType.FINANCING_FEE),
])
def test_parse_line(line, date, amount, tx_type):
    tx = G.parse_line(line)
    assert tx is not None and tx.date == date and tx.amount == pytest.approx(amount) and tx.transaction_type == tx_type


@pytest.mark.parametrize("line,no,total,amount", [
    ("15.08.2026 MEDIAMARKT 3. Taksit 1,250.00 3x1,250.00", 3, 6, 1250.0),     # kalan taksit sütunu tutar DEĞİLDİR
    ("15.08.2026 MEDIAMARKT 3. Taksit 1,250.00 3 x 1,250.00", 3, 6, 1250.0),
    ("15.08.2026 TEKNOSA 6. Taksit 450.00 Son Taksit", 6, 6, 450.0),
    ("15.08.2026 VATAN 2. Taksit 999.90", 2, None, 999.90),
])
def test_installment_column_with_remaining_count(line, no, total, amount):
    tx = G.parse_line(line)
    assert (tx.installment_no, tx.installment_total, tx.amount) == (no, total, pytest.approx(amount))
    assert "x1" not in tx.raw_description and "Son Taksit" not in tx.raw_description


@pytest.mark.parametrize("line,amount,tx_type,inst", [
    # Çok sütunlu ekstre (TL | USD | puan): TL tutarı sondaki tutar koşusunun İLK elemanıdır
    ("12.03.2025 IYZICO/ORNEKMARKET 1 İSTANBUL TR 69,90 0,00", 69.90, TransactionType.EXPENSE, (None, None)),
    ("07.03.2025 DİJİTAL PLATFORM PUAN KAZANIM 0,00 18,40", 0.0, TransactionType.POINT_REDEMPTION, (None, None)),
    # Açıklamaya gömülü yabancı tutar (belgenin yerel biçiminde DEĞİL) işlem tutarı sayılmaz
    ("04.03.2025 ORNEK* MEMBERSHIP USD 12.99 1.420,10 0,00", 1420.10, TransactionType.EXPENSE, (None, None)),
    ("23.03.2025 23/03 S/ORNEK 01.Tak İSTANBUL (2400.00 TL İşlemin 1/4 Taksidi) 600,00", 600.0, TransactionType.INSTALLMENT, (1, 4)),
    ("30.03.2025 0000 Şube Tak. Nak. Av.Faiz Or:%3.90 1. (3000.00 TL İşlemin 1/3BSMV) 7,20", 7.20, TransactionType.FINANCING_FEE, (1, 3)),
    ("11.03.2025 0000 şube-hesaptan ödeme-teşekkür ederiz 2.480,75+", -2480.75, TransactionType.PAYMENT, (None, None)),
    ("13.03.2025 S/ORNEK İSTANB Satis Iade 3.150,00+", -3150.0, TransactionType.REFUND, (None, None)),
])
def test_multi_column_and_embedded_foreign_amounts(line, amount, tx_type, inst):
    tx = G.parse_line(line, locale="tr")
    assert tx.amount == pytest.approx(amount) and tx.transaction_type == tx_type
    assert (tx.installment_no, tx.installment_total) == inst


def test_document_locale_is_majority_vote():
    assert G.detect_locale(["a 1.250,50\nb 12,00\nc USD 12.99"]) == "tr"
    assert G.detect_locale(["a 1,250.50\nb 12.00\nc 3,00"]) == "en"
    assert G.detect_locale(["tutar yok"]) is None


def test_undated_carry_over_line_uses_first_amount_column():
    assert G.parse_previous_balance("ÖNCEKİ AYDAN DEVİR 2.480,75 0,00", "tr") == Decimal("2480.75")
    assert G.parse_line("ÖNCEKİ AYDAN DEVİR 2.480,75 0,00", "tr") is None


def test_hash_masked_card_number_is_read():
    assert G.extract_header_fields("KART NO : 5400-####-####-1234 / A***** B*****")["card"] == "1234"


def test_trailing_points_column_after_remaining_installments():
    tx = G.parse_line("15.03.2025 ORNEK MAGAZA 1. Taksit 450.00 2x450.00 120", "en")
    assert (tx.amount, tx.installment_no, tx.installment_total) == (450.0, 1, 3)


def test_date_fragments_and_rates_are_not_amounts():
    assert G.AMOUNT_REGEX.search("Hesap Kesim Tarihi : 15.08.2026") is None     # '15.08' tutar değildir
    assert G.AMOUNT_REGEX.search("Akdi Faiz Oranı %4.25 %65.00") is None
    assert G.parse_line("15.08.2026 ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYESİ 5,250.00") is None
    assert G.parse_previous_balance("15.08.2026 ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYESİ 5,250.00") == 5250


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


def test_unlabelled_header_is_always_redacted():
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


def test_previous_balance_and_payment_accounting():
    """Gerçek ekstre muhasebesi: dönem borcu = önceki bakiye + tüm hareketler (ödeme ve iade eksi)."""
    rows = [("01.02.2026", "ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYESİ", "5,250.00"), ("02.02.2026", "ÖDEMENİZ İÇİN TEŞEKKÜR EDERİZ", "+4,750.00"),
            ("03.02.2026", "MIGROS", "450.00"), ("04.02.2026", "İADE/ ZARA", "+100.00"), ("05.02.2026", "KKDF", "12.81")]
    stmt, _ = DocumentAIEngine.process_pdf(build_pdf(header_lines=[(350, 65, "Dönem Borcunuz : 862.81 TL")], rows=rows))
    assert stmt.previous_balance == pytest.approx(5250.0) and len(stmt.transactions) == 4
    assert stmt.checksum_valid is True and stmt.computed_total == pytest.approx(5250 - 4750 + 450 - 100 + 12.81)

    wrong, _ = DocumentAIEngine.process_pdf(build_pdf(header_lines=[(350, 65, "Dönem Borcunuz : 999.99 TL")], rows=rows))
    assert wrong.checksum_valid is False


def test_two_column_header_address_lines_are_redacted_despite_interleaving():
    """Sol sütun satırları, sağ sütundaki etiketsiz adres satırlarının arasına girer; tarama bu yüzden durmamalı."""
    pdf = build_pdf(header_lines=[(50, 90, "Dönem Borcunuz : 770.00 TL"), (330, 97, "Sayın AYŞE DEMİRTAŞ"), (50, 104, "Asgari Ödeme Tutarı : 154.00 TL"),
                                  (330, 111, "LALE CAD. GÜNEŞ SOK. NO:"), (50, 118, "Son Ödeme Tarihi : 25.02.2026"), (330, 125, "14 ÜSKÜDAR / İSTANBUL")],
                    rows=[("01.02.2026", "MIGROS", "450.00"), ("03.02.2026", "SOK MARKET", "320.00")],
                    extra_pages=[[(50, 40, "Sayın AYŞE DEMİRTAŞ"), (50, 54, "LALE CAD. GÜNEŞ SOK. NO:"), (50, 300, "04.02.2026 BIM 0.00")]])
    stmt, redacted = DocumentAIEngine.process_pdf(pdf)
    text = pdf_text(redacted)
    assert stmt.checksum_valid is True
    for secret in ["DEMİRTAŞ", "LALE", "ÜSKÜDAR"]:
        assert secret not in text, f"{secret} sızdı"
    assert "MIGROS" in text


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

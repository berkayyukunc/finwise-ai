"""Türkçe POS ön işlemcisi: katlama, Unicode, gürültü regex regresyonları ve özellik testleri."""

import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor as P


@pytest.mark.parametrize("a,b", [
    ("MIGROS TIC. A.S.", "MİGROS TİC. A.Ş."),
    ("KOCTAS", "KOÇTAŞ"),
    ("GUNAYDIN KEBAP", "GÜNAYDIN KEBAP"),
    ("SOK MARKET", "ŞOK MARKET"),
    ("IGDAS DOGALGAZ", "İGDAŞ DOĞALGAZ"),
    ("migros", "MIGROS"),
])
def test_all_six_diacritic_pairs_fold_to_same_form(a, b):
    assert P.clean_pos_text(a) == P.clean_pos_text(b)


def test_nfd_decomposed_input_matches_nfc():
    nfd = unicodedata.normalize("NFD", "MİGROS İSTİNYE")
    assert nfd != "MİGROS İSTİNYE"
    assert P.clean_pos_text(nfd) == P.clean_pos_text("MİGROS İSTİNYE") == "migros istinye"


def test_latin5_font_artifacts_are_repaired():
    assert P.fold("ÝÞ BANKASI") == "is bankasi"  # 'İŞ' Latin-1 olarak yanlış çözülmüş


@pytest.mark.parametrize("raw,expected", [
    ("ASUS TURKIYE", "asus turkiye"),       # 'A.S.' deseni kelime başını yememeli
    ("SBARRO PIZZA", "sbarro pizza"),       # 'SB' deseni
    ("STIL KUAFOR", "stil kuafor"),         # 'STI' deseni
    ("TICKET RESTAURANT", "ticket restaurant"),
    ("ISTINYE PARK", "istinye park"),       # 'IST' deseni
    ("TRENDYOL", "trendyol"),               # 'TR' deseni
    ("LTDX MAGAZA", "ltdx magaza"),
])
def test_noise_regex_never_eats_brand_prefixes(raw, expected):
    assert P.clean_pos_text(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    # Ödeme kuruluşu önekleri ve taksit ekleri kategori sinyali taşımaz: markayı bastırmamalılar
    ("MokaUnited/BERSHKA G 1. Taksit", "bershka"),
    ("MokaUnited/PULL AND 1. Taksit", "pull and"),
    ("VALLET/KOTON 3. Taksit", "koton"),
    ("CRAFTGATE*DEFACTO", "defacto"),
    ("23/05 S/BEYMEN 01.Tak ISTANBUL", "<num> beymen"),
    ("STRADIVARIUS IZMIR I", "stradivarius"),          # kırpmadan kalan tek harf artığı atılır
    ("MIGROS TIC A.S. IST TR", "migros"),
    # Yabancı tüzel kişilik ekleri ve şehir adları da unvan çöpüdür; çekirdek marka kalır
    ("TOOLIGO LIMITED/LONDON", "tooligo"),
    ("NETFLIX INTERNATIONAL B.V.", "netflix"),
    ("QWERTY GMBH/BERLIN", "qwerty"),
    ("AKBANK T.A.S.", "akbank"),
    ("DEMIR SAN. VE TIC. LTD. STI.", "demir ve"),
    ("IYZICO/ZARA TR", "zara"),
    ("PAYTR*LEZZET DONER", "lezzet doner"),
])
def test_corporate_noise_and_facilitator_prefix_removed(raw, expected):
    assert P.clean_pos_text(raw) == expected


def test_dates_amounts_numbers_are_masked():
    assert P.clean_pos_text("ZARA 14.08.2024 1.250,50 TL 042") == "zara <date> <cur> <num>"
    assert P.clean_pos_text("ZARA 042", mask_numbers=False) == "zara 042"


def test_empty_and_none_like_inputs():
    assert P.clean_pos_text("") == ""
    assert P.clean_pos_text("   ...   ") == ""


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=0x17F), max_size=60))
def test_clean_is_idempotent_and_canonical(text):
    once = P.clean_pos_text(text)
    assert P.clean_pos_text(once) == once                   # idempotent
    assert once == once.lower() and "  " not in once        # kanonik biçim
    assert not set("çğıöşüİ") & set(once)                   # diakritik kalmaz

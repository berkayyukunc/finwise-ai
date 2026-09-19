"""Ortak test fikstürleri: PDF üreticisi, eğitilmiş model, örnek işlem tablosu."""

import io
import json
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import pandas as pd
import pdfplumber
import pymupdf
import pytest

from src.nlp.models import DEFAULT_MODEL_PATH, POSClassifierPipeline

STATEMENTS_DIR = Path("data/statements")
BANK_KEYS = ["garanti", "isbank", "ziraat", "yapikredi", "akbank", "vakifbank", "qnb", "enpara"]
FONT = pymupdf.Font("helv")

Row = Tuple[str, str, str]  # tarih, açıklama, tutar


def build_pdf(
    title: str = "GARANTİ BBVA - KREDİ KARTI HESAP ÖZETİ",
    header_lines: Optional[Sequence[Tuple[float, float, str]]] = None,
    rows: Optional[List[Row]] = None,
    extra_pages: Optional[List[List[Tuple[float, float, str]]]] = None,
    metadata: Optional[dict] = None,
) -> bytes:
    """Testlerin kendi yerleşimini tanımlayabildiği küçük PDF üreticisi."""
    rows = rows if rows is not None else [("01.02.2026", "MIGROS", "450,00"), ("03.02.2026", "SOK MARKET", "320,00")]
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    def write(pg, x, y, text, size=9):
        tw = pymupdf.TextWriter(pg.rect)
        tw.append((x, y), text, font=FONT, fontsize=size)
        tw.write_text(pg)

    write(page, 50, 40, title, 13)
    for x, y, text in header_lines or []:
        write(page, x, y, text, 10)
    y = 320
    for date, desc, amount in rows:
        write(page, 50, y, date, 8)
        write(page, 130, y, desc, 8)
        write(page, 470, y, amount, 8)
        y += 18
    for lines in extra_pages or []:
        pg = doc.new_page(width=595, height=842)
        for x, yy, text in lines:
            write(pg, x, yy, text, 9)
    if metadata:
        doc.set_metadata(metadata)
    data = doc.tobytes()
    doc.close()
    return data


def pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


@pytest.fixture(scope="session")
def classifier() -> POSClassifierPipeline:
    assert Path(DEFAULT_MODEL_PATH).exists(), "Model yok: önce `PYTHONPATH=. python train_nlp_model.py` çalıştırın."
    return POSClassifierPipeline().load(DEFAULT_MODEL_PATH)


@pytest.fixture(scope="session")
def bank_samples():
    """[(anahtar, pdf_baytları, truth_sözlüğü)]"""
    out = []
    for key in BANK_KEYS:
        pdf_path = STATEMENTS_DIR / f"sample_{key}.pdf"
        out.append((key, pdf_path.read_bytes(), json.loads(pdf_path.with_suffix("").with_suffix(".truth.json").read_text(encoding="utf-8"))))
    return out


@pytest.fixture
def sample_transactions_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2026-02-01", "2026-02-02", "2026-02-07", "2026-02-08", "2026-02-10", "2026-02-14", "2026-02-15", "2026-02-20", "2026-02-21"],
        "clean_description": ["MIGROS TICARET", "STARBUCKS KAHVE", "ZARA GIYIM", "NETFLIX ABONELIK", "SHELL AKARYAKIT", "APPLE STORE (03/06)", "ESPRESSO LAB", "SOK MARKET", "ZARA GIYIM IADE"],
        "category": ["Market / Bakkal", "Restoran / Yeme-İçme", "Giyim / Aksesuar", "Dijital Servis / Abonelik", "Akaryakıt / Ulaşım",
                     "Elektronik / Teknoloji", "Restoran / Yeme-İçme", "Market / Bakkal", "Giyim / Aksesuar"],
        "amount": [450.0, 120.0, 1850.0, 199.99, 850.0, 12500.0, 110.0, 320.0, -350.0],
        "installment_no": [None, None, None, None, None, 3, None, None, None],
        "installment_total": [None, None, None, None, None, 6, None, None, None],
        "transaction_type": ["EXPENSE"] * 5 + ["INSTALLMENT"] + ["EXPENSE"] * 2 + ["REFUND"],
    })

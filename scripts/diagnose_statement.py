"""
FinWise-AI: Gizlilik Korumalı Ekstre Teşhis Aracı
=================================================
Bir ekstre ayrıştırılamadığında NEDENİNİ, içeriği ifşa etmeden gösterir.

Her satırda harfler 'A', rakamlar '9' ile değiştirilir; noktalama ve boşluklar korunur.
Böylece çıktı yalnızca BİÇİMİ taşır ("99.99.9999 AAAAA AAA 9.999,99") ve güvenle paylaşılabilir:
isim, kart numarası, işyeri ve tutar bilgisi içermez.

Kullanım:
    PYTHONPATH=. python scripts/diagnose_statement.py /yol/ekstre.pdf
    PYTHONPATH=. python scripts/diagnose_statement.py /yol/ekstre.pdf --password-env PDF_SIFRE
"""

import argparse
import io
import os
import re
import sys
from collections import Counter

import pdfplumber
import pymupdf

from src.document_ai.bank_parsers.generic_parser import GenericBankParser as G
from src.document_ai.sanitizer import PDFPasswordRequiredError, PDFSanitizer

# Yapıyı anlamak için gereken, PII içermeyen sözcükler maskelenmez
SAFE_WORDS = {
    "tarih", "tarihi", "islem", "aciklama", "tutar", "tutari", "tl", "try", "usd", "eur", "taksit", "donem", "borcu", "borc", "asgari",
    "odeme", "son", "hesap", "kesim", "ekstre", "toplam", "bakiye", "alacak", "borclu", "puan", "bonus", "limit", "kart", "no", "sayfa",
    "onceki", "devreden", "faiz", "kkdf", "bsmv", "iade", "iptal", "valor", "referans", "ref", "sektor", "kazanilan", "harcama", "harcamalar",
}


def mask(text: str) -> str:
    def repl(m: re.Match) -> str:
        word = m.group(0)
        return word if _fold(word) in SAFE_WORDS else re.sub(r"[^\W\d_]", "A", word)
    return re.sub(r"\d", "9", re.sub(r"[^\W\d_]+", repl, text))


def _fold(word: str) -> str:
    from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor
    return TurkishFinancialNLPPreprocessor.fold(word)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ekstre biçimini içerik ifşa etmeden teşhis eder.")
    ap.add_argument("pdf")
    ap.add_argument("--password-env", help="PDF şifresini taşıyan ortam değişkeninin ADI (şifre komut satırına yazılmaz)")
    ap.add_argument("--max-lines", type=int, default=70)
    args = ap.parse_args()

    with open(args.pdf, "rb") as f:
        raw = f.read()
    password = os.environ.get(args.password_env) if args.password_env else None
    try:
        clean, report = PDFSanitizer.sanitize_with_report(raw, password)
    except PDFPasswordRequiredError:
        print("SONUÇ: PDF şifreli. --password-env ile şifreyi taşıyan ortam değişkenini verin.")
        return 2

    print(f"# Dosya: {len(raw) / 1024:.0f} KB | sayfa: {report.page_count} | şifreli: {report.was_encrypted} | sökülen aktif içerik: {len(report.removed)}")

    with pdfplumber.open(io.BytesIO(clean)) as pdf:
        plumber_pages = [(p.extract_text() or "") for p in pdf.pages]
        table_counts = [len(p.extract_tables()) for p in pdf.pages]
    doc = pymupdf.open(stream=clean, filetype="pdf")
    mupdf_pages = [p.get_text() for p in doc]
    image_counts = [len(p.get_images()) for p in doc]

    print(f"# Metin (pdfplumber): {[len(t) for t in plumber_pages]} karakter/sayfa | (pymupdf): {[len(t) for t in mupdf_pages]}")
    print(f"# Tablo sayısı/sayfa: {table_counts} | görüntü sayısı/sayfa: {image_counts}")
    odd = Counter(ch for t in plumber_pages for ch in t if ord(ch) > 0x24F or ch in "�·")
    print(f"# Olağandışı karakterler (font/kodlama sorunu işareti): {dict(odd.most_common(8))}")

    stats = Counter()
    for pi, text in enumerate(plumber_pages):
        print(f"\n===== SAYFA {pi + 1} (maskelenmiş; D=tarih bulundu, T=tutar bulundu, ✓=işlem olarak ayrıştırıldı) =====")
        for li, line in enumerate(text.split("\n")):
            has_date = any(G.parse_date(m) for m in G.DATE_REGEX.finditer(line))
            has_amount = bool(G.AMOUNT_REGEX.search(line))
            parsed = G.parse_line(line) is not None
            stats.update({"satır": 1, "tarihli": has_date, "tutarlı": has_amount, "tarih+tutar": has_date and has_amount, "ayrıştırılan": parsed})
            if li < args.max_lines:
                flags = ("D" if has_date else "-") + ("T" if has_amount else "-") + ("✓" if parsed else " ")
                print(f"{flags} | {mask(line)}")

    print(f"\n# ÖZET: {dict(stats)}")
    numeric = Counter(mask(tok) for t in plumber_pages for tok in re.findall(r"\S*\d\S*", t))
    print(f"# En sık sayısal biçimler: {numeric.most_common(14)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

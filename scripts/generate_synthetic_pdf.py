"""
FinWise-AI: Sentetik Türk Bankası PDF Ekstresi Oluşturucu
=========================================================
8 banka için BİRBİRİNDEN FARKLI yerleşimlerde test ekstresi üretir. Amaç, ayrıştırıcının
ve PII karartıcının tek bir şablona aşırı uyum sağlamasını (circular validation) önlemektir.

Yerleşim farkları: tarih biçimi (GG.AA.YYYY, GG/AA/YYYY, GG.AA.YY, YYYY-AA-GG), iade gösterimi
('-550,00', '550,00-', '550,00 (A)', '550,00 +'), taksit gösterimi ('(03/06)', '3/6 TAKSİT',
'TAKSİT 3/6'), çift tarih (işlem + valör), açıklama-önce sütun sırası, 'TL' soneki, pencereli
zarf tipi adres bloğu (sayfanın %22-30'u) ve her sayfada tekrarlanan isim/kart başlığı.

Dürüstlük Notu: Bu yerleşimler gerçek banka ekstrelerinin birebir kopyası DEĞİLDİR; gerçek
ekstrelerde görülen biçim çeşitliliğini temsil eden sentetik şablonlardır.

Her PDF'in yanına, testlerin karşılaştırma yaptığı `<ad>.truth.json` yazılır.
Kullanım:  PYTHONPATH=. python scripts/generate_synthetic_pdf.py
"""

import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

import pymupdf

from scripts.generate_synthetic_statement import SyntheticStatementGenerator

logger = logging.getLogger("synthetic_pdf")
FONT = pymupdf.Font("helv")  # PyMuPDF'in gömülü Unicode fontu: İ, Ş, Ğ doğru basılır


@dataclass(frozen=True)
class Layout:
    key: str
    title: str
    date_fmt: str = "%d.%m.%Y"
    refund_style: str = "prefix_minus"     # prefix_minus | suffix_minus | alacak | plus_suffix | plus_prefix
    installment_style: str = "paren"       # paren | slash_taksit | taksit_prefix | nth_taksit_column
    header_style: str = "top_labeled"      # top_labeled | envelope_block | two_column_unlabeled
    amount_locale: str = "tr"              # tr: 1.250,50 | en: 1,250.50
    previous_balance: bool = False         # önceki dönem devri + ödeme satırı (gerçek ekstre muhasebesi)
    previous_balance_style: str = "dated_top"   # dated_top: tarihli ilk satır | undated_bottom: tarihsiz 'ÖNCEKİ AYDAN DEVİR' son satır
    extra_amount_columns: bool = False     # 'TL Tutar | USD Tutar | Puan': TL tutarından SONRA başka tutar sütunları
    card_mask: str = "*"
    repeat_header: bool = False
    amount_suffix: str = ""
    double_date: bool = False
    desc_first: bool = False
    include_transfer: bool = False


LAYOUTS = [
    Layout("garanti", "GARANTİ BBVA BONUS - KREDİ KARTI HESAP ÖZETİ"),
    Layout("isbank", "TÜRKİYE İŞ BANKASI MAXIMUM KART EKSTRESİ", date_fmt="%d/%m/%Y", refund_style="suffix_minus", installment_style="slash_taksit"),
    # Gerçek ekstre ekran görüntülerinin YAPISINDAN türetilmiştir: çok sütunlu tutarlar, açıklamaya gömülü yabancı tutar,
    # 'İşlemin 1/4 Taksidi', sonek '+' alacak, tarihsiz 'ÖNCEKİ AYDAN DEVİR', '#' maskeli kart.
    Layout("ziraat", "T.C. ZİRAAT BANKASI BANKKART HESAP ÖZETİ", refund_style="plus_suffix_tight", installment_style="islemin",
           previous_balance=True, previous_balance_style="undated_bottom", extra_amount_columns=True, card_mask="#"),
    Layout("yapikredi", "YAPI VE KREDİ BANKASI A.Ş. WORLDCARD EKSTRESİ", date_fmt="%d.%m.%y", refund_style="plus_suffix", installment_style="taksit_prefix"),
    Layout("akbank", "AKBANK T.A.Ş. AXESS HESAP ÖZETİ", header_style="envelope_block", repeat_header=True, include_transfer=True),
    # Gerçek bir ekstrenin YAPISINDAN (içeriğinden değil) türetilmiştir: EN tutar biçimi, '+' alacak öneki, önceki dönem
    # devri + ödeme, 'N. Taksit' + kalan taksit sütunu, iki sütunlu başlıkta etiketsiz adres bloğu.
    Layout("vakifbank", "VAKIFBANK WORLD KREDİ KARTI HESAP ÖZETİ (TL)", amount_locale="en", refund_style="plus_prefix",
           installment_style="nth_taksit_column", header_style="two_column_unlabeled", previous_balance=True),
    Layout("qnb", "QNB FİNANSBANK CARDFINANS HESAP ÖZETİ", desc_first=True, date_fmt="%d/%m/%Y"),
    Layout("enpara", "ENPARA.COM KREDİ KARTI EKSTRESİ", date_fmt="%Y-%m-%d", refund_style="alacak", installment_style="slash_taksit"),
]


def tr_amount(value: float) -> str:
    return f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_amount(value: float, layout: "Layout") -> str:
    return f"{abs(value):,.2f}" if layout.amount_locale == "en" else tr_amount(value)


def _write(page: "pymupdf.Page", pos, text: str, size: float = 9) -> None:
    tw = pymupdf.TextWriter(page.rect)
    tw.append(pos, text, font=FONT, fontsize=size)
    tw.write_text(page)


def _format_amount(amount: float, is_refund: bool, layout: Layout) -> str:
    s = fmt_amount(amount, layout)
    if is_refund:
        s = {"prefix_minus": f"-{s}", "suffix_minus": f"{s}-", "alacak": f"{s} (A)", "plus_suffix": f"{s} +", "plus_prefix": f"+{s}", "plus_suffix_tight": f"{s}+"}[layout.refund_style]
    return s + layout.amount_suffix


def _format_description(tx: Dict[str, Any], layout: Layout) -> str:
    desc = tx["description"]
    if tx.get("is_installment"):
        no, total = tx["current_installment"], tx["total_installments"]
        desc = desc.replace(f"({no:02d}/{total:02d})", "").strip()
        desc += {"paren": f" ({no:02d}/{total:02d})", "slash_taksit": f" {no}/{total} TAKSİT", "taksit_prefix": f" TAKSİT {no}/{total}",
                 "nth_taksit_column": f" {no}. Taksit", "islemin": f" {no:02d}.Tak İSTANBUL"}[layout.installment_style]
    return desc[:52]


def _draw_header(page, layout: Layout, stmt: Dict[str, Any], first_page: bool) -> float:
    """Başlığı çizer, işlem tablosunun başlayacağı y koordinatını döner."""
    summary = stmt["accounting_summary"]
    _write(page, (50, 40), layout.title, 13)
    if not first_page:
        _write(page, (50, 58), f"Kart Sahibi: {stmt['card_holder']}   Kart No: {stmt['card_number']}", 8)
        return 85

    if layout.header_style == "envelope_block":
        # Özet üstte; 'Sayın <ad>' ve ETİKETSİZ adres satırları pencereli zarf konumunda (sayfanın ~%22-30'u)
        _write(page, (50, 70), f"Kart No: {stmt['card_number']}", 10)
        _write(page, (50, 85), f"Hesap Kesim Tarihi: {stmt['cutoff_date']}", 10)
        _write(page, (350, 70), f"Dönem Borcu: {tr_amount(summary['total_statement_debt_try'])} TL", 11)
        _write(page, (350, 85), f"Asgari Ödeme Tutarı: {tr_amount(summary['minimum_payment_try'])} TL", 10)
        _write(page, (350, 100), f"Son Ödeme Tarihi: {stmt['due_date']}", 10)
        _write(page, (70, 190), f"Sayın {stmt['card_holder']}", 10)
        _write(page, (70, 204), "ÇİÇEK SOKAK NO:5 DAİRE:12", 10)
        _write(page, (70, 218), "CAFERAĞA MAH. KADIKÖY", 10)
        _write(page, (70, 232), "34710 İSTANBUL", 10)
        _write(page, (70, 250), f"T.C. Kimlik No: {stmt['synthetic_full_tc']}", 9)
        return 290

    if layout.header_style == "two_column_unlabeled":
        # Sol sütun: etiketli özet. Sağ sütun: 'Sayın <ad>' + ETİKETSİZ adres; satırlar dikeyde iç içe geçer.
        _write(page, (50, 90), f"Dönem Borcunuz : {fmt_amount(summary['total_statement_debt_try'], layout)} TL", 10)
        _write(page, (50, 104), f"Asgari Ödeme Tutarı : {fmt_amount(summary['minimum_payment_try'], layout)} TL", 10)
        _write(page, (50, 118), f"Son Ödeme Tarihi : {stmt['due_date']}", 10)
        _write(page, (50, 132), f"T.C. Kimlik No : {stmt['synthetic_full_tc']}", 10)
        _write(page, (50, 146), f"Kart No : {stmt['card_number'].replace(' ', '')}", 10)
        _write(page, (50, 160), f"Hesap Kesim Tarihi : {stmt['cutoff_date']}", 10)
        _write(page, (330, 97), f"Sayın {stmt['card_holder']}", 10)
        _write(page, (330, 111), "LALE CAD. GÜNEŞ SOK. B BLOK NO:", 10)
        _write(page, (330, 125), "14 İÇ KAPI NO: 7 ÜSKÜDAR / İSTANBUL", 10)
        _write(page, (50, 180), "ornek.kullanici@example.com", 9)
        _write(page, (50, 200), "Asgari ödeme tutarının altında ödeme yapılması halinde akdi faiz %4.25 ve gecikme faizi %4.55 uygulanır.", 7)
        return 250

    _write(page, (50, 65), f"Kart Sahibi: {stmt['card_holder']}", 10)
    _write(page, (50, 80), f"T.C. Kimlik No: {stmt['synthetic_full_tc']}", 10)
    _write(page, (50, 95), f"Kart No: {stmt['card_number'].replace('*', layout.card_mask).replace(' ', '-' if layout.card_mask == '#' else ' ')}", 10)
    _write(page, (50, 110), f"Hesap Kesim Tarihi: {stmt['cutoff_date']}", 10)
    _write(page, (350, 65), f"Dönem Borcu: {tr_amount(summary['total_statement_debt_try'])} TL", 11)
    _write(page, (350, 80), f"Asgari Ödeme: {tr_amount(summary['minimum_payment_try'])} TL", 10)
    _write(page, (350, 95), f"Son Ödeme Tarihi: {stmt['due_date']}", 10)
    return 150


def create_synthetic_pdf(layout: Layout, output_path: str, seed: int) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    random.seed(seed)  # SyntheticStatementGenerator modül düzeyi random kullanır
    stmt = SyntheticStatementGenerator(bank=layout.key.upper()).generate(include_edge_cases=True)

    if layout.include_transfer:  # üçüncü kişi adı + kart sahibinin kendi adı işlem satırında
        extra = [
            {"date": stmt["transactions"][3]["date"], "description": "KARTTAN HAVALE MEHMET DENİZ ÖRNEK", "amount": 750.0, "type": "PURCHASE"},
            {"date": stmt["transactions"][5]["date"], "description": f"FAST GÖNDERİM {stmt['card_holder']}", "amount": 250.0, "type": "PURCHASE"},
        ]
        stmt["transactions"].extend(extra)
        stmt["transactions"].sort(key=lambda t: datetime.strptime(t["date"], "%d.%m.%Y"))
        summary = stmt["accounting_summary"]
        summary["total_statement_debt_try"] = round(summary["total_statement_debt_try"] + 1000.0, 2)
        summary["gross_purchases_try"] = round(summary["gross_purchases_try"] + 1000.0, 2)

    previous_balance = None
    if layout.previous_balance:  # 5.250 TL devir, 4.750 TL ödeme -> 500 TL yeni döneme taşınır
        previous_balance, payment = 5250.00, 4750.00
        first_date = stmt["transactions"][0]["date"]
        stmt["transactions"].insert(2, {"date": stmt["transactions"][2]["date"], "description": "ÖDEMENİZ İÇİN TEŞEKKÜR EDERİZ", "amount": -payment, "type": "PAYMENT"})
        summary = stmt["accounting_summary"]
        summary["total_statement_debt_try"] = round(summary["total_statement_debt_try"] + previous_balance - payment, 2)
        summary["minimum_payment_try"] = round(summary["total_statement_debt_try"] * 0.20, 2)

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = _draw_header(page, layout, stmt, first_page=True)
    page.draw_line((50, y - 18), (545, y - 18), color=(0.2, 0.2, 0.2), width=1.2)
    _write(page, (50, y - 6), "AÇIKLAMA / TARİH / TUTAR" if layout.desc_first else "TARİH / AÇIKLAMA / TUTAR (TL)", 8)
    y += 12
    if layout.extra_amount_columns:  # açıklamaya gömülü yabancı para tutarı (EN biçimi) işlem tutarı sanılmamalı
        stmt["transactions"][0]["description"] = stmt["transactions"][0]["description"][:30] + " USD 12.99"
    if previous_balance is not None and layout.previous_balance_style == "dated_top":
        _write(page, (50, y), datetime.strptime(first_date, "%d.%m.%Y").strftime(layout.date_fmt), 8)
        _write(page, (130, y), "ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYESİ", 8)
        _write(page, (470, y), fmt_amount(previous_balance, layout), 8)
        y += 18

    for tx in stmt["transactions"]:
        if y > 780:
            page = doc.new_page(width=595, height=842)
            y = _draw_header(page, layout, stmt, first_page=False) if layout.repeat_header else 50
        d = datetime.strptime(tx["date"], "%d.%m.%Y")
        date_str = d.strftime(layout.date_fmt)
        desc = _format_description(tx, layout)
        amount = _format_amount(float(tx["amount"]), tx.get("type") in ("REFUND", "PAYMENT"), layout)

        if layout.desc_first:
            _write(page, (50, y), desc, 8)
            _write(page, (340, y), date_str, 8)
        elif layout.double_date:
            _write(page, (50, y), date_str, 8)
            _write(page, (105, y), date_str, 8)  # valör tarihi
            _write(page, (165, y), desc, 8)
        else:
            _write(page, (50, y), date_str, 8)
            _write(page, (130, y), desc, 8)
        _write(page, (470, y), amount, 8)
        if layout.installment_style == "islemin" and tx.get("is_installment"):
            full = float(tx["amount"]) * tx["total_installments"]
            _write(page, (300, y), f"({full:.2f} TL İşlemin {tx['current_installment']}/{tx['total_installments']} Taksidi)", 7)
        elif layout.extra_amount_columns and tx.get("type") not in ("REFUND", "PAYMENT"):
            _write(page, (540, y), "0,00", 8)  # puan sütunu
        if layout.installment_style == "nth_taksit_column" and tx.get("is_installment"):
            left = tx["total_installments"] - tx["current_installment"]
            _write(page, (522, y), f"{left}x{fmt_amount(float(tx['amount']), layout)}" if left else "Son Taksit", 8)
        y += 18

    if previous_balance is not None and layout.previous_balance_style == "undated_bottom":
        _write(page, (130, y), "ÖNCEKİ AYDAN DEVİR", 9)
        _write(page, (470, y), fmt_amount(previous_balance, layout), 8)
        _write(page, (510, y), "0,00", 8)

    doc.set_metadata({"author": stmt["card_holder"], "title": f"Ekstre - {stmt['card_holder']}"})
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()

    truth = {
        "layout": layout.key,
        "card_holder": stmt["card_holder"],
        "tc": stmt["synthetic_full_tc"],
        "card_last_four": stmt["card_last_four"],
        "cutoff_date": datetime.strptime(stmt["cutoff_date"], "%d.%m.%Y").strftime("%Y-%m-%d"),
        "due_date": datetime.strptime(stmt["due_date"], "%d.%m.%Y").strftime("%Y-%m-%d"),
        "total_debt": stmt["accounting_summary"]["total_statement_debt_try"],
        "min_payment": stmt["accounting_summary"]["minimum_payment_try"],
        "transaction_count": len(stmt["transactions"]),
        "refund_total": -sum(t["amount"] for t in stmt["transactions"] if t.get("type") == "REFUND"),
        "third_party_names": ["MEHMET DENİZ ÖRNEK"] if layout.include_transfer else [],
        "address_fragments": {"envelope_block": ["ÇİÇEK SOKAK", "CAFERAĞA", "34710"], "two_column_unlabeled": ["LALE CAD", "ÜSKÜDAR", "ornek.kullanici"]}.get(layout.header_style, []),
        "previous_balance": previous_balance,
    }
    with open(output_path.replace(".pdf", ".truth.json"), "w", encoding="utf-8") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    logger.info("Üretildi: %s (%d işlem, yerleşim=%s)", output_path, truth["transaction_count"], layout.key)
    return truth


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for i, lay in enumerate(LAYOUTS):
        create_synthetic_pdf(lay, f"data/statements/sample_{lay.key}.pdf", seed=1000 + i)

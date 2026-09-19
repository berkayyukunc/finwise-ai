#!/usr/bin/env python3
"""
FinWise-AI: %100 Gerçekçi ve Sıfır PII Sentetik Ekstre Üreticisi
(Synthetic Statement Generator)

Bu betik; açık kaynak geliştiricilerin, araştırmacıların ve CI/CD hatlarının
gerçek bir ekstre yüklemeden sistemi uçtan uca test edebilmesi için tasarlanmıştır.

Özellikler:
1. %100 Sahte ancak algoritma uyumlu PII (Luhn algoritmalı T.C. Kimlik No, maskeli kart no).
2. Gerçekçi Türk POS açıklamaları ve kategori eşleşmeleri.
3. Kritik bankacılık uç senaryoları (İade/İptal, sonradan taksitlendirme, puan kullanımı, faiz, KKDF, BSMV).
4. Kuruşu kuruşuna bakiye sağlama garantisi (Zero Checksum Drift).
"""

import argparse
import json
import random
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List


def generate_valid_turkish_id() -> str:
    """Geçerli T.C. Kimlik Numarası algoritmasına uygun sentetik numara üretir."""
    digits = [random.randint(1, 9)] + [random.randint(0, 9) for _ in range(8)]
    # 10. hane: ((1, 3, 5, 7, 9. haneler toplamı * 7) - (2, 4, 6, 8. haneler toplamı)) % 10
    d10 = ((sum(digits[0::2]) * 7) - sum(digits[1::2])) % 10
    digits.append(d10)
    # 11. hane: İlk 10 hanenin toplamı % 10
    d11 = sum(digits) % 10
    digits.append(d11)
    return "".join(map(str, digits))


class SyntheticStatementGenerator:
    MERCHANT_CATALOG = [
        ("MİGROS TİC A.Ş. İSTANBUL TR", "Market", 120.0, 1850.0),
        ("BİM BİRLEŞİK MAĞAZALARI", "Market", 50.0, 650.0),
        ("A101 YENİ MAĞAZACILIK A.Ş.", "Market", 40.0, 480.0),
        ("CARREFOURSA KADIKÖY İST", "Market", 150.0, 1400.0),
        ("SHELL MASLAK AKARYAKIT IST", "Ulaşım / Akaryakıt", 500.0, 2600.0),
        ("BP PETROL MECİDİYEKÖY", "Ulaşım / Akaryakıt", 400.0, 2200.0),
        ("STARBUCKS KAHVE BEBEK", "Yeme-İçme", 85.0, 340.0),
        ("ESPRESSOLAB LEVENT", "Yeme-İçme", 90.0, 280.0),
        ("YEMEKSEPETİ ELEKTRONİK İST", "Yeme-İçme", 180.0, 750.0),
        ("GETİR PERAKENDE LOJİSTİK", "Market", 110.0, 520.0),
        ("TRENDYOL PAZARYERİ İSTANBUL", "Giyim & Alışveriş", 250.0, 4200.0),
        ("HEPSİBURADA ELEKTRONİK", "Teknoloji", 450.0, 6500.0),
        ("NETFLIX.COM AMSTERDAM", "Abonelik", 199.99, 199.99),
        ("SPOTIFY ABONELİK STOCKHOLM", "Abonelik", 64.99, 64.99),
        ("TURKCELL İLETİŞİM HİZMETLERİ", "Fatura", 340.0, 620.0),
        ("İSKİ SU FATURASI VEZNE", "Fatura", 180.0, 450.0),
        ("İGDAŞ DOĞALGAZ DAĞITIM", "Fatura", 600.0, 1900.0),
        ("ZARA İSTİNYEPARK GİYİM", "Giyim & Alışveriş", 650.0, 4800.0),
        ("BOYNER BÜYÜK MAĞAZACILIK", "Giyim & Alışveriş", 800.0, 3900.0),
        ("ECZANE DERMAN KADIKÖY", "Sağlık", 120.0, 950.0),
    ]

    def __init__(self, bank: str = "GARANTI", year: int = 2024, month: int = 5):
        self.bank = bank.upper()
        self.year = year
        self.month = month
        self.holder_name = f"ÖRNEK KULLANICI {random.randint(100, 999)}"
        self.tc_no = generate_valid_turkish_id()
        self.card_last_four = str(random.randint(1000, 9999))
        self.card_number = f"5400 **** **** {self.card_last_four}"
        self.cutoff_date = datetime(year, month, 15)
        self.period_start = self.cutoff_date - timedelta(days=30)
        self.due_date = self.cutoff_date + timedelta(days=10)
        self.transactions: List[Dict[str, Any]] = []

    def generate(self, include_edge_cases: bool = True) -> Dict[str, Any]:
        total_purchases = Decimal("0.00")
        total_refunds = Decimal("0.00")
        total_financing_tax = Decimal("0.00")

        # 1. Normal İşlemler (20 - 30 adet)
        tx_count = random.randint(20, 30)
        for _ in range(tx_count):
            merchant, category, min_p, max_p = random.choice(self.MERCHANT_CATALOG)
            raw_amt = Decimal(str(round(random.uniform(min_p, max_p), 2)))
            days_ago = random.randint(1, 29)
            tx_date = self.cutoff_date - timedelta(days=days_ago)

            self.transactions.append({
                "date": tx_date.strftime("%d.%m.%Y"),
                "description": merchant,
                "amount": float(raw_amt),
                "type": "PURCHASE",
                "category_hint": category,
                "is_installment": False
            })
            total_purchases += raw_amt

        # 2. Bankacılık Uç Senaryolarını Enjekte Et
        if include_edge_cases:
            # Uç 1: İade / İptal (Negatif Harcama, Kesinlikle Borç Ödemesi Değil)
            refund_amt = Decimal("550.00")
            refund_date = (self.cutoff_date - timedelta(days=4)).strftime("%d.%m.%Y")
            self.transactions.append({
                "date": refund_date,
                "description": "ZARA İSTİNYEPARK İPTAL / İADE",
                "amount": float(-refund_amt),
                "type": "REFUND",
                "category_hint": "Giyim & Alışveriş",
                "is_installment": False
            })
            total_refunds += refund_amt

            # Uç 2: Taksitli Harcama (Örn: 3. taksit)
            inst_amt = Decimal("1250.00")
            inst_date = (self.cutoff_date - timedelta(days=14)).strftime("%d.%m.%Y")
            self.transactions.append({
                "date": inst_date,
                "description": "MEDIAMARKT BİLGİSAYAR (03/06)",
                "amount": float(inst_amt),
                "type": "INSTALLMENT",
                "category_hint": "Teknoloji",
                "is_installment": True,
                "current_installment": 3,
                "total_installments": 6
            })
            total_purchases += inst_amt

            # Uç 3: 0 TL Puan Kullanımı
            points_date = (self.cutoff_date - timedelta(days=8)).strftime("%d.%m.%Y")
            self.transactions.append({
                "date": points_date,
                "description": "MİGROS TİC A.Ş. CHIP-PARA KULLANIMI",
                "amount": 0.00,
                "type": "POINT_REDEMPTION",
                "category_hint": "Market",
                "is_installment": False
            })

            # Uç 4: Finansman Maliyeti (Faiz + KKDF + BSMV)
            interest = Decimal("180.40")
            kkdf = (interest * Decimal("0.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            bsmv = (interest * Decimal("0.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            fee_date = self.cutoff_date.strftime("%d.%m.%Y")
            self.transactions.extend([
                {
                    "date": fee_date,
                    "description": "AKDİ / GECİKME FAİZİ",
                    "amount": float(interest),
                    "type": "FEE",
                    "category_hint": "Finansman Maliyeti",
                    "is_installment": False
                },
                {
                    "date": fee_date,
                    "description": "%15 KAYNAK KULLANIMI DESTEKLEME FONU (KKDF)",
                    "amount": float(kkdf),
                    "type": "TAX",
                    "category_hint": "Vergi & Fon",
                    "is_installment": False
                },
                {
                    "date": fee_date,
                    "description": "%15 BANKA VE SİGORTA MUAMELELERİ VERGİSİ (BSMV)",
                    "amount": float(bsmv),
                    "type": "TAX",
                    "category_hint": "Vergi & Fon",
                    "is_installment": False
                }
            ])
            total_financing_tax += (interest + kkdf + bsmv)

        # 3. Kuruşu Kuruşuna Dönem Borcu Sağlaması
        total_statement_debt = total_purchases - total_refunds + total_financing_tax
        minimum_payment = (total_statement_debt * Decimal("0.20")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Tarihe göre sırala
        sorted_txs = sorted(self.transactions, key=lambda x: datetime.strptime(x["date"], "%d.%m.%Y"))

        return {
            "metadata": {
                "generator": "FinWise Synthetic Statement Engine v1.0",
                "is_synthetic": True,
                "privacy_guarantee": "100% Zero PII / Anonymized"
            },
            "bank": self.bank,
            "card_holder": self.holder_name,
            "tc_identity_mask": f"*******{self.tc_no[-4:]}",
            "synthetic_full_tc": self.tc_no,
            "card_number": self.card_number,
            "card_last_four": self.card_last_four,
            "statement_period": f"{self.period_start.strftime('%d.%m.%Y')} - {self.cutoff_date.strftime('%d.%m.%Y')}",
            "cutoff_date": self.cutoff_date.strftime("%d.%m.%Y"),
            "due_date": self.due_date.strftime("%d.%m.%Y"),
            "accounting_summary": {
                "gross_purchases_try": float(total_purchases),
                "refunds_reversals_try": float(total_refunds),
                "financing_and_taxes_try": float(total_financing_tax),
                "total_statement_debt_try": float(total_statement_debt),
                "minimum_payment_try": float(minimum_payment)
            },
            "transaction_count": len(sorted_txs),
            "transactions": sorted_txs
        }


def main():
    parser = argparse.ArgumentParser(description="FinWise Sentetik Ekstre Üreticisi")
    parser.add_argument("--bank", type=str, default="GARANTI", help="Banka adı (GARANTI, ISBANK, YAPIKREDI, AKBANK)")
    parser.add_argument("--year", type=int, default=2024, help="Ekstre yılı")
    parser.add_argument("--month", type=int, default=5, help="Ekstre ayı")
    parser.add_argument("--output", type=str, default="synthetic_statement.json", help="Çıktı JSON dosya yolu")
    parser.add_argument("--no-edge-cases", action="store_true", help="Uç senaryoları dahil etme")

    args = parser.parse_args()
    gen = SyntheticStatementGenerator(bank=args.bank, year=args.year, month=args.month)
    result = gen.generate(include_edge_cases=not args.no_edge_cases)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ Sentetik ekstre başarıyla üretildi: {args.output}")
    print(f"📊 Banka: {result['bank']} | Toplam Borç: {result['accounting_summary']['total_statement_debt_try']:.2f} TL | İşlem Sayısı: {result['transaction_count']}")
    print(f"🔒 PII Güvencesi: Luhn T.C. ({result['tc_identity_mask']}), Maskeli Kart: {result['card_number']}")


if __name__ == "__main__":
    main()

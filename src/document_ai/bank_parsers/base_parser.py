"""
FinWise-AI: Temel Banka Ayrıştırıcı Arayüzü ve Veri Modeli
==========================================================
Eğitici Not (Mülakat İçin):
Strateji Deseni burada VERİ güdümlüdür: her banka bir `BankProfile` (parmak izi
ağırlıkları, tarih biçimi, başlık etiketleri) ile tanımlanır; ortak ayrıştırma motoru
profile göre davranır. Yeni banka eklemek = yeni bir profil nesnesi (Open/Closed).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import pandas as pd


class TransactionType(str, Enum):
    EXPENSE = "EXPENSE"                     # Standart tek çekim harcama
    INSTALLMENT = "INSTALLMENT"             # Taksitli işlem
    REFUND = "REFUND"                       # İade / İptal (negatif harcama)
    POINT_REDEMPTION = "POINT_REDEMPTION"   # Bonus / Worldpuan / Chip-para kullanımı (0 TL)
    FINANCING_FEE = "FINANCING_FEE"         # Faiz, KKDF, BSMV, kart aidatı
    PAYMENT = "PAYMENT"                     # Kart borcu ödemesi (harcama değildir)


@dataclass
class ParsedTransaction:
    date: str                                   # YYYY-MM-DD
    raw_description: str                        # PII maskelenmiş ham POS metni
    clean_description: str
    amount: float                               # TL (harcama pozitif, iade negatif)
    currency: str = "TRY"
    installment_no: Optional[int] = None
    installment_total: Optional[int] = None
    transaction_type: TransactionType = TransactionType.EXPENSE
    category_predicted: Optional[str] = None
    confidence_score: Optional[float] = None


@dataclass
class ParsedStatement:
    bank_name: str
    card_last4: Optional[str]
    period_start: Optional[str]
    period_end: Optional[str]
    total_debt: float                           # Başlıkta yazan dönem borcu; yoksa hesaplanan toplam
    min_payment: Optional[float]
    transactions: List[ParsedTransaction] = field(default_factory=list)
    # Sağlama: başlıktaki dönem borcu ile işlemlerin toplamı karşılaştırılır.
    # None  -> başlıkta dönem borcu bulunamadı, sağlama YAPILAMADI (başarılı sayılmaz).
    checksum_valid: Optional[bool] = None
    checksum_diff: Optional[float] = None
    stated_total_debt: Optional[float] = None
    computed_total: float = 0.0
    due_date: Optional[str] = None
    parser_name: str = "generic"
    fingerprint_score: int = 0
    pii_redaction_count: int = 0
    warnings: List[str] = field(default_factory=list)

    COLUMNS = [
        "date", "raw_description", "clean_description", "amount", "currency",
        "installment_no", "installment_total", "transaction_type", "category", "confidence",
    ]

    def to_dataframe(self) -> pd.DataFrame:
        if not self.transactions:
            return pd.DataFrame(columns=self.COLUMNS)
        return pd.DataFrame([
            {
                "date": t.date,
                "raw_description": t.raw_description,
                "clean_description": t.clean_description,
                "amount": t.amount,
                "currency": t.currency,
                "installment_no": t.installment_no,
                "installment_total": t.installment_total,
                "transaction_type": t.transaction_type.value,
                "category": t.category_predicted,
                "confidence": t.confidence_score,
            }
            for t in self.transactions
        ], columns=self.COLUMNS)


class BaseBankParser(ABC):
    """Tüm banka ayrıştırıcılarının türediği soyut temel sınıf."""

    @abstractmethod
    def fingerprint_score(self, header_text: str) -> int:
        """Ekstre BAŞLIK metninin bu bankaya ait olma skoru (0 = eşleşme yok)."""

    def can_parse(self, header_text: str) -> bool:
        return self.fingerprint_score(header_text) > 0

    @abstractmethod
    def parse_text(self, page_texts: List[str]) -> ParsedStatement:
        """Sayfa metinlerinden yapılandırılmış ParsedStatement üretir."""

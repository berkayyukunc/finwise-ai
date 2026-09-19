"""
FinWise-AI: Profil Güdümlü Ekstre Ayrıştırma Motoru
===================================================
Tüm bankalar için ortak satır ayrıştırıcıyı, başlık alanı çıkarımını ve GERÇEK
sağlama (checksum) mantığını içerir. Banka farkları `BankProfile` ile verilir.

Sağlama (Mülakat İçin):
`total_debt` işlemlerden türetilip kendisiyle karşılaştırılmaz (totoloji). Başlıkta
yazan dönem borcu ayrı okunur, işlemlerin Decimal toplamıyla karşılaştırılır:
    |başlık - Σ işlemler| <= 0.01 TL  -> checksum_valid = True
Başlıkta dönem borcu bulunamazsa checksum_valid = None döner ("doğrulanamadı");
bu durum arayüzde "doğrulandı" olarak GÖSTERİLMEZ.
"""

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import pdfplumber

from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor

from .base_parser import BaseBankParser, ParsedStatement, ParsedTransaction, TransactionType

logger = logging.getLogger(__name__)
fold = TurkishFinancialNLPPreprocessor.fold

CHECKSUM_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class BankProfile:
    """Bir bankanın ekstre şablonunu tanımlayan veri. Kod değil, konfigürasyon."""
    key: str
    bank_name: str
    # Katlanmış (fold) başlık metninde tam kelime olarak aranır: {ifade: ağırlık}
    fingerprints: Dict[str, int] = field(default_factory=dict)
    # İki haneli yıl ve belirsiz ayraçlar için banka bazlı ipucu
    day_first: bool = True
    min_fingerprint_score: int = 2


GENERIC_PROFILE = BankProfile(key="generic", bank_name="Genel / Tanımlanamayan Banka")


class GenericBankParser(BaseBankParser):
    """Profil güdümlü genel Türk bankası ekstre ayrıştırıcısı."""

    profile: BankProfile = GENERIC_PROFILE

    DATE_REGEX = re.compile(r"(?<![\d.,/-])(?:(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})|(\d{4})-(\d{2})-(\d{2}))(?![\d/-])")

    # [işaret] tutar [TL] [işaret]. İki yerel biçim de desteklenir:
    #   TR: 1.250,50 | 250,00      EN: 1,250.50 | 250.00  (bazı bankalar ekstreyi bu biçimde basar)
    # Bakışlar (lookaround) tarihin bir parçasını ('15.08' <- 15.08.2026) ya da oranları (%4.25) tutar sanmayı engeller.
    AMOUNT_REGEX = re.compile(
        r"(?<![\w.,%:])(?P<sign>[-+])?\s?"
        r"(?P<num>\d{1,3}(?:\.\d{3})+,\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+[.,]\d{2})"
        r"(?:\s?(?:TL|TRY)\b)?\s?(?P<mark>\((?:A|B)\)|[AB]\b|[-+](?![\d]))?(?![.,]?\d)"
    )
    # Kredi kartı ekstresinde '+', '-' , '(A)' alacak kaydıdır (ödeme / iade): borcu azaltır
    CREDIT_SIGNS = {"-", "+"}
    CREDIT_MARKS = {"(A)", "A", "-", "+"}

    # Taksit sütunu: '3x1,250.00' (kalan taksit × tutar) ya da 'Son Taksit'
    REMAINING_INSTALLMENT_REGEX = re.compile(r"(?:(?<!\w)(\d{1,2})\s?[xX]\s?\d[\d.,]*|\bSON\s+TAKS[İIi]T)(?:\s+[\d.,]+)?\s*$", re.IGNORECASE)  # sonda puan sütunu olabilir
    PREVIOUS_BALANCE_REGEX = re.compile(r"\b(onceki donem|onceki hesap|onceki aydan|devreden|devir)\b")

    INSTALLMENT_PATTERNS = [
        re.compile(r"\(\s*(\d{1,2})\s*/\s*(\d{1,2})\s*\)"),
        re.compile(r"[İIi]ŞLEM[İIi]N\s+(\d{1,2})\s*/\s*(\d{1,2})", re.IGNORECASE),          # '(2400.00 TL İşlemin 1/4 Taksidi)'
        re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*\.?\s*TAKS[İI][TD]", re.IGNORECASE),
        re.compile(r"(\d{1,2})\s*\.\s*TAKS[İI]T(?:\s*/\s*(\d{1,2}))?", re.IGNORECASE),
        re.compile(r"TAKS[İI]T\s*[:-]?\s*(\d{1,2})\s*/\s*(\d{1,2})", re.IGNORECASE),
    ]
    MAX_INSTALLMENTS = 36

    HEADER_LABELS = {
        "total_debt": ["donem borcu", "toplam borc", "ekstre borcu", "hesap ozeti borcu", "odenmesi gereken tutar"],
        "min_payment": ["asgari odeme tutari", "asgari odeme", "minimum odeme", "asgari tutar"],
        "cutoff_date": ["hesap kesim tarihi", "ekstre tarihi", "kesim tarihi"],
        "due_date": ["son odeme tarihi", "son odeme"],
        "card": ["kart numarasi", "kart no"],
    }
    # Bu ifadeleri içeren satırlar işlem değildir (özet / ara toplam / başlık satırları)
    NON_TRANSACTION_REGEX = re.compile(
        r"\b(donem borcu|toplam|onceki donem|devreden|asgari|kart limiti|kullanilabilir limit|son odeme|hesap kesim|ekstre tarihi|sayfa)\b"
    )

    FEE_WORDS = ["faiz", "kkdf", "bsmv", "gecikme", "yillik uyelik", "kart aidati", "komisyon", "hesap isletim"]
    POINT_WORDS = ["chip-para", "chip para", "bonus kullanim", "worldpuan", "maxipuan", "puan kullanim", "parapuan"]

    # ------------------------------------------------------------ parmak izi
    def fingerprint_score(self, header_text: str) -> int:
        folded = fold(header_text)
        score = sum(w for phrase, w in self.profile.fingerprints.items() if re.search(rf"\b{re.escape(phrase)}\b", folded))
        return score if score >= self.profile.min_fingerprint_score else 0

    # ------------------------------------------------------ düşük seviye araçlar
    @classmethod
    def parse_date(cls, match: re.Match) -> Optional[str]:
        try:
            if match.group(5):
                return datetime(int(match.group(5)), int(match.group(6)), int(match.group(7))).strftime("%Y-%m-%d")
            day, month, year = int(match.group(1)), int(match.group(3)), match.group(4)
            year_i = int(year) if len(year) == 4 else 2000 + int(year)
            return datetime(year_i, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    @classmethod
    def clean_turkish_amount(cls, amount_str: str) -> Tuple[float, bool]:
        """'1.450,50 TL', '-250,00', '120,00 (A)', '550,00-' -> (tutar, negatif_mi)."""
        m = cls.AMOUNT_REGEX.search(amount_str.strip())
        if not m:
            return 0.0, False
        value = cls._match_to_decimal(m)
        return float(value), value < 0

    @classmethod
    def _match_to_decimal(cls, m: re.Match) -> Decimal:
        num = m.group("num")
        decimal_sep = num[-3]  # desen gereği sondan 3. karakter ondalık ayraçtır
        value = Decimal(num.replace("." if decimal_sep == "," else ",", "").replace(",", "."))
        negative = m.group("sign") in cls.CREDIT_SIGNS or (m.group("mark") or "").strip() in cls.CREDIT_MARKS
        return -value if negative else value

    @staticmethod
    def parse_amount_any_locale(text: str) -> Optional[Decimal]:
        """Başlık alanları için: hem '32.699,46' hem '32,699.46' biçimini okur."""
        m = re.search(r"\d[\d.,]*\d|\d", text)
        if not m:
            return None
        s = m.group(0)
        last_sep = max(s.rfind(","), s.rfind("."))
        if last_sep != -1 and len(s) - last_sep - 1 == 2:
            s = re.sub(r"[.,]", "", s[:last_sep]) + "." + s[last_sep + 1:]
        else:
            s = re.sub(r"[.,]", "", s)
        try:
            return Decimal(s)
        except InvalidOperation:
            return None

    @classmethod
    def extract_installment(cls, description: str) -> Tuple[Optional[int], Optional[int]]:
        """'(02/06)', '2/6 TAKSİT', '3.TAKSİT' -> (no, toplam). '24/7 MARKET' taksit DEĞİLDİR."""
        for pattern in cls.INSTALLMENT_PATTERNS:
            m = pattern.search(description)
            if not m:
                continue
            no = int(m.group(1))
            total = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else None
            if total is None:
                if 1 <= no <= cls.MAX_INSTALLMENTS:
                    return no, None
                continue
            if 1 <= no <= total <= cls.MAX_INSTALLMENTS and total >= 2:
                return no, total
        return None, None

    @classmethod
    def classify_transaction_type(cls, description: str, amount: float) -> TransactionType:
        d = fold(description)
        if amount < 0 and re.search(r"\bodeme(niz|si)?\b", d) and not re.search(r"\b(iade|iptal)\b", d):
            return TransactionType.PAYMENT
        if amount < 0 or re.search(r"\b(iade|iptal)\b", d):
            return TransactionType.REFUND
        if amount == 0.0 or any(w in d for w in cls.POINT_WORDS):
            return TransactionType.POINT_REDEMPTION
        if any(w in d for w in cls.FEE_WORDS):
            return TransactionType.FINANCING_FEE
        if cls.extract_installment(description)[0] is not None:
            return TransactionType.INSTALLMENT
        return TransactionType.EXPENSE

    # ------------------------------------------------------------ satır ayrıştırma
    @staticmethod
    def amount_locale(num: str) -> str:
        """'1.250,50' -> 'tr' ; '1,250.50' -> 'en' (ondalık ayraç sondan 3. karakterdir)."""
        return "tr" if num[-3] == "," else "en"

    @classmethod
    def detect_locale(cls, page_texts: List[str]) -> Optional[str]:
        """
        Belgenin sayı biçimi: çoğunluk kazanır. Azınlıktaki biçim, açıklamaya gömülü yabancı tutardır
        ('ORNEK USD 12.99', '(2400.00 TL İşlemin 1/4 Taksidi)') ve işlem tutarı SAYILMAZ.
        """
        counts = {"tr": 0, "en": 0}
        for text in page_texts:
            for m in cls.AMOUNT_REGEX.finditer(text):
                counts[cls.amount_locale(m.group("num"))] += 1
        if counts["tr"] == counts["en"]:
            return None
        return max(counts, key=counts.get)  # type: ignore[arg-type]

    @classmethod
    def _tl_amount_run(cls, line: str, locale: Optional[str]) -> List[re.Match]:
        """
        Satır sonundaki KESİNTİSİZ tutar koşusu (aralarında yalnızca boşluk olan tutarlar).
        Çok sütunlu ekstrelerde ('TL Tutar | USD Tutar | Puan') TL tutarı bu koşunun İLK elemanıdır:
        '... TR 69,90 0,00' -> [69,90, 0,00]. Tek sütunlu ekstrede koşu tek elemanlıdır.
        """
        matches = [m for m in cls.AMOUNT_REGEX.finditer(line) if locale is None or cls.amount_locale(m.group("num")) == locale]
        run: List[re.Match] = []
        for m in reversed(matches):
            gap = line[m.end():run[0].start()] if run else line[m.end():]
            if gap.strip():
                break
            run.insert(0, m)
        return run or matches[-1:]

    @classmethod
    def parse_line(cls, line: str, locale: Optional[str] = None) -> Optional[ParsedTransaction]:
        # Sondaki taksit sütunu ('3x1,250.00' / 'Son Taksit') tutar değildir; önce ayrılır
        remaining: Optional[int] = None
        tail = cls.REMAINING_INSTALLMENT_REGEX.search(line)
        if tail:
            remaining = int(tail.group(1)) if tail.group(1) else 0
            line = line[:tail.start()].rstrip()

        dates = [(m, cls.parse_date(m)) for m in cls.DATE_REGEX.finditer(line)]
        dates = [(m, d) for m, d in dates if d]
        amounts = cls._tl_amount_run(line, locale)
        if not dates or not amounts:
            return None
        if cls.NON_TRANSACTION_REGEX.search(fold(line)):
            return None

        amount_match = amounts[0]
        amount = cls._match_to_decimal(amount_match)

        desc = line[:amount_match.start()]  # tutarın sağındakiler diğer sütunlardır (USD, puan), açıklama değildir
        if not re.search(r"[^\W\d_]", desc):  # tutar açıklamadan önce geliyorsa açıklama sağdadır
            desc = line[:amount_match.start()] + " " + line[amounts[-1].end():]
        for m, _ in dates:
            desc = desc.replace(m.group(0), " ")
        desc = re.sub(r"\s+", " ", desc).strip(" -|")
        if not desc:
            return None

        tx_type = cls.classify_transaction_type(desc, float(amount))
        if tx_type == TransactionType.REFUND and amount > 0:
            amount = -amount  # 'İADE' yazıp işaret taşımayan satır
        inst_no, inst_total = cls.extract_installment(desc)
        if inst_no is not None and inst_total is None and remaining is not None:
            inst_total = inst_no + remaining
        return ParsedTransaction(
            date=dates[0][1], raw_description=desc, clean_description=desc, amount=float(amount),
            installment_no=inst_no, installment_total=inst_total, transaction_type=tx_type,
        )

    @classmethod
    def parse_previous_balance(cls, line: str, locale: Optional[str] = None) -> Optional[Decimal]:
        """'ÖNCEKİ DÖNEM HESAP ÖZETİ BAKİYESİ 5,250.00' -> 5250.00 (işlem değildir; muhasebe için gerekir)."""
        if not cls.PREVIOUS_BALANCE_REGEX.search(fold(line)):
            return None
        amounts = cls._tl_amount_run(line, locale)
        return abs(cls._match_to_decimal(amounts[0])) if amounts else None

    # --------------------------------------------------------------- başlık
    @classmethod
    def split_header(cls, page_texts: List[str], max_lines: int = 60) -> str:
        """İlk sayfada ilk işlem satırına kadar olan metin (parmak izi ve özet alanları için)."""
        header: List[str] = []
        for line in (page_texts[0] if page_texts else "").split("\n")[:max_lines]:
            if cls.parse_line(line) is not None:
                break
            header.append(line)
        return "\n".join(header)

    @classmethod
    def extract_header_fields(cls, header_text: str) -> Dict[str, object]:
        fields: Dict[str, object] = {}
        for line in header_text.split("\n"):
            folded = fold(line)
            for name, labels in cls.HEADER_LABELS.items():
                if name in fields:
                    continue
                pos = next((folded.find(lbl) + len(lbl) for lbl in labels if lbl in folded), None)
                if pos is None or (name == "total_debt" and "onceki" in folded[:pos]):
                    continue
                # Etiketten sonraki İLK değer okunur (aynı satırda başka etiketler olabilir)
                tail = folded[pos:]
                if name in ("total_debt", "min_payment"):
                    value = cls.parse_amount_any_locale(tail)
                    if value is not None:
                        fields[name] = value
                elif name in ("cutoff_date", "due_date"):
                    m = cls.DATE_REGEX.search(tail)
                    if m and cls.parse_date(m):
                        fields[name] = cls.parse_date(m)
                elif name == "card":
                    m = re.search(r"(?:\d{4}|[*xX#]{4}|\d{2}[*xX#]{2})[ -]?(?:[\d*xX#]{4}[ -]?){2}(\d{4})", tail)
                    if m:
                        fields[name] = m.group(1)
        return fields

    # ---------------------------------------------------------------- ana akış
    def parse(self, pdf_bytes: bytes) -> ParsedStatement:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_texts = [(page.extract_text() or "") for page in pdf.pages]
        return self.parse_text(page_texts)

    def parse_text(self, page_texts: List[str]) -> ParsedStatement:
        warnings: List[str] = []
        locale = self.detect_locale(page_texts)
        transactions = [tx for text in page_texts for line in text.split("\n") if (tx := self.parse_line(line, locale)) is not None]
        header = self.extract_header_fields(self.split_header(page_texts))

        if not transactions:
            warnings.append("Hiç işlem satırı ayrıştırılamadı.")

        previous_balance = next((pb for text in page_texts for line in text.split("\n") if (pb := self.parse_previous_balance(line, locale)) is not None), None)

        def total(include_payments: bool) -> Decimal:
            return sum((Decimal(str(round(t.amount, 2))) for t in transactions
                        if include_payments or t.transaction_type != TransactionType.PAYMENT), Decimal("0.00"))

        # Ekstre muhasebesi: dönem borcu = önceki bakiye + TÜM hareketler (ödeme ve iadeler eksi).
        # Önceki bakiye satırı yoksa ödemeler hesaba katılamaz (neyi kapattıkları bilinmez) ve dışarıda bırakılır.
        computed = previous_balance + total(include_payments=True) if previous_balance is not None else total(include_payments=False)
        stated: Optional[Decimal] = header.get("total_debt")  # type: ignore[assignment]
        if stated is None:
            checksum_valid, diff = None, None
            warnings.append("Başlıkta dönem borcu bulunamadı: sağlama yapılamadı.")
        else:
            diff_dec = stated - computed
            checksum_valid, diff = abs(diff_dec) <= CHECKSUM_TOLERANCE, float(diff_dec)
            if not checksum_valid:
                warnings.append(
                    f"Sağlama tutmadı: başlık {stated:,.2f} TL, işlemler toplamı {computed:,.2f} TL (fark {diff_dec:,.2f} TL). "
                    "Olası nedenler: önceki dönem devri, ayrıştırılamayan satır."
                )

        tx_dates = sorted(t.date for t in transactions)
        min_payment = header.get("min_payment")
        return ParsedStatement(
            bank_name=self.profile.bank_name,
            card_last4=header.get("card"),  # type: ignore[arg-type]
            period_start=tx_dates[0] if tx_dates else None,
            period_end=header.get("cutoff_date") or (tx_dates[-1] if tx_dates else None),  # type: ignore[arg-type]
            total_debt=float(stated if stated is not None else computed),
            min_payment=float(min_payment) if min_payment is not None else None,  # type: ignore[arg-type]
            transactions=transactions,
            checksum_valid=checksum_valid,
            checksum_diff=diff,
            stated_total_debt=float(stated) if stated is not None else None,
            computed_total=float(computed),
            previous_balance=float(previous_balance) if previous_balance is not None else None,
            due_date=header.get("due_date"),  # type: ignore[arg-type]
            parser_name=self.profile.key,
            warnings=warnings,
        )

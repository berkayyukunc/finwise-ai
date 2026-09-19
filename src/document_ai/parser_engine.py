"""
FinWise-AI: Master Document AI Ekstre İşleme Motoru
===================================================
Boru hattı sırası bilinçlidir:

1. [Güvenlik]   Boyut / sayfa / açılma bombası denetimi + aktif içerik temizliği.
2. [Okuma]      Metin BELLEK İÇİNDE çıkarılır. Taranmış (metin katmanı olmayan) PDF reddedilir.
3. [Tanıma]     Yalnızca başlık metninden ağırlıklı parmak izi ile banka profili seçilir.
4. [Ayrıştırma] Başlık alanları (dönem borcu, asgari, son 4 hane, tarihler) ve işlemler okunur;
                başlıktaki dönem borcu işlemlerin toplamıyla SAĞLANIR.
5. [KVKK]       PII ancak bundan SONRA karartılır. Önce karartıp sonra ayrıştırmak, sağlama için
                gereken başlık alanlarını da yok eder (eski sürümdeki sabit kodlanmış alanların kök nedeni).
6. [Maskeleme]  İşlem açıklamalarındaki TC / PAN / IBAN / isimler metin düzeyinde maskelenir.

Ham PDF ve ham metin bu fonksiyonun dışına çıkmaz; dönen nesnelerde yalnızca maskelenmiş veri bulunur.
"""

import io
import logging
from typing import Optional, Tuple

import pdfplumber

from .bank_parsers.banks import select_parser
from .bank_parsers.base_parser import ParsedStatement
from .bank_parsers.generic_parser import GenericBankParser
from .redactor import PIIRedactor, has_text_layer
from .sanitizer import PDFSanitizer, PDFSanitizerError

logger = logging.getLogger(__name__)


class ScannedPDFError(PDFSanitizerError):
    """PDF'te metin katmanı yok (taranmış görüntü). OCR bu sürümde desteklenmiyor."""


class DocumentAIEngine:
    """Uçtan uca ekstre ayrıştırma ve güvenlik motoru."""

    @classmethod
    def process_pdf(cls, raw_pdf_bytes: bytes, password: Optional[str] = None) -> Tuple[ParsedStatement, bytes]:
        """
        Returns:
            (ayrıştırılmış ve PII'si maskelenmiş ekstre, PII'si karartılmış PDF baytları)
        Raises:
            PDFPasswordRequiredError, ScannedPDFError, PDFSanitizerError
        """
        sanitized_bytes, san_report = PDFSanitizer.sanitize_with_report(raw_pdf_bytes, password)

        if not has_text_layer(sanitized_bytes):
            raise ScannedPDFError(
                "Bu PDF taranmış bir görüntü (metin katmanı yok). Bu sürüm OCR içermez; "
                "lütfen bankanızın internet şubesinden indirilen dijital ekstreyi yükleyin."
            )

        with pdfplumber.open(io.BytesIO(sanitized_bytes)) as pdf:
            page_texts = [(page.extract_text() or "") for page in pdf.pages]

        parser, score = select_parser(GenericBankParser.split_header(page_texts))
        statement = parser.parse_text(page_texts)
        statement.fingerprint_score = score
        if score == 0:
            statement.warnings.append("Banka şablonu tanınamadı: genel ayrıştırıcı kullanıldı.")
        if san_report.removed:
            statement.warnings.append(f"PDF'ten aktif içerik temizlendi: {', '.join(sorted(set(san_report.removed)))}")

        redacted_bytes, red_report, known_names = PIIRedactor.redact_pdf(sanitized_bytes)
        statement.pii_redaction_count = red_report.total

        for tx in statement.transactions:
            tx.raw_description = PIIRedactor.mask_text(tx.raw_description, known_names)
            tx.clean_description = PIIRedactor.mask_text(tx.clean_description, known_names)

        logger.info(
            "Ekstre işlendi: banka=%s işlem=%d sağlama=%s pii=%d",
            statement.bank_name, len(statement.transactions), statement.checksum_valid, red_report.total,
        )
        return statement, redacted_bytes

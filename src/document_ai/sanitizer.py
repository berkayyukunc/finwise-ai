"""
FinWise-AI: PDF Güvenlik ve Temizleme (Sanitization) Modülü
============================================================
Yüklenen PDF ekstrelerini bellek içinde açar ve:
1. Boyut / sayfa sayısı tavanlarını denetler.
2. Açılma bombasına (decompression bomb) karşı akışları SINIRLI bellekle açarak ölçer.
3. Aktif içeriği yalnızca kök sözlükte değil TÜM nesne ağacında arar ve söker:
   /OpenAction, /AA (sayfa, annotation, form alanı düzeyinde), JavaScript / Launch /
   SubmitForm / ImportData / GoToR / GoToE eylemleri, /Names altındaki JavaScript ve
   EmbeddedFiles ağaçları, FileAttachment / RichMedia / Screen annotation'ları, XFA formları.
4. Şifreli PDF'leri ayrı bir hata tipiyle bildirir (bankalar e-ekstreyi çoğunlukla şifreli yollar).

Tehdit Modeli (Mülakat İçin):
Sunucuda PDF içindeki JavaScript çalışmaz; buradaki temizlik, temizlenmiş/karartılmış
PDF kullanıcıya geri verildiğinde onu bir PDF okuyucuda açacak kişiyi korur. Sunucu
tarafındaki asıl riskler ayrıştırıcı zafiyetleri ve kaynak tüketimidir; bunlara karşı
bu modül boyut/sayfa/açılmış-boyut sınırı koyar. Üretimde buna ek olarak süreç düzeyinde
bellek limiti, zaman aşımı ve sandbox (konteyner, seccomp) gerekir — bu modülün kapsamı dışıdır.
"""

import io
import logging
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pikepdf

logger = logging.getLogger(__name__)


class PDFSanitizerError(Exception):
    """PDF güvenlik ve doğrulama hataları için temel istisna."""


class PDFPasswordRequiredError(PDFSanitizerError):
    """PDF şifreli ve şifre verilmemiş / yanlış."""


@dataclass
class SanitizationReport:
    page_count: int = 0
    removed: List[str] = field(default_factory=list)
    was_encrypted: bool = False

    def note(self, what: str) -> None:
        self.removed.append(what)


class PDFSanitizer:
    MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024
    MAX_PAGES = 30
    MAX_STREAM_DECOMPRESSED_BYTES = 50 * 1024 * 1024
    MAX_TOTAL_DECOMPRESSED_BYTES = 150 * 1024 * 1024

    DANGEROUS_ACTIONS = {"/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE", "/Rendition", "/Movie", "/Sound"}
    DANGEROUS_ANNOTATIONS = {"/FileAttachment", "/RichMedia", "/Screen", "/Movie", "/Sound", "/3D"}
    ACTION_KEYS = ("/A", "/PA")

    @classmethod
    def sanitize(cls, pdf_bytes: bytes, password: Optional[str] = None) -> Tuple[bytes, int]:
        """Geriye dönük uyumlu arayüz: (temiz_baytlar, sayfa_sayısı)."""
        clean, report = cls.sanitize_with_report(pdf_bytes, password)
        return clean, report.page_count

    @classmethod
    def sanitize_with_report(cls, pdf_bytes: bytes, password: Optional[str] = None) -> Tuple[bytes, SanitizationReport]:
        if len(pdf_bytes) > cls.MAX_FILE_SIZE_BYTES:
            raise PDFSanitizerError(
                f"Dosya boyutu çok büyük: {len(pdf_bytes) / (1024 * 1024):.1f} MB "
                f"(sınır {cls.MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB)."
            )

        report = SanitizationReport()
        try:
            pdf = pikepdf.Pdf.open(io.BytesIO(pdf_bytes), password=password or "")
        except pikepdf.PasswordError as e:
            raise PDFPasswordRequiredError(
                "Bu PDF şifreli. Bankanızın bildirdiği ekstre şifresini girin "
                "(çoğunlukla T.C. kimlik ya da müşteri numarasının belirli haneleri)."
            ) from e
        except pikepdf.PdfError as e:
            raise PDFSanitizerError(f"Geçersiz veya bozuk PDF: {e}") from e

        try:
            with pdf:
                report.was_encrypted = bool(pdf.is_encrypted)
                report.page_count = len(pdf.pages)
                if report.page_count == 0:
                    raise PDFSanitizerError("PDF dosyası boş (0 sayfa).")
                if report.page_count > cls.MAX_PAGES:
                    raise PDFSanitizerError(f"Sayfa sayısı çok fazla: {report.page_count} (sınır {cls.MAX_PAGES}).")

                cls._check_decompression_budget(pdf)
                cls._strip_catalog(pdf, report)
                cls._strip_object_tree(pdf, report)

                out = io.BytesIO()
                pdf.save(out, encryption=False)  # şifre kaldırılır: sonraki aşamalar bellek içinde çalışır
        except PDFSanitizerError:
            raise
        except pikepdf.PdfError as e:
            raise PDFSanitizerError(f"Geçersiz veya bozuk PDF: {e}") from e

        if report.removed:
            logger.warning("PDF'ten aktif içerik söküldü: %s", ", ".join(sorted(set(report.removed))))
        return out.getvalue(), report

    # ----------------------------------------------------------- DoS koruması
    @classmethod
    def _check_decompression_budget(cls, pdf: pikepdf.Pdf) -> None:
        """
        Flate akışlarını zlib.decompressobj ile en fazla (sınır + 1) bayt açar; böylece
        300 MB'a açılan 300 KB'lık bir akış belleğe hiç tam olarak alınmadan reddedilir.
        """
        total = 0
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Stream):
                continue
            filters = obj.get("/Filter")
            names = [str(f) for f in filters] if isinstance(filters, pikepdf.Array) else [str(filters)] if filters is not None else []
            raw = obj.read_raw_bytes()
            if names[:1] == ["/FlateDecode"]:
                size = cls._bounded_inflate_size(raw, cls.MAX_STREAM_DECOMPRESSED_BYTES)
            else:
                size = len(raw)
            if size > cls.MAX_STREAM_DECOMPRESSED_BYTES:
                raise PDFSanitizerError("PDF, açıldığında aşırı büyüyen bir akış içeriyor (decompression bomb şüphesi).")
            total += size
            if total > cls.MAX_TOTAL_DECOMPRESSED_BYTES:
                raise PDFSanitizerError("PDF'in açılmış toplam boyutu sınırı aşıyor (decompression bomb şüphesi).")

    @staticmethod
    def _bounded_inflate_size(raw: bytes, limit: int) -> int:
        inflater = zlib.decompressobj()
        size, chunk = 0, 1024 * 1024
        data = raw
        try:
            while data and size <= limit:
                out = inflater.decompress(data, chunk)
                size += len(out)
                data = inflater.unconsumed_tail
                if not out and not data:
                    break
        except zlib.error:
            return len(raw)  # bozuk akış: QPDF kaydederken ele alır
        return size

    # ------------------------------------------------------- aktif içerik
    @classmethod
    def _strip_catalog(cls, pdf: pikepdf.Pdf, report: SanitizationReport) -> None:
        root = pdf.Root
        for key in ("/OpenAction", "/AA", "/JavaScript"):
            if key in root:
                del root[key]
                report.note(f"Root{key}")
        names = root.get("/Names")
        if names is not None:
            for key in ("/JavaScript", "/EmbeddedFiles"):
                if key in names:
                    del names[key]
                    report.note(f"Names{key}")
        acroform = root.get("/AcroForm")
        if acroform is not None and "/XFA" in acroform:
            del acroform["/XFA"]
            report.note("AcroForm/XFA")
        if "/Collection" in root:  # PDF portfolio (ekli dosya koleksiyonu)
            del root["/Collection"]
            report.note("Root/Collection")

    @classmethod
    def _strip_object_tree(cls, pdf: pikepdf.Pdf, report: SanitizationReport) -> None:
        for obj in pdf.objects:
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            if "/AA" in obj:
                del obj["/AA"]
                report.note("/AA")
            for key in cls.ACTION_KEYS:
                action = obj.get(key)
                if isinstance(action, pikepdf.Dictionary) and str(action.get("/S")) in cls.DANGEROUS_ACTIONS:
                    report.note(f"Action{action.get('/S')}")
                    del obj[key]
            annots = obj.get("/Annots")
            if isinstance(annots, pikepdf.Array):
                kept = [a for a in annots if not (isinstance(a, pikepdf.Dictionary) and str(a.get("/Subtype")) in cls.DANGEROUS_ANNOTATIONS)]
                if len(kept) != len(annots):
                    report.note("Annotation(ek/medya)")
                    obj["/Annots"] = pikepdf.Array(kept)
            # Zincirlenmiş eylemler: /Next ile gizlenen JavaScript
            nxt = obj.get("/Next")
            if isinstance(nxt, pikepdf.Dictionary) and str(nxt.get("/S")) in cls.DANGEROUS_ACTIONS:
                del obj["/Next"]
                report.note("Action/Next")

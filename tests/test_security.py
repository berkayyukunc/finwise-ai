"""Negatif güvenlik testleri: zararlı, şifreli, bozuk, aşırı büyük ve taranmış PDF'ler."""

import io

import pikepdf
import pymupdf
import pytest

from src.document_ai.parser_engine import DocumentAIEngine, ScannedPDFError
from src.document_ai.sanitizer import PDFPasswordRequiredError, PDFSanitizer, PDFSanitizerError
from tests.conftest import build_pdf


def _weaponize(pdf_bytes: bytes) -> bytes:
    pdf = pikepdf.Pdf.open(io.BytesIO(pdf_bytes))
    js = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("app.alert('x')")))
    launch = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.Launch, F=pikepdf.String("cmd.exe")))
    chained = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.URI, URI=pikepdf.String("https://example.com"), Next=js))
    page = pdf.pages[0].obj
    page["/AA"] = pikepdf.Dictionary(O=js)
    page["/Annots"] = pikepdf.Array([
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 595, 842], A=launch)),
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10], A=chained)),
        pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.FileAttachment, Rect=[0, 0, 10, 10])),
    ])
    pdf.attachments["payload.exe"] = b"MZ-evil"
    pdf.Root["/OpenAction"] = js
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(XFA=pikepdf.String("<xfa/>"), Fields=pikepdf.Array())
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def test_active_content_is_stripped_everywhere_not_just_root():
    clean, report = PDFSanitizer.sanitize_with_report(_weaponize(build_pdf()))
    out = pikepdf.Pdf.open(io.BytesIO(clean))

    assert "/OpenAction" not in out.Root
    assert "/AA" not in out.pages[0].obj
    assert len(out.attachments) == 0
    assert "/XFA" not in out.Root.get("/AcroForm", {})
    subtypes = [str(a.get("/Subtype")) for a in out.pages[0].obj.get("/Annots", [])]
    assert "/FileAttachment" not in subtypes
    for obj in out.objects:  # belgenin hiçbir yerinde JavaScript / Launch eylemi kalmamalı
        if isinstance(obj, pikepdf.Dictionary):
            assert str(obj.get("/S")) not in ("/JavaScript", "/Launch")
    assert report.removed, "Rapor sökülenleri listelemeli"


def test_sanitized_weaponized_pdf_still_parses():
    stmt, _ = DocumentAIEngine.process_pdf(_weaponize(build_pdf()))
    assert len(stmt.transactions) == 2
    assert any("aktif içerik" in w for w in stmt.warnings)


def test_decompression_bomb_is_rejected_without_full_inflation():
    pdf = pikepdf.Pdf.open(io.BytesIO(build_pdf()))
    pdf.pages[0].obj["/Contents"] = pdf.make_stream(b" " * (PDFSanitizer.MAX_STREAM_DECOMPRESSED_BYTES + 1024))
    out = io.BytesIO()
    pdf.save(out, compress_streams=True)
    assert len(out.getvalue()) < 1024 * 1024, "Bomba sıkıştırılmış halde küçük olmalı"
    with pytest.raises(PDFSanitizerError, match="decompression bomb"):
        PDFSanitizer.sanitize(out.getvalue())


def test_bounded_inflate_stops_at_limit():
    import zlib
    raw = zlib.compress(b"\0" * (10 * 1024 * 1024))
    assert PDFSanitizer._bounded_inflate_size(raw, limit=1024 * 1024) <= 3 * 1024 * 1024


def test_encrypted_pdf_asks_for_password_then_works():
    enc = io.BytesIO()
    pikepdf.Pdf.open(io.BytesIO(build_pdf())).save(enc, encryption=pikepdf.Encryption(user="1234", owner="owner"))
    with pytest.raises(PDFPasswordRequiredError):
        DocumentAIEngine.process_pdf(enc.getvalue())
    with pytest.raises(PDFPasswordRequiredError):
        DocumentAIEngine.process_pdf(enc.getvalue(), password="yanlis")
    stmt, redacted = DocumentAIEngine.process_pdf(enc.getvalue(), password="1234")
    assert len(stmt.transactions) == 2
    assert not pikepdf.Pdf.open(io.BytesIO(redacted)).is_encrypted


@pytest.mark.parametrize("payload", [b"", b"not a pdf at all", b"%PDF-1.7\n" + b"\x00" * 200])
def test_corrupt_input_raises_domain_error(payload):
    with pytest.raises(PDFSanitizerError):
        DocumentAIEngine.process_pdf(payload)


def test_oversized_file_rejected_before_parsing():
    with pytest.raises(PDFSanitizerError, match="boyutu"):
        PDFSanitizer.sanitize(b"%PDF-1.7" + b"0" * (PDFSanitizer.MAX_FILE_SIZE_BYTES + 1))


def test_too_many_pages_rejected():
    doc = pymupdf.open()
    for _ in range(PDFSanitizer.MAX_PAGES + 1):
        doc.new_page()
    with pytest.raises(PDFSanitizerError, match="Sayfa sayısı"):
        PDFSanitizer.sanitize(doc.tobytes())


def test_scanned_pdf_without_text_layer_is_reported_honestly():
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50))
    pix.clear_with(200)
    page.insert_image(page.rect, pixmap=pix)
    with pytest.raises(ScannedPDFError, match="OCR"):
        DocumentAIEngine.process_pdf(doc.tobytes())

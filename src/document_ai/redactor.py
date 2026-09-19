"""
FinWise-AI: PII Tespit ve Karartma (Redaction) Modülü
=====================================================
Sabit "ilk sayfanın üst %20'si" varsayımı yerine, PII'yi TÜM sayfalarda kelime
koordinatlarıyla bulur ve içerik akışından fiziksel olarak siler.

Katmanlar:
1. Etiketli alanlar   : 'Kart Sahibi:', 'Sayın', 'Adres:', 'T.C. Kimlik No:' ... etiketinden
                         sonraki değer (adres için hizalı devam satırları dahil).
2. Desen + sağlama    : T.C. kimlik (mod-10/11), PAN (Luhn), IBAN, e-posta, telefon.
3. İsim yayılımı      : 1. katmanda öğrenilen kart sahibi adı, belgenin her yerinde
                         (2. sayfa başlıkları, 'HAVALE <AD SOYAD>' satırları) yeniden aranır.
4. Havale karşı tarafı: HAVALE / EFT / FAST satırlarındaki üçüncü kişi adları.
5. Metadata           : /Info (Author, Title...) ve XMP silinir; dosya garbage=4 ile
                         yeniden yazılır (yetim nesnelerde eski içerik kalmaz).
6. Emniyet supabı     : İlk sayfada hiçbir isim etiketi bulunamazsa (etiketsiz, pencereli
                         zarf tipi adres bloğu), ilk işlem satırının üstündeki bölge karartılır.

Bilinen Sınır (dürüstlük): Etiketsiz ve serbest metin içindeki kişi adları (ör. işyeri
adı olarak geçen şahıs isimleri) kural tabanlı yaklaşımla yakalanamaz; bunun için NER gerekir.
"""

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Tuple

import pymupdf

from src.nlp.preprocessor import TurkishFinancialNLPPreprocessor

logger = logging.getLogger(__name__)
fold = TurkishFinancialNLPPreprocessor.fold

Word = Tuple[float, float, float, float, str]  # x0, y0, x1, y1, metin


@dataclass
class RedactionReport:
    counts: Dict[str, int] = field(default_factory=dict)
    fallback_zone_used: bool = False
    metadata_scrubbed: bool = False

    def add(self, kind: str, n: int = 1) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + n

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class _Line:
    words: List[Word]

    def __post_init__(self) -> None:
        self.text, self.folded = "", ""
        self.spans: List[Tuple[int, int]] = []      # özgün metindeki kelime aralıkları
        self.fspans: List[Tuple[int, int]] = []     # katlanmış metindeki kelime aralıkları
        for w in self.words:
            for attr, spans, token in (("text", self.spans, w[4]), ("folded", self.fspans, fold(w[4]))):
                current = getattr(self, attr)
                start = len(current) + (1 if current else 0)
                setattr(self, attr, f"{current} {token}" if current else token)
                spans.append((start, start + len(token)))

    @property
    def x0(self) -> float:
        return self.words[0][0]

    @property
    def y0(self) -> float:
        return min(w[1] for w in self.words)

    @property
    def height(self) -> float:
        return max(w[3] - w[1] for w in self.words)

    def words_in(self, start: int, end: int, folded: bool = False) -> List[int]:
        spans = self.fspans if folded else self.spans
        return [i for i, (s, e) in enumerate(spans) if s < end and e > start]


class PIIRedactor:
    TC_REGEX = re.compile(r"(?<!\d)([1-9]\d{10})(?!\d)")
    CARD_REGEX = re.compile(r"(?<![\d*])(?:\d[ -]?){12,18}\d(?![\d*])")
    IBAN_REGEX = re.compile(r"\bTR\d{2}(?: ?\d{4}){5} ?\d{2}\b", re.IGNORECASE)
    EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    PHONE_REGEX = re.compile(r"(?<![\d*])(?:\+90[ -]?|0)?5\d{2}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?!\d)")

    NAME_LABELS = ["kart sahibi", "kart hamili", "hesap sahibi", "musteri adi", "adi soyadi", "ad soyad", "sayin"]
    VALUE_LABELS = {
        "address": ["adres"],
        "tc": ["t.c. kimlik no", "tc kimlik no", "t.c. kimlik", "tc kimlik", "tckn"],
        "customer_no": ["musteri numarasi", "musteri no"],
        "phone": ["telefon", "cep tel", "gsm"],
        "email": ["e-posta", "eposta", "e-mail", "email"],
    }
    # Değer okumayı durduran, PII olmayan etiketler
    STOP_LABELS = ["donem borcu", "asgari odeme", "son odeme", "hesap kesim", "ekstre tarihi", "kart no", "kart numarasi", "kart limiti", "toplam borc"]

    TRANSFER_WORDS = {"havale", "eft", "fast", "transfer", "transferi", "gonderim", "gonderimi"}
    TRANSFER_FILLERS = {"giden", "gelen", "para", "karttan", "karta", "kart", "hesaba", "hesaptan", "islemi", "alici", "gonderen", "-", ":"}
    TRANSFER_FEE_WORDS = {"ucreti", "ucret", "masrafi", "masraf", "komisyonu", "komisyon", "bsmv"}
    MAX_NAME_TOKENS = 4
    PERSON_MASK = "[KİŞİ]"

    # ------------------------------------------------------------ doğrulayıcılar
    @staticmethod
    def is_valid_tc(tc_str: str) -> bool:
        """T.C. Kimlik No sağlaması (10. ve 11. hane kuralları)."""
        if len(tc_str) != 11 or not tc_str.isdigit() or tc_str[0] == "0":
            return False
        d = [int(c) for c in tc_str]
        if ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10 != d[9]:
            return False
        return sum(d[:10]) % 10 == d[10]

    @staticmethod
    def is_luhn_valid(card_number: str) -> bool:
        clean = re.sub(r"\D", "", card_number)
        if not 13 <= len(clean) <= 19:
            return False
        checksum = 0
        for i, ch in enumerate(reversed(clean)):
            n = int(ch)
            if i % 2 == 1:
                n = n * 2 - 9 if n * 2 > 9 else n * 2
            checksum += n
        return checksum % 10 == 0

    # ---------------------------------------------------------- metin maskeleme
    @classmethod
    def _pattern_matches(cls, text: str) -> List[Tuple[int, int, str]]:
        """Metindeki doğrulanmış PII aralıkları: (başlangıç, bitiş, tür)."""
        hits: List[Tuple[int, int, str]] = []
        hits += [(m.start(), m.end(), "pan") for m in cls.CARD_REGEX.finditer(text) if cls.is_luhn_valid(m.group(0))]
        hits += [(m.start(), m.end(), "tc") for m in cls.TC_REGEX.finditer(text) if cls.is_valid_tc(m.group(1))]
        hits += [(m.start(), m.end(), "iban") for m in cls.IBAN_REGEX.finditer(text)]
        hits += [(m.start(), m.end(), "email") for m in cls.EMAIL_REGEX.finditer(text)]
        hits += [(m.start(), m.end(), "phone") for m in cls.PHONE_REGEX.finditer(text)]
        hits.sort()
        merged: List[Tuple[int, int, str]] = []
        for h in hits:  # çakışanlarda ilk (en soldaki / en uzun) kazanır
            if not merged or h[0] >= merged[-1][1]:
                merged.append(h)
        return merged

    @classmethod
    def _transfer_name_token_indexes(cls, tokens: Sequence[str]) -> List[int]:
        """'HAVALE AHMET YILMAZ 4829' -> [1, 2]. Ücret satırlarına ('HAVALE EFT UCRETI') dokunmaz."""
        folded = [fold(t).strip(".,:;") for t in tokens]
        if not cls.TRANSFER_WORDS & set(folded) or cls.TRANSFER_FEE_WORDS & set(folded):
            return []
        last_kw = max(i for i, t in enumerate(folded) if t in cls.TRANSFER_WORDS)
        out: List[int] = []
        for i in range(last_kw + 1, len(tokens)):
            if folded[i] in cls.TRANSFER_FILLERS or folded[i] in cls.TRANSFER_WORDS:
                if out:
                    break
                continue
            if not re.fullmatch(r"[^\W\d_]{2,}\.?", tokens[i]) or len(out) >= cls.MAX_NAME_TOKENS:
                break
            out.append(i)
        return out

    @classmethod
    def mask_text(cls, text: str, known_names: Iterable[str] = ()) -> str:
        """Metindeki TC, PAN, IBAN, e-posta, telefon, bilinen isim ve havale karşı tarafını maskeler."""
        def render(kind: str, raw: str) -> str:
            digits = re.sub(r"\D", "", raw)
            return {
                "pan": f"{digits[:4]} **** **** {digits[-4:]}",
                "tc": f"{digits[:2]}*******{digits[-2:]}",
                "iban": f"TR** **** **** **** **** **{digits[-2:]}",
                "email": "[E-POSTA]",
                "phone": "[TELEFON]",
            }[kind]

        out, cursor = [], 0
        for start, end, kind in cls._pattern_matches(text):
            out += [text[cursor:start], render(kind, text[start:end])]
            cursor = end
        masked = "".join(out) + text[cursor:]

        for name in known_names:
            tokens = [re.escape(t) for t in fold(name).split()]
            if len(tokens) >= 2:
                pattern = re.compile(r"\s+".join(tokens))
                spans = [m.span() for m in pattern.finditer(fold(masked))] if len(fold(masked)) == len(masked) else []
                for s, e in reversed(spans):
                    masked = masked[:s] + cls.PERSON_MASK + masked[e:]

        tokens = masked.split()
        idx = cls._transfer_name_token_indexes(tokens)
        if idx:
            tokens[idx[0]:idx[-1] + 1] = [cls.PERSON_MASK]
            masked = " ".join(tokens)
        return masked

    # ---------------------------------------------------------- PDF karartma
    @staticmethod
    def _page_lines(page: "pymupdf.Page") -> List[_Line]:
        grouped: Dict[Tuple[int, int], List[Word]] = {}
        for x0, y0, x1, y1, text, block, line, _ in page.get_text("words"):
            grouped.setdefault((block, line), []).append((x0, y0, x1, y1, text))
        lines = [_Line(sorted(ws)) for ws in grouped.values()]
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))

    @classmethod
    def _label_hits(cls, line: _Line) -> List[Tuple[int, int, str]]:
        """Satırdaki etiketler: (başlangıç, bitiş, tür). Tür: name / address / ... / stop."""
        table = [("name", cls.NAME_LABELS), ("stop", cls.STOP_LABELS)] + list(cls.VALUE_LABELS.items())
        hits = []
        for kind, labels in table:
            for label in labels:
                for m in re.finditer(rf"(?<![^\W\d_]){re.escape(label)}(?![^\W\d_])\s*:?", line.folded):
                    hits.append((m.start(), m.end(), kind))
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        dedup: List[Tuple[int, int, str]] = []
        for h in hits:
            if not dedup or h[0] >= dedup[-1][1]:
                dedup.append(h)
        return dedup

    @classmethod
    def find_pii_rects(cls, lines: List[_Line], known_names: List[str], report: RedactionReport) -> List["pymupdf.Rect"]:
        from .bank_parsers.generic_parser import GenericBankParser  # döngüsel import'u önlemek için yerel

        rects: List[pymupdf.Rect] = []

        def mark(line: _Line, idxs: Iterable[int], kind: str) -> None:
            idxs = list(idxs)
            if idxs:
                rects.extend(pymupdf.Rect(line.words[i][:4]) for i in idxs)
                report.add(kind)

        for li, line in enumerate(lines):
            # İşlem satırlarında etiket aranmaz ('TELEFON AKSESUAR' bir işyeri adıdır, etiket değil)
            labels = [] if GenericBankParser.parse_line(line.text) else cls._label_hits(line)
            for k, (_start, end, kind) in enumerate(labels):
                if kind == "stop":
                    continue
                value_end = labels[k + 1][0] if k + 1 < len(labels) else len(line.folded)
                idxs = [i for i in line.words_in(end, value_end, folded=True) if line.fspans[i][0] >= end]
                mark(line, idxs, kind)
                if kind == "name" and idxs:
                    name = " ".join(line.words[i][4] for i in idxs).strip(" :,")
                    if len(name.split()) >= 2 and name not in known_names:
                        known_names.append(name)
                # 'Adres:' ya da 'Sayın <ad>' altındaki hizalı, etiketsiz satırlar adres devamıdır
                if kind in ("address", "name") and idxs:
                    anchor_x = line.words[idxs[0]][0]
                    prev = line
                    for nxt in lines[li + 1: li + 4]:  # hizalı devam satırları
                        aligned = abs(nxt.x0 - anchor_x) < 4 or abs(nxt.x0 - line.x0) < 4
                        close = 0 < nxt.y0 - prev.y0 < 2.2 * max(prev.height, 1)
                        if not (aligned and close) or cls._label_hits(nxt) or GenericBankParser.parse_line(nxt.text):
                            break
                        mark(nxt, range(len(nxt.words)), "address")
                        prev = nxt

            for start, end, kind in cls._pattern_matches(line.text):
                mark(line, line.words_in(start, end), kind)

            mark(line, cls._transfer_name_token_indexes([w[4] for w in line.words]), "transfer_party")
        return rects

    @classmethod
    def _name_rects(cls, lines: List[_Line], known_names: List[str], report: RedactionReport) -> List["pymupdf.Rect"]:
        rects = []
        for name in known_names:
            pattern = re.compile(r"\s+".join(re.escape(t) for t in fold(name).split()))
            for line in lines:
                for m in pattern.finditer(line.folded):
                    rects.extend(pymupdf.Rect(line.words[i][:4]) for i in line.words_in(m.start(), m.end(), folded=True))
                    report.add("name_propagated")
        return rects

    @classmethod
    def redact_pdf(cls, pdf_bytes: bytes) -> Tuple[bytes, RedactionReport, List[str]]:
        """
        Returns:
            (karartılmış PDF baytları, rapor, öğrenilen kart sahibi adları).
            İsim listesi yalnızca bellek içinde işlem açıklamalarını maskelemek içindir;
            loglanmaz ve ParsedStatement'a yazılmaz.
        """
        from .bank_parsers.generic_parser import GenericBankParser

        report, known_names = RedactionReport(), []
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            pages_lines = [cls._page_lines(page) for page in doc]
            page_rects = [cls.find_pii_rects(lines, known_names, report) for lines in pages_lines]
            for rects, lines in zip(page_rects, pages_lines):
                rects.extend(cls._name_rects(lines, known_names, report))

            if len(doc) > 0 and not known_names:
                first_tx_y = next((ln.y0 for ln in pages_lines[0] if GenericBankParser.parse_line(ln.text)), None)
                page_rect = doc[0].rect
                zone_bottom = (first_tx_y - 2) if first_tx_y else page_rect.y0 + page_rect.height * 0.25
                page_rects[0].append(pymupdf.Rect(page_rect.x0, page_rect.y0, page_rect.x1, zone_bottom))
                report.fallback_zone_used = True
                report.add("fallback_zone")

            for page, rects in zip(doc, page_rects):
                for r in rects:
                    page.add_redact_annot(r + (-1, -1, 1, 1), fill=(0, 0, 0))
                if rects:
                    page.apply_redactions()

            doc.set_metadata({})
            doc.del_xml_metadata()
            report.metadata_scrubbed = True

            out = io.BytesIO()
            doc.save(out, garbage=4, deflate=True, clean=True)
        finally:
            doc.close()

        logger.info("PII karartma tamamlandı: %s (emniyet bölgesi: %s)", report.counts, report.fallback_zone_used)
        return out.getvalue(), report, known_names

    @classmethod
    def visually_redact_first_page(cls, pdf_bytes: bytes) -> bytes:
        """Geriye dönük uyumluluk: artık tüm sayfalarda içerik tabanlı karartma yapar."""
        return cls.redact_pdf(pdf_bytes)[0]


def has_text_layer(pdf_bytes: bytes, min_chars: int = 20) -> bool:
    """Taranmış (yalnızca görüntü) PDF tespiti: metin katmanı yoksa ayrıştırma yapılamaz."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return sum(len(page.get_text().strip()) for page in doc) >= min_chars
    finally:
        doc.close()


__all__ = ["PIIRedactor", "RedactionReport", "has_text_layer"]

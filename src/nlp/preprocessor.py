"""
FinWise-AI: Türkçe Finansal Metin Ön İşleme ve Normalizasyon Modülü
===================================================================
Bu modül, Türk bankacılık ekstrelerindeki ve POS cihazlarındaki özel
metin problemlerini çözer:
1. Unicode normalizasyonu (NFC): PDF'lerden gelen ayrıştırılmış 'I' + U+0307
   dizilerini tek kod noktasına ('İ') birleştirir.
2. PDF font bozulmalarını (Latin-1 / Latin-5 karışıklığı: 'Ý' -> 'İ', 'Þ' -> 'Ş') onarır.
3. Tam diakritik katlama (folding): yalnızca ı/i değil, 6 Türkçe çiftin tamamı
   (ç/c, ğ/g, ı/i, ö/o, ş/s, ü/u) ASCII eşdeğerine indirgenir.
4. POS unvan çöplerini ("TİC", "A.Ş.", "LTD", "ŞB", "İST", "TR") ve ödeme kuruluşu
   öneklerini ("IYZICO/", "PAYTR*") YALNIZCA tam kelime olarak temizler.
5. Tarih, tutar ve referans kodlarını genel etiketlerle (<date>, <cur>, <num>) maskeler.

Eğitici Not (Mülakat İçin):
POS terminalleri aynı markayı bazen ASCII ('KOCTAS'), bazen Türkçe ('KOÇTAŞ') basar.
Yalnızca ı/i eşitlemek yetmez: eğitim verisi ASCII ise 'koçtaş' n-gram'ları modelin
hiç görmediği öznitelikler üretir. Bu yüzden katlama 6 çiftin tamamına uygulanır.
Gürültü regex'lerinde kelime sonu sınırı (?!\\w) zorunludur; aksi halde 'ASUS' -> 'us',
'SBARRO' -> 'arro' gibi marka bozulmaları oluşur (bkz. tests/test_nlp.py regresyonları).
"""

import re
import unicodedata


class TurkishFinancialNLPPreprocessor:
    """Türkçe finans ve POS metinleri için uzman önişlemci."""

    # Latin-5 (ISO-8859-9) metnin Latin-1 olarak çözülmesinden doğan bozulmalar
    FONT_CORRECTIONS = {
        "Ð": "Ğ",
        "Ý": "İ",
        "Þ": "Ş",
        "ð": "ğ",
        "ý": "ı",
        "þ": "ş",
    }

    _FOLD_MAP = str.maketrans({
        "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g",
        "ı": "i", "I": "i", "İ": "i",
        "ö": "o", "Ö": "o",
        "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
        "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
    })

    # Aşağıdaki desenler KATLANMIŞ (küçük harf + ASCII) metin üzerinde çalışır.
    # Her desen tam kelime eşleşir: \b ... (?!\w)
    NOISE_TERMS = [
        r"tic(?:aret)?\.?",
        r"a\.?s\.?",
        r"t\.a\.s\.?",
        r"ltd\.?",
        r"limited",
        r"sti\.?",
        r"san\.",
        r"sanayi",
        r"subesi",
        r"sb\.?",
        r"tr",
        r"ist",
        r"istanbul",
        r"ankara",
        r"izmir",
        r"merkez",
        r"pos",
        r"online",
        r"etic",
    ]
    NOISE_REGEX = re.compile(r"\b(?:" + "|".join(NOISE_TERMS) + r")(?!\w)")

    # Ödeme kuruluşu / sanal POS önekleri kategori bilgisi taşımaz: "IYZICO/ZARA" -> "ZARA"
    FACILITATOR_REGEX = re.compile(
        r"^\s*(?:iyzico|iyz|paytr|pyt|param|sipay|payu|papara|sbm|bd)\s*[*/]\s*"
    )

    DATE_REGEX = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")
    CURRENCY_REGEX = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b(?:\s*(?:tl|try|usd|eur)\b)?|\b\d+(?:[.,]\d+)?\s*(?:tl|try|usd|eur)\b")
    NUMBER_REGEX = re.compile(r"\b\d+(?:[.,/]\d+)*\b")

    @classmethod
    def normalize_unicode(cls, text: str) -> str:
        """NFC birleştirme + font onarımı. Artık kalan birleştirici noktaları (U+0307) atar."""
        text = unicodedata.normalize("NFC", text)
        for wrong, right in cls.FONT_CORRECTIONS.items():
            text = text.replace(wrong, right)
        return text.replace("̇", "")

    @classmethod
    def fix_pdf_font_artifacts(cls, text: str) -> str:
        """Geriye dönük uyumluluk için: normalize_unicode ile aynıdır."""
        return cls.normalize_unicode(text)

    @classmethod
    def turkish_lower(cls, text: str) -> str:
        """Türkçe kurallarına uygun küçük harf dönüşümü ('I' -> 'ı', 'İ' -> 'i')."""
        text = cls.normalize_unicode(text)
        mapping = {"İ": "i", "I": "ı"}
        return "".join(mapping.get(ch, ch.lower()) for ch in text)

    @classmethod
    def fold(cls, text: str) -> str:
        """
        Arama, parmak izi ve model girdisi için kanonik biçim:
        küçük harf + tüm Türkçe diakritikler ASCII'ye indirgenmiş.
        'KOÇTAŞ', 'KOCTAS', 'koçtaş' -> 'koctas'
        """
        text = cls.normalize_unicode(text)
        return text.translate(cls._FOLD_MAP).lower()

    @classmethod
    def to_ascii_search_form(cls, text: str) -> str:
        """Geriye dönük uyumluluk için: fold ile aynıdır."""
        return cls.fold(text)

    @classmethod
    def clean_pos_text(cls, raw_pos_text: str, mask_numbers: bool = True) -> str:
        """
        Ham POS metnini NLP modeline girmeye hazır kanonik metne dönüştürür.
        İşlem idempotenttir: clean(clean(x)) == clean(x).
        """
        if not raw_pos_text:
            return ""

        # 1. Unicode + font onarımı, 2. Tam katlama (küçük harf + ASCII)
        t = cls.fold(raw_pos_text)

        # 3. Ödeme kuruluşu öneki
        t = cls.FACILITATOR_REGEX.sub(" ", t)

        # 4. Tarih ve tutarları maskele
        t = cls.DATE_REGEX.sub(" <date> ", t)
        t = cls.CURRENCY_REGEX.sub(" <cur> ", t)

        # 5. Kurumsal gürültü (yalnızca tam kelime)
        t = cls.NOISE_REGEX.sub(" ", t)

        # 6. Kalan sayılar (şube / terminal / referans numaraları)
        if mask_numbers:
            t = cls.NUMBER_REGEX.sub(" <num> ", t)

        # 7. Özel karakterler ve fazla boşluklar
        t = re.sub(r"[^\w\s<>]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

"""
FinWise-AI: Türk Bankaları İçin Ekstre Profilleri
=================================================
Her banka bir `BankProfile` verisidir; ayrıştırma motoru ortaktır (generic_parser).

Dürüstlük Notu:
Bu profiller gerçek banka ekstreleri üzerinde DOĞRULANMAMIŞTIR (gerçek ekstre KVKK
gereği repoya konamaz). Parmak izleri bankaların kamuya açık marka adlarına, test
şablonları ise `scripts/generate_synthetic_pdf.py` içindeki sentetik düzenlere dayanır.
Gerçek bir ekstrede sağlama tutmazsa sistem bunu `checksum_valid=False` ve uyarı ile bildirir.

Parmak izi kuralları:
- Yalnızca BAŞLIK metninde aranır (işlem açıklamasındaki 'MAXIMUM AVM' İş Bankası yapmaz).
- Tam kelime eşleşir ve ağırlıklıdır; genel kelimeler ('world', 'türkiye', 'bankası') kullanılmaz.
- Tek başına zayıf işaret (ör. yalnızca 'bonus': DenizBank Bonus da vardır) eşiği geçemez.
"""

from typing import List, Tuple, Type

from .generic_parser import BankProfile, GenericBankParser


def _make_parser(profile: BankProfile) -> Type[GenericBankParser]:
    return type(f"{profile.key.title().replace('_', '')}Parser", (GenericBankParser,), {"profile": profile})


BANK_PROFILES: List[BankProfile] = [
    BankProfile("ziraat", "Ziraat Bankası", {"ziraat bankasi": 3, "ziraat": 2, "bankkart": 2}),
    BankProfile("garanti", "Garanti BBVA", {"garanti bbva": 3, "garanti bankasi": 3, "garanti": 2, "bonus": 1}),
    BankProfile("isbank", "Türkiye İş Bankası", {"turkiye is bankasi": 3, "is bankasi": 3, "isbank": 3, "maximum kart": 2, "maximiles": 1}),
    BankProfile("yapikredi", "Yapı Kredi Bankası", {"yapi ve kredi bankasi": 3, "yapi kredi": 3, "worldcard": 2}),
    BankProfile("akbank", "Akbank", {"akbank": 3, "axess": 2, "wings": 1}),
    BankProfile("vakifbank", "Vakıfbank", {"vakifbank": 3, "vakiflar bankasi": 3, "vakif bank": 3}),
    BankProfile("qnb", "QNB Finansbank", {"qnb": 3, "finansbank": 3, "cardfinans": 2}),
    BankProfile("enpara", "Enpara.com", {"enpara.com": 3, "enpara": 3}),
]

ALL_BANK_PARSERS: List[Type[GenericBankParser]] = [_make_parser(p) for p in BANK_PROFILES]


def select_parser(header_text: str) -> Tuple[GenericBankParser, int]:
    """En yüksek parmak izi skorlu ayrıştırıcıyı seçer; eşitlik ya da eşleşmeme halinde geneli döner."""
    scored = sorted(((cls().fingerprint_score(header_text), i, cls) for i, cls in enumerate(ALL_BANK_PARSERS)), reverse=True)
    best_score, _, best_cls = scored[0]
    if best_score == 0 or (len(scored) > 1 and scored[1][0] == best_score):
        return GenericBankParser(), 0
    return best_cls(), best_score

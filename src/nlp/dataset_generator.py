"""
FinWise-AI: Türkçe POS İşlem Veri Seti Üreticisi (Synthetic POS Dataset)
========================================================================
Bu modül, POS sınıflandırıcısının eğitimi için gürültülü sentetik veri üretir.

Dürüstlük Notu:
Gerçek banka verisi KVKK ve banka sırrı nedeniyle kullanılamaz; bu veri seti
SENTETİKTİR. Sentetik veride rastgele train/test ayrımı yapıldığında aynı marka
şablonu her iki tarafa da düşer ve metrikler şişer (template leakage). Bu yüzden:
1. Her satır bir `group` anahtarı taşır (marka ya da esnaf adı). Değerlendirme
   GroupShuffleSplit ile yapılır: test markaları eğitimde HİÇ görülmez.
2. Nihai başarı, eğitimde hiç kullanılmayan elle yazılmış altın set
   (`data/gold/pos_gold_set.csv`) üzerinde raporlanır.

Üç örnek kaynağı vardır:
- brand   : Bilinen zincir markalar ("MIGROS", "SHELL") + POS gürültüsü.
- generic : Bağımsız esnaf = rastgele ad + kategori anahtar kelimesi ("OZKAN ECZANESI").
            Modelin marka ezberi yerine anahtar kelimeden genellemesini sağlar.
- other   : Kategori sinyali taşımayan işlemler (havale, inşaat, anlamsız kodlar).
            Modele "bilmiyorum" demeyi öğretir -> "Diğer / Belirsiz".
"""

import logging
import random
import string
from typing import Dict, List

import pandas as pd

from .preprocessor import TurkishFinancialNLPPreprocessor

logger = logging.getLogger(__name__)

OTHER_CATEGORY = "Diğer / Belirsiz"

MERCHANT_DOMAINS: Dict[str, List[str]] = {
    "Market / Bakkal": [
        "MIGROS", "MIGROS JET", "BIM", "A101", "SOK MARKET", "CARREFOURSA",
        "FILE MARKET", "MACROCENTER", "HAPPY CENTER", "KIM MARKET", "ONUR MARKET",
        "CAGRI MARKET", "NAMLI HYPERMARKET", "TARIM KREDI KOOP", "PEHLIVANOGLU",
        "GETIR", "ISTEGELSIN", "METRO GROSMARKET", "HAKMAR", "MOPAS",
    ],
    "Restoran / Yeme-İçme": [
        "YEMEKSEPETI", "GETIR YEMEK", "TRENDYOL YEMEK", "STARBUCKS", "KAHVE DUNYASI",
        "ESPRESSOLAB", "BIG CHEFS", "HAPPY MOONS", "MIDPOINT", "COOKSHOP",
        "BURGER KING", "MC DONALDS", "KFC", "POPEYES", "DOMINOS PIZZA",
        "NUSR-ET", "GUNAYDIN KEBAP", "HD ISKENDER", "TAVUK DUNYASI", "KOMAGENE",
        "SIMIT SARAYI", "ARBYS", "LITTLE CAESARS", "BAYDONER", "MADO",
    ],
    "Akaryakıt / Ulaşım": [
        "SHELL", "PETROL OFISI", "OPET", "BP", "TOTAL ENERGIES",
        "AYGAZ", "LUKOIL", "TURKISH AIRLINES", "THY", "PEGASUS",
        "AJET", "TCDD TASIMACILIK", "UBER", "BITAKSI", "ISTANBULKART",
        "IZMIRIM KART", "MARTI", "BINBIN", "TAV HAVALIMANLARI", "ISPARK", "KAMIL KOC", "METRO TURIZM",
    ],
    "Giyim / Aksesuar": [
        "ZARA", "MANGO", "H&M", "BERSHKA", "PULL&BEAR", "MASSIMO DUTTI",
        "BOYNER", "BEYMEN", "VAKKO", "LC WAIKIKI", "DEFACTO", "KOTON",
        "MAVI JEANS", "COLINS", "SARAR", "KIP", "ALTINYILDIZ", "DERIMOD",
        "DESA", "FLO", "INCI DERI", "SKECHERS", "NIKE", "ADIDAS", "PENTI", "IPEKYOL",
    ],
    "Elektronik / Teknoloji": [
        "MEDIAMARKT", "TEKNOSA", "VATAN BILGISAYAR", "APPLE STORE", "SAMSUNG",
        "ITOPYA", "INCEHESAP", "MONSTER NOTEBOOK", "CASPER", "MI STORE",
        "GURGENCLER", "TROY", "ASUS", "LENOVO", "DYSON",
    ],
    "Dijital Servis / Abonelik": [
        "NETFLIX", "SPOTIFY", "YOUTUBE PREMIUM", "APPLE.COM/BILL", "ICLOUD",
        "AMAZON PRIME", "DISNEY PLUS", "BLUTV", "EXXEN", "BEIN CONNECT",
        "OPENAI CHATGPT", "GOOGLE STORAGE", "MICROSOFT 365", "PLAYSTATION NETWORK",
        "STEAM GAMES", "DISCORD NITRO", "GOOGLE PLAY", "TOD TV", "GAIN",
    ],
    "Sağlık / Kozmetik": [
        "GRATIS", "WATSONS", "ROSSMANN", "SEPHORA", "EVE SHOP",
        "ACIBADEM", "MEDICANA", "MEMORIAL", "DUNYAGOZ", "LIV HOSPITAL", "MEDIPOL",
        "FLORENCE NIGHTINGALE", "YVES ROCHER", "MAC COSMETICS",
    ],
    "Ev / Yaşam / Fatura": [
        "IKEA", "KOCTAS", "BAUHAUS", "ENZA HOME", "YATAS", "BEKO",
        "ARCELIK", "BOSCH", "KARACA", "MADAME COCO", "ENGLISH HOME",
        "IGDAS", "ISKI", "CK BOGAZICI ELEKTRIK", "TURK TELEKOM",
        "TURKCELL", "VODAFONE", "ENERJISA", "SUPERONLINE", "TEKZEN",
    ],
    "Eğlence / Hobi / Kitap": [
        "D&R", "REMZI KITABEVI", "KABALCI", "CINEMAXIMUM",
        "PARIBU CINEVERSE", "ZORLU PSM", "PASSO", "BILETIX", "BILETINO",
        "BUBBLE SOCCER", "BOWLING ARENA", "IDEFIX", "KITAPYURDU", "TOYZZ SHOP", "MAC FIT",
    ],
    "Finans / Vergi / Masraf": [
        "GECIKME FAIZI", "AKDI FAIZ", "KKDF", "BSMV",
        "YILLIK UYELIK UCRETI", "KART AIDATI", "NAKIT CEKIM KOMISYONU",
        "HAVALE EFT UCRETI", "GELIR IDARESI BASKANLIGI", "VERGI DAIRESI",
        "NAKIT AVANS FAIZI", "HESAP ISLETIM UCRETI", "MTV ODEMESI", "SGK PRIM ODEMESI",
    ],
    "E-Ticaret / Pazaryeri": [
        "TRENDYOL", "HEPSIBURADA", "AMAZON", "N11", "CICEKSEPETI", "TEMU",
        "ALIEXPRESS", "PAZARAMA", "PTTAVM", "SAHIBINDEN", "DOLAP", "LETGO", "MORHIPO",
    ],
}

# Bağımsız esnafın kategori sinyalini taşıyan anahtar kelimeler
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Market / Bakkal": ["MARKET", "SUPERMARKET", "GIDA", "BAKKAL", "SARKUTERI", "MANAV", "KASAP", "KURUYEMIS", "TEKEL"],
    "Restoran / Yeme-İçme": ["RESTORAN", "LOKANTA", "KAFE", "CAFE", "KAHVE", "PASTANE", "FIRIN", "DONER", "PIZZA", "KEBAP", "BURGER", "BUFE", "BALIK"],
    "Akaryakıt / Ulaşım": ["PETROL", "AKARYAKIT", "OTOGAZ", "TAKSI", "OTOPARK", "TURIZM SEYAHAT", "HAVAYOLLARI", "OTOYOL", "RENT A CAR"],
    "Giyim / Aksesuar": ["GIYIM", "TEKSTIL", "BUTIK", "AYAKKABI", "MODA", "OUTLET", "KONFEKSIYON", "CANTA", "KUYUMCU"],
    "Elektronik / Teknoloji": ["ELEKTRONIK", "BILGISAYAR", "TEKNOLOJI", "BILISIM", "TELEFON AKSESUAR", "GSM"],
    "Dijital Servis / Abonelik": ["SUBSCRIPTION", "ABONELIK", "PREMIUM UYELIK", "DIGITAL", "STREAMING"],
    "Sağlık / Kozmetik": ["ECZANE", "ECZANESI", "HASTANE", "HASTANESI", "KLINIK", "DIS POLIKLINIGI", "OPTIK", "KOZMETIK", "KUAFOR", "BERBER", "GUZELLIK SALONU", "MEDIKAL"],
    "Ev / Yaşam / Fatura": ["MOBILYA", "YAPI MARKET", "NALBUR", "FATURA", "ELEKTRIK", "DOGALGAZ", "SU IDARESI", "EV TEKSTILI", "ZUCCACIYE", "HIRDAVAT", "AIDAT"],
    "Eğlence / Hobi / Kitap": ["KITABEVI", "KIRTASIYE", "SINEMA", "TIYATRO", "OYUNCAK", "SPOR SALONU", "HOBI", "MUZIK", "ETKINLIK BILET"],
    "Finans / Vergi / Masraf": ["FAIZ", "VERGI", "KOMISYON", "MASRAF", "UCRETI", "SIGORTA PRIMI"],
    "E-Ticaret / Pazaryeri": ["PAZARYERI", "E-TICARET", "ONLINE ALISVERIS"],
}

# Kategori sinyali OLMAYAN iş kolları ve işlem türleri -> "Diğer / Belirsiz"
OTHER_KEYWORDS = [
    "INSAAT", "TAAHHUT", "NAKLIYAT", "EMLAK", "HUKUK BUROSU", "MUHASEBE", "NOTER",
    "DERNEGI", "VAKFI BAGIS", "DANISMANLIK", "MUHENDISLIK", "REKLAM AJANSI", "MATBAA",
    "OTO YIKAMA", "CICEKCILIK", "VETERINER", "KURS", "APARTMAN YONETIMI",
]
TRANSFER_PREFIXES = ["HAVALE", "EFT", "FAST", "PARA TRANSFERI", "GIDEN HAVALE", "KARTTAN KARTA TRANSFER"]

FIRST_NAMES = ["AHMET", "MEHMET", "AYSE", "FATMA", "MUSTAFA", "EMRE", "ZEYNEP", "ELIF", "CAN", "DENIZ", "HASAN", "HUSEYIN", "MERVE", "BURAK", "SELIN", "OGUZ", "TOLGA", "EBRU", "KEREM", "GIZEM"]
SURNAMES = ["YILMAZ", "KAYA", "DEMIR", "CELIK", "SAHIN", "OZKAN", "ARSLAN", "DOGAN", "KILIC", "ASLAN", "CETIN", "KARA", "KOC", "KURT", "OZDEMIR", "AYDIN", "OZTURK", "POLAT", "ERDOGAN", "GUNES", "AKSOY", "TEKIN", "BULUT", "YAVUZ", "UYSAL", "TUNC", "ERGIN", "SEZER", "BAYRAK", "ALTUN"]

POS_PREFIXES = ["", "", "", "", "IYZICO/", "PAYTR*", "BD*", "SBM*", "PARAM*", "PARAM/", "SIPAY*"]
CITY_SUFFIXES = ["IST", "ISTANBUL", "ANKARA", "IZMIR", "BURSA", "ANTALYA", "KADIKOY", "SISLI", "BESIKTAS", "TR", "AVM", "ISTINYE PARK", "ZORLU", "KANYON", "CEVAHIR", "AKASYA", "ADANA", "ESKISEHIR", "ATASEHIR", "MASLAK"]
LEGAL_SUFFIXES = ["TIC A.S.", "TIC. LTD. STI.", "A.S.", "LTD", "MAGAZACILIK", "PERAKENDE", "PAZARLAMA", "SUBESI", "SAN. VE TIC."]

_VOWELS = set("AEIOU")
POS_FIELD_LIMIT = 25  # Birçok POS / ekstre satırı işyeri adını ~22-25 karaktere kırpar


def _abbreviate(word: str, rng: random.Random) -> str:
    """Sesli harf düşürme: MIGROS -> MGRS (ilk harf korunur)."""
    if len(word) < 5:
        return word
    return word[0] + "".join(ch for ch in word[1:] if ch not in _VOWELS or rng.random() < 0.15)


def _typo(text: str, rng: random.Random) -> str:
    """Tek karakter silme ya da komşu karakter değiş tokuşu."""
    if len(text) < 6:
        return text
    i = rng.randint(1, len(text) - 2)
    if rng.random() < 0.5:
        return text[:i] + text[i + 1:]
    return text[:i] + text[i + 1] + text[i] + text[i + 2:]


def _pseudo_word(rng: random.Random) -> str:
    """Anlamsız ama telaffuz edilebilir sahte marka: 'VELTRAN', 'KOMIRA'."""
    consonants, vowels = "BCDFGHKLMNPRSTVYZ", "AEIOU"
    n = rng.randint(2, 4)
    return "".join(rng.choice(consonants) + rng.choice(vowels) for _ in range(n)) + rng.choice(["", "N", "R", "S", "X"])


def _decorate(core: str, rng: random.Random, noise_level: float = 1.0) -> str:
    """Çekirdek işyeri adına POS gürültüsü ekler."""
    text = core
    if rng.random() < 0.20 * noise_level:
        words = text.split()
        k = rng.randrange(len(words))
        words[k] = _abbreviate(words[k], rng)
        text = " ".join(words)
    if rng.random() < 0.08 * noise_level:
        text = _typo(text, rng)
    if rng.random() < 0.06 * noise_level:
        text = text.replace(" ", "")

    prefix = rng.choice(POS_PREFIXES)
    legal = rng.choice(LEGAL_SUFFIXES) if rng.random() < 0.35 else ""
    city = rng.choice(CITY_SUFFIXES) if rng.random() < 0.6 else ""
    branch = f"{rng.randint(1, 9999):0{rng.choice([3, 4])}d}" if rng.random() < 0.45 else ""
    parts = [p for p in [prefix + text, legal, city, branch] if p]
    if rng.random() < 0.15:
        rng.shuffle(parts)
    out = " ".join(parts)

    if rng.random() < 0.25 * noise_level:
        out = out[:POS_FIELD_LIMIT].rstrip()
    return out


def _brand_sample(category: str, rng: random.Random) -> Dict[str, str]:
    brand = rng.choice(MERCHANT_DOMAINS[category])
    core = brand
    if rng.random() < 0.25 and category in CATEGORY_KEYWORDS:
        core = f"{brand} {rng.choice(CATEGORY_KEYWORDS[category])}"
    return {"raw_text": _decorate(core, rng), "category": category, "group": f"brand:{brand}", "source": "brand"}


def _generic_sample(category: str, rng: random.Random) -> Dict[str, str]:
    name = rng.choice(SURNAMES) if rng.random() < 0.7 else _pseudo_word(rng)
    keyword = rng.choice(CATEGORY_KEYWORDS[category])
    core = f"{name} {keyword}" if rng.random() < 0.8 else f"{keyword} {name}"
    return {"raw_text": _decorate(core, rng, noise_level=0.5), "category": category, "group": f"generic:{name}", "source": "generic"}


def _other_sample(rng: random.Random) -> Dict[str, str]:
    roll = rng.random()
    if roll < 0.35:
        person = f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}"
        core, group = f"{rng.choice(TRANSFER_PREFIXES)} {person}", f"other:{person}"
        raw = core if rng.random() < 0.7 else f"{core} {rng.randint(100000, 999999)}"
    elif roll < 0.70:
        name = rng.choice(SURNAMES) if rng.random() < 0.6 else _pseudo_word(rng)
        raw, group = _decorate(f"{name} {rng.choice(OTHER_KEYWORDS)}", rng, noise_level=0.5), f"other:{name}"
    elif roll < 0.92:
        name = " ".join(_pseudo_word(rng) for _ in range(rng.randint(1, 2)))
        raw, group = _decorate(name, rng, noise_level=0.3), f"other:{name}"
    else:
        code = "".join(rng.choices(string.ascii_uppercase + string.digits, k=rng.randint(4, 12)))
        raw, group = code, f"other:{code}"
    return {"raw_text": raw, "category": OTHER_CATEGORY, "group": group, "source": "other"}


def generate_pos_dataset(num_samples: int = 18000, random_seed: int = 42) -> pd.DataFrame:
    """
    Sentetik POS eğitim verisi üretir.

    Returns:
        pd.DataFrame: ['raw_text', 'clean_text', 'category', 'group', 'source'].
        `group`, GroupShuffleSplit için sızıntı önleyici anahtardır.
    """
    rng = random.Random(random_seed)  # global random durumuna dokunmaz
    categories = list(MERCHANT_DOMAINS.keys())
    per_class = num_samples // (len(categories) + 1)
    records: List[Dict[str, str]] = []

    for category in categories:
        for _ in range(per_class):
            use_generic = category in CATEGORY_KEYWORDS and rng.random() < 0.40
            records.append(_generic_sample(category, rng) if use_generic else _brand_sample(category, rng))
    for _ in range(per_class):
        records.append(_other_sample(rng))

    df = pd.DataFrame(records)
    df["clean_text"] = df["raw_text"].map(TurkishFinancialNLPPreprocessor.clean_pos_text)
    df = df[df["clean_text"].str.len() > 0]
    df = df.sample(frac=1.0, random_state=random_seed).reset_index(drop=True)
    return df[["raw_text", "clean_text", "category", "group", "source"]]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dataset = generate_pos_dataset()
    logger.info("Üretilen veri seti: %s | benzersiz clean_text: %d", dataset.shape, dataset["clean_text"].nunique())
    logger.info("Kategori dağılımı:\n%s", dataset["category"].value_counts())
    dataset.to_csv("data/synthetic_pos_dataset.csv", index=False)

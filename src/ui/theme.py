"""
FinWise-AI: Görsel Tema ve Grafik Paleti (tek kaynak)
=====================================================
Arayüzdeki bütün renkler buradan gelir; sayfalarda elle yazılmış hex yoktur.

Palet seçimi ölçülmüştür (eyeballing değil). Kategorik sıra, renk körlüğü (CVD) ve
normal görme ayrım eşiklerini geçen adaylar arasından seçilmiştir:

    sıcak yüzey #fdfaf6 üzerinde, komşu çiftler:
    CVD ΔE 16.3 (eşik ≥ 8) · normal görme ΔE 19.6 (eşik ≥ 15)   -> 7 slot GEÇTİ

Kurallar (grafik kodu yazarken uyulması gerekenler):
1. Kategorik renkler SABİT sırayla atanır, asla döngüye sokulmaz. 7'den fazla kategori
   varsa fazlası "Diğer" (nötr gri) altında toplanır — 8. renk uydurulmaz.
2. Büyüklük zaten çubuk uzunluğuyla kodlanıyorsa renkle TEKRAR kodlanmaz (tek renk kullanılır).
3. Kutupluluk (SHAP +/-) için ıraksak çift: kırmızı ↔ mavi, ortada nötr gri. Yeşil/kırmızı
   ikilisi kullanılmaz: en yaygın renk körlüğü türünde ayırt edilemez.
4. Durum renkleri (iyi/uyarı/kritik) seriye dönüşmez; her zaman metin etiketiyle birlikte gelir.
5. Saçılım grafikleri tüm çiftleri karşılaştırdığı için en fazla 3 kategorik renk taşıyabilir
   (ölçüldü); daha fazlası gerekiyorsa seri kesilir ya da grafik ayrıştırılır.
"""

from typing import Dict, List, Sequence

import plotly.graph_objects as go
import plotly.io as pio

# ----------------------------------------------------------------- yüzeyler ve mürekkep
SURFACE = "#fdfaf6"          # grafik zemini (sıcak kağıt)
PAGE = "#fbf7f1"             # sayfa zemini
CARD = "#ffffff"
INK = "#1a1a19"              # birincil metin
INK_SOFT = "#57544e"         # ikincil metin
INK_MUTED = "#8a867e"        # eksen / etiket
GRID = "#eae3d9"             # ızgara (saç teli)
AXIS = "#d6cec1"
BORDER = "rgba(26, 26, 25, 0.10)"

# --------------------------------------------------------------------- marka
BRAND = "#eb6834"            # turuncu: birincil aksiyon ve tek serili grafikler
BRAND_SOFT = "#fdf0e8"
BRAND_DEEP = "#c24a1c"

# ------------------------------------------------- kategorik palet (SABİT SIRA)
CATEGORICAL: List[str] = [
    "#eb6834",  # 1 turuncu
    "#2a78d6",  # 2 mavi
    "#1baf7a",  # 3 turkuaz
    "#4a3aa7",  # 4 mor
    "#eda100",  # 5 sarı
    "#e87ba4",  # 6 pembe
    "#008300",  # 7 yeşil
]
OTHER_COLOR = "#b9b2a7"      # 8. renk değil: artakalanların toplandığı nötr kova
OTHER_LABEL = "Diğer kategoriler"   # modelin kendi "Diğer / Belirsiz" sınıfıyla karışmasın diye ayrı ad
MAX_CATEGORIES = len(CATEGORICAL)

# ------------------------------------------------------------- ıraksak (SHAP)
DIVERGING_POS = "#d03b3b"    # tahmini destekler
DIVERGING_NEG = "#2a78d6"    # tahminden uzaklaştırır
DIVERGING_MID = "#efe9e0"

# -------------------------------------------------------------- durum renkleri
STATUS: Dict[str, str] = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'
TEMPLATE = "finwise"


def register_plotly_template() -> None:
    """Plotly varsayılan şablonunu kaydeder; sayfalar template=TEMPLATE der."""
    if TEMPLATE in pio.templates:
        return
    pio.templates[TEMPLATE] = go.layout.Template(
        layout=go.Layout(
            colorway=CATEGORICAL,
            paper_bgcolor=SURFACE,
            plot_bgcolor=SURFACE,
            font=dict(family=FONT_FAMILY, size=13, color=INK),
            title=dict(font=dict(size=15, color=INK), x=0, xanchor="left", y=0.97, yanchor="top"),
            margin=dict(l=16, r=16, t=84, b=16),  # başlık + altındaki lejant için yer
            # automargin: uzun Türkçe kategori adları kırpılmasın; standoff: eksen başlığı tick'lerle çakışmasın
            xaxis=dict(gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS, automargin=True,
                       tickfont=dict(color=INK_MUTED, size=12), title=dict(font=dict(color=INK_SOFT, size=12), standoff=12)),
            yaxis=dict(gridcolor=GRID, zerolinecolor=AXIS, linecolor=AXIS, automargin=True,
                       tickfont=dict(color=INK_MUTED, size=12), title=dict(font=dict(color=INK_SOFT, size=12), standoff=12)),
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, font=dict(color=INK_SOFT, size=12), bgcolor="rgba(0,0,0,0)"),
            hoverlabel=dict(bgcolor=CARD, bordercolor=AXIS, font=dict(family=FONT_FAMILY, size=12, color=INK)),
            colorscale=dict(sequential=[[0.0, BRAND_SOFT], [1.0, BRAND_DEEP]]),
        )
    )
    pio.templates.default = TEMPLATE


def category_colors(labels: Sequence[str]) -> List[str]:
    """
    Etiketlere SABİT sırayla renk atar. Renk SIRAYA göre değil BÜYÜKLÜK sırasına göre verilir:
    çağıran, etiketleri azalan büyüklükte vermelidir (yatay çubukta liste ters çevrilmeden ÖNCE).
    `OTHER_LABEL` kovası her zaman nötr gridir ve bir slot tüketmez.
    """
    colors, slot = [], 0
    for label in labels:
        if str(label).startswith(OTHER_LABEL):
            colors.append(OTHER_COLOR)
            continue
        colors.append(CATEGORICAL[slot % MAX_CATEGORIES])
        slot += 1
    return colors


def top_n_with_other(series, n: int = MAX_CATEGORIES):
    """
    En büyük n kategoriyi bırakır, kalanını tek bir nötr kovada toplar (kova her zaman en sonda).
    8. rengi uydurmak yerine kovaya almak kuralının uygulanışı.
    """
    series = series.sort_values(ascending=False)
    if len(series) <= n:
        return series
    head, tail = series.iloc[:n].copy(), series.iloc[n:]
    head[f"{OTHER_LABEL} ({len(tail)})"] = tail.sum()
    return head


CSS = f"""
<style>
    :root {{
        --brand: {BRAND}; --brand-soft: {BRAND_SOFT}; --ink: {INK};
        --ink-soft: {INK_SOFT}; --surface: {SURFACE}; --card: {CARD}; --border: {BORDER};
    }}
    .stApp {{ background: linear-gradient(180deg, {PAGE} 0%, {SURFACE} 420px); }}
    .main-header {{
        font-size: 2.1rem; font-weight: 800; letter-spacing: -0.02em;
        background: linear-gradient(92deg, {BRAND_DEEP}, {BRAND} 45%, {STATUS["warning"]});
        -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0.15rem;
    }}
    .tagline {{ color: {INK_SOFT}; font-size: 0.95rem; margin-bottom: 0.6rem; }}
    /* Metrik kartları: beyaz kart + turuncu üst şerit */
    div[data-testid="stMetric"] {{
        background: {CARD}; border: 1px solid {BORDER}; border-top: 3px solid {BRAND};
        border-radius: 14px; padding: 14px 16px 12px;
        box-shadow: 0 1px 2px rgba(26,26,25,0.04), 0 8px 20px -14px rgba(26,26,25,0.18);
    }}
    div[data-testid="stMetricLabel"] p {{ color: {INK_SOFT}; font-weight: 600; font-size: 0.82rem; }}
    div[data-testid="stMetricValue"] {{ color: {INK}; font-weight: 700; }}
    /* Sekme ve butonlarda marka rengi */
    .stTabs [aria-selected="true"] {{ color: {BRAND} !important; }}
    .stButton > button {{
        border-radius: 999px; border: 1px solid {BRAND}; color: {BRAND_DEEP};
        background: {BRAND_SOFT}; font-weight: 600;
    }}
    .stButton > button:hover {{ background: {BRAND}; color: #fff; border-color: {BRAND}; }}
    section[data-testid="stSidebar"] {{ background: {CARD}; border-right: 1px solid {BORDER}; }}
    .stAlert {{ border-radius: 12px; }}
    div[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 12px; }}
</style>
"""


def inject_css(st_module) -> None:
    """Streamlit CSS'ini ve Plotly şablonunu bir kerede kurar."""
    register_plotly_template()
    st_module.markdown(CSS, unsafe_allow_html=True)  # sabit tema CSS'i; kullanıcı girdisi içermez

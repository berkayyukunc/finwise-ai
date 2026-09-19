"""
FinWise-AI: Finansal Belge Zekası ve Tahminleme Platformu
=========================================================
Ana Uygulama & Ekstre Yükleme Merkezi (Dashboard)
"""

import os

import pandas as pd
import plotly.express as px
import streamlit as st

from src.clustering.archetypes import SpendingArchetypeClusterer
from src.utils.statement_loader import load_classifier, process_statements

st.set_page_config(
    page_title="FinWise-AI | Finansal Belge Zekası",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Özel CSS & Tasarım
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(90deg, #3b82f6, #8b5cf6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .metric-card {
        background-color: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .stAlert {
        border-radius: 10px;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def load_ml_pipeline():
    """NLP sınıflandırıcı ve kümeleme modellerini önbelleğe alır."""
    return load_classifier(), SpendingArchetypeClusterer()


nlp_pipeline, clusterer = load_ml_pipeline()

# Yan Menü (Sidebar)
with st.sidebar:
    st.markdown("### 🧠 FinWise-AI")
    st.caption("Uçtan Uca Finansal Belge Zekası & ML Platformu")
    st.divider()

    st.markdown("#### 📂 Ekstre Kaynağı Seçin")
    source_choice = st.radio(
        "Veri Girişi:",
        ["Örnek Hazır Ekstre (0 TL / Hızlı Test)", "Kendi PDF Ekstreni Yükle"],
        index=0
    )

    selected_sample = None
    uploaded_file = None
    pdf_password = None
    if source_choice == "Örnek Hazır Ekstre (0 TL / Hızlı Test)":
        selected_sample = st.selectbox(
            "Hazır Banka Ekstresi Seç:",
            [
                "Garanti BBVA (Taksitli & İadeli)",
                "Türkiye İş Bankası (Maksimum Kart)",
                "Ziraat Bankası (Bankkart)",
                "Yapı Kredi (Worldcard)",
                "Akbank (Axess)",
                "Vakıfbank (World)",
                "QNB Finansbank (CardFinans)",
                "Enpara.com (Dijital Banka)"
            ]
        )
    else:
        uploaded_file = st.file_uploader(
            "PDF ekstre(ler)inizi yükleyin:",
            type=["pdf"],
            accept_multiple_files=True,
            help="Birden çok ayın ekstresini birlikte seçebilirsiniz; geçmiş uzadıkça tahmin ve anomali analizi güçlenir. "
                 "Dosyalar yalnızca bu oturumun belleğinde işlenir; diske yazılmaz."
        )
        pdf_password = st.text_input("PDF şifresi (varsa):", type="password", help="Bankalar e-ekstreyi çoğunlukla şifreli gönderir.")

    st.divider()
    st.markdown("#### 🛡️ Gizlilik & KVKK")
    st.info("PII (ad, adres, TC, kart, IBAN, telefon) tüm sayfalarda tespit edilip PDF içerik akışından silinir; ham PDF saklanmaz.")
    st.warning("Bu bir **demo**dur. Herkese açık bir bulut kurulumuna gerçek ekstre yüklemek KVKK md. 9 kapsamında yurt dışına veri aktarımı "
               "sayılabilir. Gerçek ekstreyi yalnızca **kendi bilgisayarınızda** (`streamlit run app.py`) işleyin.")


# Ana İçerik
st.markdown("<div class='main-header'>FinWise-AI: Finansal Belge Zekası & Tahminleme Motoru</div>", unsafe_allow_html=True)
st.caption("Document AI + POS sınıflandırma + bileşen tabanlı tahmin + XAI (SHAP) + kümeleme · tüm örnek ekstreler sentetiktir")

# Veri Yükleme ve İşleme
pdf_files = []
if source_choice == "Örnek Hazır Ekstre (0 TL / Hızlı Test)":
    file_map = {
        "Garanti BBVA (Taksitli & İadeli)": "data/statements/sample_garanti.pdf",
        "Türkiye İş Bankası (Maksimum Kart)": "data/statements/sample_isbank.pdf",
        "Ziraat Bankası (Bankkart)": "data/statements/sample_ziraat.pdf",
        "Yapı Kredi (Worldcard)": "data/statements/sample_yapikredi.pdf",
        "Akbank (Axess)": "data/statements/sample_akbank.pdf",
        "Vakıfbank (World)": "data/statements/sample_vakifbank.pdf",
        "QNB Finansbank (CardFinans)": "data/statements/sample_qnb.pdf",
        "Enpara.com (Dijital Banka)": "data/statements/sample_enpara.pdf"
    }
    path = file_map[selected_sample]
    if os.path.exists(path):
        with open(path, "rb") as f:
            pdf_files = [(os.path.basename(path), f.read())]

elif uploaded_file:
    pdf_files = [(f.name, f.read()) for f in uploaded_file]

if not pdf_files:
    st.warning("Lütfen sol taraftan bir örnek ekstre seçin veya kendi PDF ekstrenizi yükleyin.")
    st.stop()

# 1-3. Document AI -> NLP kategori -> anomali (tek giriş noktası; birden çok ekstre birleştirilir)
with st.spinner(f"{len(pdf_files)} ekstre güvenlik denetiminden geçiriliyor, ayrıştırılıyor ve PII karartılıyor..."):
    statements, df, errors = process_statements(pdf_files, classifier=nlp_pipeline, password=pdf_password or None)

for err in errors:
    st.error(f"⛔ {err}")
if not statements:
    st.stop()

stmt = statements[-1]  # borç / asgari kartları en güncel ekstreyi gösterir
st.session_state.update({
    "parsed_statement": stmt, "transactions_df": df, "bank_name": stmt.bank_name,
    "total_debt": stmt.total_debt, "min_payment": stmt.min_payment,
})
for st_item in statements:
    for w in st_item.warnings:
        st.warning(f"**{st_item.bank_name} · {st_item.period_end or '?'}:** {w}")

if len(statements) > 1:
    st.markdown(f"### 🗂️ Yüklenen {len(statements)} Ekstre")
    label = {True: "✓ tuttu", False: "✗ TUTMADI", None: "— yapılamadı"}
    st.dataframe(pd.DataFrame([{
        "Banka": x.bank_name, "Dönem Sonu": x.period_end, "İşlem": len(x.transactions), "Devreden": x.previous_balance,
        "Dönem Borcu": x.total_debt, "Asgari": x.min_payment, "Sağlama": label[x.checksum_valid],
    } for x in statements]).style.format({"Devreden": "{:,.2f}", "Dönem Borcu": "{:,.2f}", "Asgari": "{:,.2f}"}, na_rep="—"),
        use_container_width=True, hide_index=True)
    st.caption("Aşağıdaki analizler tüm ekstrelerin birleşimi üzerindedir; borç ve asgari kartları en güncel ekstreye aittir.")

# KPI Kartları (Bento Grid)
st.markdown("### 📊 Finansal Özet Kartları")
c1, c2, c3, c4 = st.columns(4)
positive_df = df[df["amount"] > 0] if "amount" in df.columns else pd.DataFrame()
total_spend = positive_df["amount"].sum() if len(positive_df) > 0 else 0.0
anomaly_count = int(df["is_anomaly"].sum()) if "is_anomaly" in df.columns else 0
top_cat = positive_df.groupby("category")["amount"].sum().idxmax() if len(positive_df) > 0 and "category" in positive_df.columns else "N/A"

with c1:
    checksum_label = {True: "Sağlama tuttu ✓", False: "Sağlama TUTMADI", None: "Sağlama yapılamadı"}[stmt.checksum_valid]
    st.metric("Tespit Edilen Banka", stmt.bank_name, delta=checksum_label, delta_color="normal" if stmt.checksum_valid else "inverse")
with c2:
    st.metric("Toplam Harcama", f"{total_spend:,.2f} TL", delta=f"{len(df)} İşlem")
with c3:
    st.metric("Dönem Borcu", f"{stmt.total_debt:,.2f} TL", delta=f"Asgari: {stmt.min_payment:,.2f} TL" if stmt.min_payment is not None else "Asgari: okunamadı", delta_color="off")
with c4:
    st.metric("Tespit Edilen Anomali", f"{anomaly_count} İşlem", delta="İnceleme Gerekli" if anomaly_count > 0 else "Temiz", delta_color="inverse")

st.divider()

# Grafikler (İki Sütun)
col_left, col_right = st.columns(2)

with col_left:
    st.markdown("#### 📅 Günlük Harcama Hızı (Burn Rate)")
    df_daily = positive_df.groupby("date")["amount"].sum().reset_index() if len(positive_df) > 0 and "date" in positive_df.columns else pd.DataFrame(columns=["date", "amount"])
    fig_bar = px.bar(
        df_daily,
        x="date",
        y="amount",
        title="Günlük Harcama Dağılımı (TL)",
        color="amount",
        color_continuous_scale="Blues"
    )
    fig_bar.update_layout(template="plotly_dark", height=340, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig_bar, use_container_width=True)

with col_right:
    st.markdown("#### 🍩 Kategori Bazlı Dağılım")
    df_cat = positive_df.groupby("category")["amount"].sum().reset_index() if len(positive_df) > 0 and "category" in positive_df.columns else pd.DataFrame(columns=["category", "amount"])
    fig_donut = px.pie(
        df_cat,
        names="category",
        values="amount",
        hole=0.45,
        title="Harcama Kategorileri"
    )
    fig_donut.update_layout(template="plotly_dark", height=340, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig_donut, use_container_width=True)

# İşlemler Tablosu
st.markdown("### 📋 Ayrıştırılmış ve Sınıflandırılmış İşlemler")
tab1, tab2 = st.tabs(["Tüm İşlemler", "🚨 Yakalanan Anomaliler"])

all_possible_cols = [
    "date", "clean_description", "amount", "category",
    "confidence", "installment_no", "transaction_type", "is_anomaly"
]
display_cols = [c for c in all_possible_cols if c in df.columns]

with tab1:
    st.dataframe(
        df[display_cols].style.format({c: "{:,.2f} TL" for c in ["amount"] if c in display_cols}),
        use_container_width=True,
        height=320
    )

with tab2:
    if "is_anomaly" in df.columns:
        anom_df = df[df["is_anomaly"]]
        if len(anom_df) > 0:
            anom_cols = [c for c in ["date", "clean_description", "amount", "category", "anomaly_type", "anomaly_reason"] if c in anom_df.columns]
            st.dataframe(
                anom_df[anom_cols].style.format({c: "{:,.2f} TL" for c in ["amount"] if c in anom_cols}),
                use_container_width=True
            )
        else:
            st.success("Tebrikler! Bu ekstrede olağandışı bir harcama anomalisi tespit edilmedi.")
    else:
        st.info("Anomali taraması tamamlandı.")


st.info("💡 **İpucu:** Sol menüden **XAI & NLP Arena**, **Harcama Galaksisi**, **Tahmin & Anomali** ve **FinWise Copilot** sayfalarına geçerek derinlemesine yapay zeka analizlerini inceleyebilirsiniz.")

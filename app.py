"""
FinWise-AI: Finansal Belge Zekası ve Tahminleme Platformu
=========================================================
Ana Uygulama & Ekstre Yükleme Merkezi (Dashboard)
"""

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.ui import theme
from src.utils.statement_loader import load_classifier, process_statements

st.set_page_config(
    page_title="FinWise-AI | Finansal Belge Zekası",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

theme.inject_css(st)


@st.cache_resource
def load_ml_pipeline():
    """POS sınıflandırıcısını önbelleğe alır (model dosyası yoksa None döner)."""
    return load_classifier()


nlp_pipeline = load_ml_pipeline()

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
st.markdown("<div class='main-header'>FinWise-AI</div>", unsafe_allow_html=True)
st.markdown("<div class='tagline'>Finansal Belge Zekası &amp; Tahminleme Motoru</div>", unsafe_allow_html=True)
st.caption("Document AI + POS sınıflandırma + bileşen tabanlı tahmin + anomali + XAI (SHAP) · tüm örnek ekstreler sentetiktir")

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
    st.markdown("#### 📅 Günlük Harcama Hızı")
    df_daily = positive_df.groupby("date")["amount"].sum().reset_index() if len(positive_df) > 0 and "date" in positive_df.columns else pd.DataFrame(columns=["date", "amount"])
    fig_bar = go.Figure(go.Bar(
        x=df_daily["date"], y=df_daily["amount"], marker_color=theme.BRAND, marker_line_width=0,
        hovertemplate="%{x}<br><b>%{y:,.2f} TL</b><extra></extra>",
    ))
    fig_bar.update_layout(title="Günlük toplam harcama (TL)", template=theme.TEMPLATE, height=340, bargap=0.25, yaxis_title=None, xaxis_title=None)
    st.plotly_chart(fig_bar, use_container_width=True)

with col_right:
    st.markdown("#### 🎨 Kategori Bazlı Dağılım")
    if len(positive_df) > 0 and "category" in positive_df.columns:
        cat_series = theme.top_n_with_other(positive_df.groupby("category")["amount"].sum())
        colors = theme.category_colors(cat_series.index)          # renk azalan büyüklüğe göre
        labels, values = list(cat_series.index)[::-1], list(cat_series.values)[::-1]
        fig_cat = go.Figure(go.Bar(
            x=values, y=labels, orientation="h", marker_color=colors[::-1], marker_line_width=0,
            text=[f"{v:,.0f} TL" for v in values], textposition="outside", cliponaxis=False,
            hovertemplate="%{y}<br><b>%{x:,.2f} TL</b><extra></extra>",
        ))
        fig_cat.update_layout(title="Kategori başına harcama", template=theme.TEMPLATE, height=360,
                              xaxis_title=None, yaxis_title=None, bargap=0.32, margin=dict(l=8, r=96, t=64, b=16))
        fig_cat.update_yaxes(automargin=True, ticksuffix="  ")
        fig_cat.update_xaxes(showticklabels=False, showgrid=False)
        st.plotly_chart(fig_cat, use_container_width=True)
    else:
        st.caption("Kategori dağılımı için harcama bulunamadı.")

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


st.info("💡 **İpucu:** Sol menüden **XAI & NLP Arena**, **Tahmin & Anomali**, **Yatırım & DCA** ve **FinWise Copilot** sayfalarına geçebilirsiniz.")

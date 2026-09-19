"""
FinWise-AI: Açıklanabilir Yapay Zeka (XAI) ve Model Arenası
============================================================
SHAP ile karar açıklaması + bu makinede ÖLÇÜLMÜŞ model karşılaştırması.
Bu sayfadaki hiçbir metrik elle yazılmamıştır; hepsi `train_nlp_model.py` ve
`scripts/benchmark_models.py` çıktılarından okunur.
"""

import json
import os
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.nlp.explainer import POSShapExplainer

st.set_page_config(page_title="XAI & NLP Arena | FinWise-AI", page_icon="🔬", layout="wide")
st.markdown("# 🔬 Açıklanabilir Yapay Zeka (XAI) & NLP Model Arenası")
st.caption("SHAP TreeExplainer ile toplanabilir (additive) karar açıklaması ve sızıntısız değerlendirme sonuçları")

METRICS_PATH, BENCHMARK_PATH = "data/models/pos_model_metrics.json", "data/models/benchmark_results.json"


@st.cache_resource
def load_explainer():
    return POSShapExplainer()


def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


explainer = load_explainer()

st.markdown("### 1. Canlı POS Açıklama Testi")
c_in1, c_in2 = st.columns([3, 1])
with c_in2:
    sample_select = st.selectbox("Hızlı Örnek:", [
        "MİGROS TİC. A.Ş. LEVENT İSTANBUL", "KOÇTAŞ YAPI MARKETLERİ", "MGRS TR GIDA IST 042", "IYZICO/ZARA TR 3342",
        "OZKAN ECZANESI KADIKOY", "AHMET YILMAZ HAVALE", "XQZW 12345",
    ])
with c_in1:
    user_input = st.text_input("Test Edilecek POS Metni:", value=sample_select)

if user_input:
    start = time.perf_counter()
    result = explainer.explain(user_input, top_k=8)
    latency_ms = (time.perf_counter() - start) * 1000

    m1, m2, m3 = st.columns(3)
    m1.metric("En Olası Kategori", result["predicted_class"])
    m2.metric("Güven Skoru", f"%{result['confidence'] * 100:.1f}")
    m3.metric("Açıklama Süresi (tahmin + SHAP)", f"{latency_ms:.1f} ms")
    if result["abstained"]:
        st.warning(f"Güven eşiğin (%{explainer.abstain_threshold * 100:.0f}) altında: üretimde bu işlem **'Diğer / Belirsiz'** olarak etiketlenir; model zorla kategori atamaz.")

    st.markdown("#### Kelime Bazlı SHAP Isı Haritası")
    st.caption(f"Modelin gördüğü temizlenmiş metin: `{result['clean_text']}` · Yeşil: sınıfı destekler, kırmızı: uzaklaştırır (log-odds).")
    st.markdown(
        f"<div style='background-color: #0f172a; padding: 20px; border-radius: 10px; border: 1px solid #334155; font-size: 1.25rem;'>{result['html_explanation']}</div>",
        unsafe_allow_html=True,  # html_explanation içindeki tüm kullanıcı metni html.escape'ten geçer
    )

    st.markdown("#### Toplanabilirlik Dökümü (log-odds)")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Taban değer", f"{result['base_value']:+.2f}")
    a2.metric("Metindeki n-gram'lar", f"{result['active_contribution']:+.2f}")
    a3.metric("Metinde OLMAYAN n-gram'lar", f"{result['absent_contribution']:+.2f}", help="Ağaç modellerinde bir n-gram'ın yokluğu da karar verdirir; bu katkı gizlenmez.")
    a4.metric("Toplam margin", f"{result['margin']:+.2f}")

    features = [f[0] for f in result["top_features"]][::-1]
    scores = [f[1] for f in result["top_features"]][::-1]
    fig = go.Figure(go.Bar(x=scores, y=[f"'{f}'" for f in features], orientation="h", marker_color=["#22c55e" if s > 0 else "#ef4444" for s in scores], text=[f"{s:+.3f}" for s in scores], textposition="outside"))
    fig.update_layout(title="En Etkili Aktif N-Gram'lar", xaxis_title="SHAP (log-odds)", template="plotly_dark", height=340, margin=dict(l=20, r=40, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

st.divider()
st.markdown("### 2. Değerlendirme: Üç Ayrı Soru, Üç Ayrı Metrik")
metrics = load_json(METRICS_PATH)
if metrics is None:
    st.info("Metrik dosyası yok. `PYTHONPATH=. python train_nlp_model.py` çalıştırın.")
else:
    m = metrics["metrics"]
    k1, k2, k3 = st.columns(3)
    k1.metric("Altın set macro-F1 (manşet)", f"{m['gold']['macro_f1']:.3f}", delta=f"n={m['gold']['n']} elle yazılmış satır", delta_color="off")
    k2.metric("Görülmemiş işyeri macro-F1", f"{m['unseen_merchant']['macro_f1']:.3f}", delta="GroupShuffleSplit (sızıntısız)", delta_color="off")
    k3.metric("Görülmüş marka macro-F1", f"{m['seen_merchant']['macro_f1']:.3f}", delta="iyimser üst sınır", delta_color="off")
    by_src = m["unseen_merchant"].get("by_source", {})
    st.caption(
        f"Eğitimden {m['duplicates_removed']} kopya satır silindi. Görülmemiş işyeri kırılımı — "
        + " · ".join(f"{k}: %{v['accuracy'] * 100:.1f}" for k, v in by_src.items())
        + ". 'brand' düşüktür çünkü anahtar kelime taşımayan, hiç görülmemiş bir marka adından kategori çıkarmak bilgi-teorik olarak mümkün değildir; "
          f"doğru davranış 'Belirsiz' demektir. Altın sette kapsama %{m['gold']['coverage'] * 100:.0f}, emin-ama-yanlış oranı %{m['gold']['confident_wrong_rate'] * 100:.1f}, ECE {m['gold']['ece']:.3f}."
    )

st.markdown("### 3. Ölçülmüş Model Karşılaştırması")
bench = load_json(BENCHMARK_PATH)
if bench is None:
    st.info("Benchmark dosyası yok. `PYTHONPATH=. python scripts/benchmark_models.py` çalıştırın.")
else:
    table = pd.DataFrame(bench["results"]).rename(columns={
        "model": "Model", "unseen_merchant_macro_f1": "Görülmemiş işyeri F1", "gold_macro_f1": "Altın set F1", "gold_accuracy": "Altın set doğruluk",
        "single_ms_median": "Tekil gecikme (ms)", "batch_ms_per_row": "Batch (ms/satır)", "pickle_mb": "Boyut (MB)", "fit_sec": "Eğitim (sn)"})
    st.dataframe(table.style.format({c: "{:.3f}" for c in table.columns if c != "Model"}), use_container_width=True, hide_index=True)
    st.caption(f"Protokol: {bench['protocol']}. {bench['not_measured']} "
               "Not: Logistic Regression ile LightGBM bu veri boyutunda istatistiksel olarak başa baştır (170 satırlık altın sette fark birkaç satırdır).")

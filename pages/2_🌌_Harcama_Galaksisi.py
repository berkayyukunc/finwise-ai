"""
FinWise-AI: Harcama Galaksisi & Denetimsiz Öğrenme (K-Means & PCA)
==================================================================
Bu sayfa, kullanıcının 8 boyutlu finansal davranış vektörünü
K-Means++ ile kümeleyip PCA ile 3D Galaksi koordinatlarına taşır.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.clustering.archetypes import SpendingArchetypeClusterer
from src.utils.statement_loader import ensure_statement_loaded

st.set_page_config(page_title="Harcama Galaksisi | FinWise-AI", page_icon="🌌", layout="wide")

st.markdown("# 🌌 Senin Harcama Galaksin (Unsupervised Behavioral Clustering)")
st.caption("8 Boyutlu Davranış Uzayı + K-Means++ ($k=6$) + PCA 3D İndirgeme")

df = ensure_statement_loaded()


@st.cache_resource
def load_clusterer():
    return SpendingArchetypeClusterer()

clusterer = load_clusterer()
result = clusterer.classify_user_archetype(df)

st.info(
    f"ℹ️ Popülasyon **sentetiktir** (6 prototip etrafında üretilmiş 1200 sanal kullanıcı); bu sayfa gerçek bir segmentasyon bulgusu değil, "
    f"kümeleme hattının gösterimidir. Küme→arketip eşlemesi Macar algoritmasıyla yapılır · saflık %{result['quality']['purity'] * 100:.1f} · silhouette {result['quality']['silhouette']:.2f}"
)
if result["status"] != "ok":
    st.warning(result["archetype_desc"])
    st.stop()

# Üst Bilgi Kartı
c1, c2 = st.columns([2, 3])
with c1:
    st.markdown("### 🏆 Finansal Harcama Kişiliğin")
    st.markdown(f"## **{result['archetype_title']}**")
    st.info(result['archetype_desc'])

    st.markdown("##### 📍 3D Galaksi Koordinatların:")
    st.code(f"PCA-1 (X): {result['user_coords']['x']:+.2f}\nPCA-2 (Y): {result['user_coords']['y']:+.2f}\nPCA-3 (Z): {result['user_coords']['z']:+.2f}")

    st.caption(f"📐 PCA Açıklanan Toplam Varyans: %{sum(result['explained_variance_ratio'])*100:.1f} (8 boyuttan 3 boyuta)")

with c2:
    st.markdown("### 🎯 8 Boyutlu Davranış Radar Grafiği")
    feat_vec = clusterer.extract_features_from_statement(df)[0]
    categories = [
        "Yeme-İçme", "Market", "Teknoloji", "Giyim",
        "Abonelik", "Hafta Sonu", "Taksit Oranı", "Sepet Hacmi"
    ]
    fig_radar = go.Figure(data=go.Scatterpolar(
        r=feat_vec,
        theta=categories,
        fill="toself",
        fillcolor="rgba(59, 130, 246, 0.4)",
        line=dict(color="#3b82f6", width=2)
    ))
    fig_radar.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        showlegend=False,
        template="plotly_dark",
        height=320,
        margin=dict(l=40, r=40, t=20, b=20)
    )
    st.plotly_chart(fig_radar, use_container_width=True)

st.divider()

# 3D Galaksi Saçılım Grafiği (Plotly 3D)
st.markdown("### 🪐 3 Boyutlu Finansal Galaksi Haritası")
st.caption("Sentetik popülasyonun 6 kümesi ve ekstrenin bu uzaydaki konumu:")

pop_df = pd.DataFrame(result["population_samples"])

fig_3d = px.scatter_3d(
    pop_df,
    x="x",
    y="y",
    z="z",
    color="archetype",
    title="Popülasyon Harcama Kümeleri (PCA 3D Projeksiyonu)",
    opacity=0.6,
    hover_data=["archetype"]
)

# Kullanıcının konumunu altın bir yıldız olarak ekle
user_x = result["user_coords"]["x"]
user_y = result["user_coords"]["y"]
user_z = result["user_coords"]["z"]

fig_3d.add_trace(go.Scatter3d(
    x=[user_x],
    y=[user_y],
    z=[user_z],
    mode="markers+text",
    marker=dict(size=14, color="#facc15", symbol="diamond", line=dict(color="#ffffff", width=2)),
    text=["⭐ SEN BURADASIN!"],
    textposition="top center",
    name="Senin Konumun"
))

fig_3d.update_layout(
    template="plotly_dark",
    height=600,
    margin=dict(l=10, r=10, t=40, b=10),
    scene=dict(
        xaxis_title="PCA 1",
        yaxis_title="PCA 2",
        zaxis_title="PCA 3"
    )
)

st.plotly_chart(fig_3d, use_container_width=True)

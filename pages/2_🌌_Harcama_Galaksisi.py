"""
FinWise-AI: Harcama Galaksisi & Denetimsiz Öğrenme (K-Means & PCA)
==================================================================
Bu sayfa, kullanıcının 8 boyutlu finansal davranış vektörünü
K-Means++ ile kümeleyip PCA ile 3D Galaksi koordinatlarına taşır.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.clustering.archetypes import SpendingArchetypeClusterer
from src.ui import theme
from src.utils.statement_loader import ensure_statement_loaded

st.set_page_config(page_title="Harcama Galaksisi | FinWise-AI", page_icon="🌌", layout="wide")

theme.inject_css(st)
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
        r=list(feat_vec) + [feat_vec[0]],
        theta=categories + [categories[0]],
        fill="toself",
        fillcolor="rgba(235, 104, 52, 0.22)",
        line=dict(color=theme.BRAND, width=2),
        hovertemplate="%{theta}: %{r:.0%}<extra></extra>",
    ))
    fig_radar.update_layout(
        polar=dict(bgcolor=theme.SURFACE, radialaxis=dict(visible=True, range=[0, 1], tickformat=".0%", gridcolor=theme.GRID, tickfont=dict(size=10, color=theme.INK_MUTED)),
                   angularaxis=dict(gridcolor=theme.GRID, tickfont=dict(size=11, color=theme.INK_SOFT))),
        showlegend=False, template=theme.TEMPLATE, height=330, margin=dict(l=50, r=50, t=20, b=20),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

st.divider()

# 3D Galaksi Saçılım Grafiği (Plotly 3D)
st.markdown("### 🪐 3 Boyutlu Finansal Galaksi Haritası")
st.caption("Renk kümeler arasında karşılaştırma için değil, **senin kümeni** ayırt etmek içindir: 6 kümeyi aynı anda renkle ayırmak, saçılım grafiğinde ölçülen ayrım eşiğini geçemiyor. Küme adları imleçle gelen ipucunda yazar.")

pop_df = pd.DataFrame(result["population_samples"])

own_cluster = result["cluster_id"]
pop_df["kume"] = pop_df["cluster"].map(lambda c: "Senin kümen" if c == own_cluster else "Diğer profiller")

fig_3d = go.Figure()
for label, color, size, opacity in [("Diğer profiller", theme.OTHER_COLOR, 3.5, 0.45), ("Senin kümen", theme.BRAND, 5.5, 0.85)]:
    sub = pop_df[pop_df["kume"] == label]
    fig_3d.add_trace(go.Scatter3d(
        x=sub["x"], y=sub["y"], z=sub["z"], mode="markers", name=f"{label} ({len(sub)})",
        marker=dict(size=size, color=color, opacity=opacity, line=dict(width=0)),
        customdata=sub[["archetype"]], hovertemplate="%{customdata[0]}<extra></extra>",
    ))

user_x = result["user_coords"]["x"]
user_y = result["user_coords"]["y"]
user_z = result["user_coords"]["z"]

fig_3d.add_trace(go.Scatter3d(
    x=[user_x], y=[user_y], z=[user_z], mode="markers+text",
    marker=dict(size=13, color=theme.STATUS["warning"], symbol="diamond", line=dict(color="#ffffff", width=2)),
    text=["⭐ SEN"], textposition="top center", textfont=dict(color=theme.INK, size=12),
    name="Senin konumun", hovertemplate="Senin konumun<extra></extra>",
))

fig_3d.update_layout(
    title="Popülasyon içindeki konumun (PCA 3B)",
    template=theme.TEMPLATE, height=600, margin=dict(l=10, r=10, t=48, b=10),
    scene=dict(xaxis_title="PCA 1", yaxis_title="PCA 2", zaxis_title="PCA 3",
               xaxis=dict(backgroundcolor=theme.SURFACE, gridcolor=theme.GRID),
               yaxis=dict(backgroundcolor=theme.SURFACE, gridcolor=theme.GRID),
               zaxis=dict(backgroundcolor=theme.SURFACE, gridcolor=theme.GRID)),
)

st.plotly_chart(fig_3d, use_container_width=True)

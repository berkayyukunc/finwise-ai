"""
FinWise-AI: Bileşik Getiri & Tasarruf Simülatörü (Wealth & DCA Engine)
=======================================================================
Bu sayfa, harcama tasarruflarını BIST, Altın ve Fon yatırımlarına
dönüştürür ve Fisher denklemiyle enflasyon arındırması yapar.
"""

import plotly.graph_objects as go
import streamlit as st

from src.predictive.wealth_simulator import DISCLAIMER, WealthSimulator

st.set_page_config(page_title="Yatırım & DCA | FinWise-AI", page_icon="💰", layout="wide")

st.markdown("# 💰 Tasarruf -> Bileşik Getiri & Varlık Simülatörü")
st.caption("Fisher Denklemi ile Enflasyon Arındırması & Düzenli Alım (DCA) Servet Projeksiyonu")

# Parametre Girişleri (İki Sütun)
c1, c2, c3 = st.columns([2, 2, 2])
with c1:
    monthly_saving = st.slider(
        "Aylık Tasarruf ve Yatırım Tutarı (TL):",
        min_value=500,
        max_value=30000,
        value=3000,
        step=500
    )
with c2:
    horizon = st.slider(
        "Yatırım Vadesi (Yıl):",
        min_value=1,
        max_value=10,
        value=3,
        step=1
    )
with c3:
    view_mode = st.radio(
        "Görünüm Modu:",
        ["Enflasyondan Arındırılmış (Reel Satın Alma Gücü)", "Nominal (Gelecekteki TL Tutarı)"],
        index=0
    )

is_real = "Reel" in view_mode
sim = WealthSimulator.simulate_dca(monthly_saving_tl=monthly_saving, horizon_years=horizon)

# Özgürlük Göstergesi Kartı
st.markdown(
    f"<div style='background: linear-gradient(90deg, #1e3a8a, #312e81); padding: 18px; border-radius: 12px; border: 1px solid #4338ca; margin-bottom: 20px;'>"
    f"<h3 style='color: #60a5fa; margin: 0;'>🕊️ {sim['freedom_metric']['headline']}</h3>"
    f"<p style='color: #cbd5e1; margin-top: 6px; margin-bottom: 0;'>Aylık üreteceği tahmini reel pasif gelir: <b>{sim['freedom_metric']['monthly_passive_income_today_tl']:,.2f} TL (Bugünün parasıyla)</b></p>"
    f"</div>",
    unsafe_allow_html=True
)

# Özet Metrikler
total_principal = sim["total_invested_principal"]
bist_data = sim["results_by_asset"]["BIST 100 Hisse Fonu"]
gold_data = sim["results_by_asset"]["Gram Altın (Darphane / Fon)"]
fund_data = sim["results_by_asset"]["Para Piyasası / Mevduat Fonu"]

val_key = "final_real_value" if is_real else "final_nominal_value"

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric("Toplam Yatırdığınız Ana Para", f"{total_principal:,.2f} TL")
with k2:
    st.metric("BIST 100 Portföy Değeri", f"{bist_data[val_key]:,.2f} TL", delta=f"+%{bist_data['real_annual_return_pct' if is_real else 'nominal_annual_return_pct']:.1f} Yıllık")
with k3:
    st.metric("Gram Altın Portföy Değeri", f"{gold_data[val_key]:,.2f} TL", delta=f"+%{gold_data['real_annual_return_pct' if is_real else 'nominal_annual_return_pct']:.1f} Yıllık")
with k4:
    st.metric("Mevduat / Para Piyasası", f"{fund_data[val_key]:,.2f} TL", delta=f"+%{fund_data['real_annual_return_pct' if is_real else 'nominal_annual_return_pct']:.1f} Yıllık")

st.divider()

# Büyüme Eğrileri Grafiği (Plotly)
st.markdown(f"### 📈 Aylar Bazında Varlık Birikim Eğrileri ({'Reel Satın Alma Gücü' if is_real else 'Nominal'})")

curve_key = "real_curve" if is_real else "nominal_curve"
months_count = horizon * 12
x_months = [f"{m}. Ay" for m in range(1, months_count + 1)]

fig_wealth = go.Figure()

colors = {
    "BIST 100 Hisse Fonu": "#3b82f6",
    "Gram Altın (Darphane / Fon)": "#eab308",
    "Para Piyasası / Mevduat Fonu": "#10b981",
    "S&P 500 / Global Teknoloji": "#a855f7"
}

for asset_name, asset_info in sim["results_by_asset"].items():
    fig_wealth.add_trace(go.Scatter(
        x=x_months,
        y=asset_info[curve_key],
        mode="lines",
        name=asset_name,
        line=dict(color=colors.get(asset_name, "#ffffff"), width=2.5)
    ))

# Ana para referans çizgisi
fig_wealth.add_trace(go.Scatter(
    x=x_months,
    y=[monthly_saving * m for m in range(1, months_count + 1)],
    mode="lines",
    name="Yatırılan Ana Para",
    line=dict(color="#64748b", width=1.5, dash="dot")
))

fig_wealth.update_layout(
    title=f"Düzenli Alım (DCA) Servet Projeksiyonu - {horizon} Yıl",
    xaxis_title="Vade (Ay)",
    yaxis_title="Toplam Varlık Değeri (TL)",
    template="plotly_dark",
    height=450,
    margin=dict(l=20, r=20, t=40, b=20)
)

st.plotly_chart(fig_wealth, use_container_width=True)

st.caption(f"📐 **Varsayımlar:** Yıllık enflasyon %{sim['inflation_rate_pct']:.0f}; varlık getirileri sabit nominal yıllık oranlardır (tahmin değil, senaryo girdisi). "
           "Reel getiri Fisher formülü $\\frac{1 + r_{nominal}}{1 + i_{TÜFE}} - 1$ ile hesaplanır.")
st.warning(f"⚠️ {DISCLAIMER}")

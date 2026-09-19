"""
FinWise-AI: Bileşen Tabanlı Harcama Tahmini & Anomali İncelemesi
================================================================
Gelecek 30 gün = bilinen taksitler + tespit edilen abonelikler + isteğe bağlı harcama tahmini.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.predictive.forecasting import SpendingForecaster
from src.utils.statement_loader import ensure_statement_loaded

st.set_page_config(page_title="Tahmin & Anomali | FinWise-AI", page_icon="📈", layout="wide")

st.markdown("# 📈 Gelecek 30 Gün: Yükümlülükler + Harcama Tahmini")
st.caption("Deterministik bileşenler (taksit, abonelik) ayrı; stokastik bileşen walk-forward ile seçilen modelle ve blok bootstrap aralığıyla tahmin edilir.")

df = ensure_statement_loaded()
fc = SpendingForecaster(df).forecast_next_30_days()
ob = fc["obligations"]

m1, m2, m3 = st.columns(3)
m1.metric("Bilinen Taksitler", f"{ob['installments_total']:,.2f} TL", delta=f"{len(ob['installments'])} kalem", delta_color="off")
m2.metric("Tespit Edilen Abonelikler", f"{ob['subscriptions_total']:,.2f} TL", delta=f"{len(ob['subscriptions'])} kalem", delta_color="off")

if fc["status"] != "ok":
    m3.metric("İsteğe Bağlı Harcama Tahmini", "—")
    st.warning(f"**Tahmin üretilmedi:** {fc['method_used']}. Sayı uydurmak yerine yalnızca ekstrede kesinleşmiş kalemler gösteriliyor.")
else:
    m3.metric("İsteğe Bağlı Harcama Tahmini", f"{fc['components']['discretionary']:,.2f} TL", delta=fc["method_used"], delta_color="off")
    t1, t2 = st.columns(2)
    t1.metric("Beklenen Toplam (30 gün)", f"{fc['total_expected_monthly']:,.2f} TL")
    t2.metric("%95 Aralık (30 günlük toplam)", f"{fc['risk_lower_monthly']:,.0f} – {fc['risk_upper_monthly']:,.0f} TL")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fc["historical_daily_dates"], y=fc["historical_daily_amounts"], mode="lines+markers", name="Geçmiş (isteğe bağlı)", line=dict(color="#3b82f6", width=2)))
    fig.add_trace(go.Scatter(x=fc["forecast_dates"], y=fc["forecast_upper_95"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=fc["forecast_dates"], y=fc["forecast_lower_95"], mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(16, 185, 129, 0.15)", name="Günlük %95 bant"))
    fig.add_trace(go.Scatter(x=fc["forecast_dates"], y=fc["forecast_daily_mean"], mode="lines", name="Tahmin", line=dict(color="#10b981", width=2, dash="dash")))
    fig.update_layout(title="Günlük İsteğe Bağlı Harcama ve 30 Günlük Projeksiyon", xaxis_title="Tarih", yaxis_title="TL", template="plotly_dark", height=420, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

    val = fc["validation"]
    with st.expander("🔬 Model seçimi: walk-forward doğrulama sonuçları"):
        if val.get("candidates"):
            st.dataframe(pd.DataFrame(val["candidates"]).T.rename(columns={"daily_rmse": "Günlük RMSE (TL)", "daily_mae": "Günlük MAE (TL)", "horizon_total_ape": "Ufuk toplamı mutlak % hata", "folds": "Katman"}), use_container_width=True)
            st.caption(f"Genişleyen pencere, {val['n_splits']} katman × {val['horizon']} gün. Seçim ölçütü: günlük RMSE (ortalamayı hedefler; aylık toplam için yansız). Holt-Winters yalnızca ≥ 56 gün veri varken adaydır.")
        else:
            st.caption("Walk-forward için yeterli veri yok; günlük ortalama kullanıldı.")

with st.expander("📋 Yükümlülük detayları", expanded=fc["status"] != "ok"):
    if ob["installments"]:
        st.dataframe(pd.DataFrame(ob["installments"]), use_container_width=True)
    if ob["subscriptions"]:
        st.dataframe(pd.DataFrame(ob["subscriptions"]), use_container_width=True)
    if not ob["installments"] and not ob["subscriptions"]:
        st.caption("Bu ekstrede devam eden taksit ya da abonelik bulunamadı.")

st.divider()
st.markdown("### 🚨 Anomali İncelemesi")
st.caption("Medyan/MAD tabanlı sağlam z-skoru (log tutar), mükerrer çekim kuralı ve destekleyici Isolation Forest skoru. Sabit 'her ekstrede %X anomali' varsayımı yoktur.")

anom_df = df[df["is_anomaly"]] if "is_anomaly" in df.columns else df.iloc[0:0]
if len(anom_df) > 0:
    cols = [c for c in ["date", "clean_description", "amount", "category", "anomaly_type", "anomaly_score", "anomaly_reason"] if c in anom_df.columns]
    st.dataframe(anom_df[cols].style.format({"amount": "{:,.2f} TL", "anomaly_score": "{:.2f}"}), use_container_width=True)
else:
    st.success("Bu dönemde eşikleri aşan bir işlem saptanmadı.")

"""
FinWise-AI: Finansal Yapay Zeka Sohbet Asistanı (AI Copilot)
============================================================
Bu sayfa, kural tabanlı niyet yönlendirici + deterministik Pandas araçlarıyla çalışan
soru-cevap arayüzüdür. LLM içermez; yanıttaki her tutar araç çıktısından gelir.
"""

import streamlit as st

from src.copilot.agent import FinancialCopilot
from src.utils.statement_loader import ensure_statement_loaded

st.set_page_config(page_title="FinWise Copilot | FinWise-AI", page_icon="🤖", layout="wide")

st.markdown("# 🤖 FinWise Copilot")
st.caption("Kural tabanlı niyet yönlendirici + deterministik hesaplama araçları. LLM içermez; anlamadığı soruyu tahmin etmez, söyler.")

df = ensure_statement_loaded()
debt = st.session_state.get("total_debt", 0.0)
bank = st.session_state.get("bank_name", "Banka")

copilot = FinancialCopilot(df, total_debt=debt, bank_name=bank, min_payment=st.session_state.get("min_payment"))


# Hızlı Soru Butonları
st.markdown("##### ⚡ Hızlı Sorular:")
q_cols = st.columns(4)
selected_quick = None
with q_cols[0]:
    if st.button("🛒 Markete ne kadar gitti?"):
        selected_quick = "Markete ne kadar harcadım?"
with q_cols[1]:
    if st.button("☕ Kahve & Yeme-İçme?"):
        selected_quick = "Kahveye ve yemeğe ne kadar gitti?"
with q_cols[2]:
    if st.button("📊 En çok nereye harcadım?"):
        selected_quick = "En çok nereye para kaçırdım?"
with q_cols[3]:
    if st.button("🎯 Tasarruf Önerisi Ver"):
        selected_quick = "Bana tasarruf önerisi ver"

# Sohbet Geçmişi
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = [
        {
            "role": "assistant",
            "content": f"Merhaba! Ben **FinWise Copilot**. {bank} ekstrenizi inceledim. Bana harcamalarınız, lider mağazalarınız veya tasarruf stratejileriniz hakkında dilediğinizi sorabilirsiniz!"
        }
    ]

# Sohbet Mesajlarını Çiz
for msg in st.session_state["chat_history"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Giriş Çubuğu
user_query = st.chat_input("Finansal sorunuzu yazın (Örn: 'Markete ne kadar harcadım?')...")
prompt_to_process = selected_quick or user_query

if prompt_to_process:
    # Kullanıcı mesajını ekle
    st.session_state["chat_history"].append({"role": "user", "content": prompt_to_process})
    with st.chat_message("user"):
        st.markdown(prompt_to_process)

    # Asistan cevabı üret
    with st.chat_message("assistant"):
        ans = copilot.ask(prompt_to_process)
        st.markdown(ans)
        st.session_state["chat_history"].append({"role": "assistant", "content": ans})

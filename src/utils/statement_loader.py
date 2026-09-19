"""
FinWise-AI: Ekstre İşleme ve Oturum Durumu Yardımcısı
=====================================================
`process_statement`, PDF -> (ParsedStatement, DataFrame) hattının TEK giriş noktasıdır
(Document AI -> NLP kategori -> anomali). Hem ana sayfa hem alt sayfalar bunu kullanır;
böylece iki ayrı kopya birbirinden sapmaz.
"""

import logging
import os
from typing import Optional, Tuple

import pandas as pd

from src.document_ai.bank_parsers.base_parser import ParsedStatement
from src.document_ai.parser_engine import DocumentAIEngine
from src.nlp.models import DEFAULT_MODEL_PATH, POSClassifierPipeline
from src.predictive.anomaly import TransactionAnomalyDetector

logger = logging.getLogger(__name__)
DEFAULT_SAMPLE_PDF = "data/statements/sample_garanti.pdf"


def load_classifier(model_path: str = DEFAULT_MODEL_PATH) -> Optional[POSClassifierPipeline]:
    if not os.path.exists(model_path):
        logger.warning("Model dosyası yok: %s (önce `python train_nlp_model.py` çalıştırın).", model_path)
        return None
    return POSClassifierPipeline().load(model_path)


def process_statement(
    pdf_bytes: bytes,
    classifier: Optional[POSClassifierPipeline] = None,
    password: Optional[str] = None,
) -> Tuple[ParsedStatement, pd.DataFrame]:
    """PDF baytlarını uçtan uca işler. Document AI hataları (şifre, taranmış PDF, bozuk dosya) çağırana yükselir."""
    stmt, _redacted = DocumentAIEngine.process_pdf(pdf_bytes, password=password)

    if classifier is not None and classifier.is_trained and stmt.transactions:
        preds = classifier.predict([t.raw_description for t in stmt.transactions])
        for tx, (category, confidence) in zip(stmt.transactions, preds):
            tx.category_predicted, tx.confidence_score = category, confidence

    df = TransactionAnomalyDetector().detect_anomalies(stmt.to_dataframe())
    return stmt, df


def ensure_statement_loaded(default_pdf_path: str = DEFAULT_SAMPLE_PDF) -> pd.DataFrame:
    """Alt sayfalara doğrudan girildiğinde oturumu varsayılan örnek ekstreyle doldurur (Streamlit'e bağımlıdır)."""
    import streamlit as st

    if st.session_state.get("transactions_df") is not None:
        return st.session_state["transactions_df"]
    if not os.path.exists(default_pdf_path):
        st.warning("Varsayılan örnek ekstre bulunamadı. Lütfen ana sayfadan bir ekstre yükleyin.")
        st.stop()

    with open(default_pdf_path, "rb") as f:
        stmt, df = process_statement(f.read(), classifier=load_classifier())

    st.session_state.update({
        "parsed_statement": stmt, "transactions_df": df, "bank_name": stmt.bank_name,
        "total_debt": stmt.total_debt, "min_payment": stmt.min_payment,
    })
    st.info("💡 **Otomatik Yükleme:** Sentetik örnek ekstre yüklendi. Kendi ekstreniz için Ana Sayfa'ya dönün.")
    return df

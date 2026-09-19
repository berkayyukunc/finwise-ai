# FinWise-AI: CPU-only konteyner (Hugging Face Spaces uyumlu, port 7860)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

# libgomp1: LightGBM çalışma zamanı. OCR (tesseract) bilinçli olarak YOK: kodda OCR kullanılmıyor.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Model, kilitli bağımlılıklarla imaj içinde eğitilir: pickle sürüm uyuşmazlığı riski sıfırlanır.
RUN python train_nlp_model.py

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 7860
HEALTHCHECK CMD curl --fail http://localhost:7860/_stcore/health || exit 1
ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.maxUploadSize=15", "--browser.gatherUsageStats=false"]

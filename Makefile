.PHONY: install train benchmark samples test lint check run
PY := PYTHONPATH=. python

install:
	pip install -r requirements-dev.txt

train:        ## Sentetik veri üret, modeli sızıntısız protokolle değerlendir ve kaydet
	$(PY) train_nlp_model.py

benchmark:    ## Arena sayfasındaki karşılaştırma tablosunu yeniden ölç
	$(PY) scripts/benchmark_models.py

samples:      ## 8 farklı yerleşimde sentetik PDF + truth.json üret
	$(PY) scripts/generate_synthetic_pdf.py

test:
	python -m pytest --cov --cov-report=term-missing

lint:
	ruff check .

check: lint test

run:
	streamlit run app.py

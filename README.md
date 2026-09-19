---
title: FinWise AI - Finansal Belge Zekasi
emoji: 🧠
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# 🧠 FinWise-AI: Kredi Kartı Ekstreleri İçin Uygulamalı Yapay Zeka

> PDF ekstre → güvenlik denetimi → ayrıştırma + **kuruşu kuruşuna sağlama** → PII karartma → POS sınıflandırma (+SHAP) → bileşen tabanlı tahmin → anomali → kümeleme → deterministik soru-cevap.
> Tamamı CPU'da, sıfır API maliyetiyle, **sentetik veriyle** ve ölçülmüş rakamlarla.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Bu repoda gerçek kişisel veri yoktur.** Tüm ekstreler ve eğitim verisi sentetiktir; bunun metriklere etkisi [`MODEL_CARD.md`](MODEL_CARD.md) içinde açıkça tartışılır.

---

## Ölçülmüş sonuçlar

Aşağıdaki her sayı `make train`, `make benchmark` ya da `make test` ile yeniden üretilebilir.

| Alan | Ölçüm | Sonuç |
| :-- | :-- | :-- |
| **POS sınıflandırma** | Altın set (170 elle yazılmış satır, eğitimde yok) macro-F1 | **0,828** |
| | Görülmemiş işyeri (GroupShuffleSplit) macro-F1 | 0,720 → esnaf+anahtar kelime **0,979**, anahtar kelimesiz yeni marka 0,496 (beklenen sınır) |
| | Görülmüş marka macro-F1 (iyimser üst sınır) | 0,978 |
| | Emin-ama-yanlış oranı (güven ≥ 0,90) | %4,7 |
| | Gecikme / boyut | 0,57 ms tekil · 0,05 ms/satır batch · 2 MB |
| **Document AI** | 8 farklı yerleşim: işlem sayısı, dönem borcu, asgari, son 4 hane, tarihler | 8/8 birebir; sağlama farkı 0,00 TL |
| | PII sızıntısı (ad, TC, adres, 3. kişi adı, metadata; tüm sayfalar) | 0 |
| **Tahmin** | Aylık %95 aralığın ampirik kapsaması (100 simülasyon) | **%92** |
| **Anomali** | Anomalisiz sentetik ekstrelerde yanlış alarm | **%0,5** (ekstrelerin %76'sında hiç alarm yok) |
| **Kümeleme** | Centroid→arketip eşleme doğruluğu | 6/6 (4 farklı tohumda) · saflık > %95 |
| **Kalite** | Test / kapsama / lint | 229 test · %94 satır+dal kapsaması · ruff temiz |

> **İlk sürümle fark:** İlk sürüm "%99,9 F1" bildiriyordu; bu, sentetik veride rastgele ayrımın yol açtığı şablon sızıntısıydı (test satırlarının %35,5'i eğitimde vardı; markalar ayrılınca F1 0,205'e düşüyordu). Hikâyenin tamamı: [`PROJE_AMACI_VE_DEGERLENDIRME.md`](PROJE_AMACI_VE_DEGERLENDIRME.md).

---

## Mimari

```mermaid
flowchart TD
    PDF["📄 PDF ekstre (+ şifre)"] --> SAN["🛡️ Sanitizer<br/>boyut · sayfa · açılma bombası<br/>tüm nesne ağacında aktif içerik"]
    SAN --> TXT["Metin çıkarımı (bellek içi)<br/>metin katmanı yoksa RED"]
    TXT --> FP["Banka profili<br/>yalnızca başlıktan ağırlıklı parmak izi"]
    FP --> PARSE["Ayrıştırma<br/>başlık alanları + işlemler"]
    PARSE --> CHK{"Sağlama<br/>başlık borcu = Σ işlemler?"}
    CHK --> RED["🕶️ PII karartma (tüm sayfalar)<br/>etiket · desen+sağlama · isim yayılımı · metadata"]
    RED --> DF["Maskelenmiş işlemler"]

    DF --> NLP["🔤 Katlama → char_wb TF-IDF → LightGBM<br/>güven < 0,55 ⇒ Diğer / Belirsiz"]
    NLP --> SHAP["SHAP (toplanabilir, çift sayımsız)"]
    DF --> FC["📈 Tahmin = taksitler + abonelikler<br/>+ walk-forward ile seçilen model<br/>blok bootstrap aralığı"]
    DF --> AN["🚨 Medyan/MAD + mükerrer çekim<br/>+ destekleyici Isolation Forest"]
    NLP --> KM["🪐 K-Means + Macar eşlemesi + PCA"]
    NLP --> CP["🤖 Copilot: niyet yönlendirici<br/>→ Pandas araçları → şablon"]
```

**Sıra bilinçlidir:** PII, ayrıştırmadan *sonra* karartılır. Önce karartmak, sağlama için gereken başlık alanlarını (dönem borcu, son 4 hane) da yok eder. Ham PDF ve ham metin `process_pdf` dışına çıkmaz.

| Modül | Dosya | Tasarım kararı |
| :-- | :-- | :-- |
| Sanitizer | `src/document_ai/sanitizer.py` | Aktif içerik yalnızca kökte değil tüm nesnelerde aranır; akışlar sınırlı bellekle açılır |
| Ayrıştırıcı | `src/document_ai/bank_parsers/` | Veri güdümlü `BankProfile`; tek motor; gerçek sağlama (`None` = doğrulanamadı ≠ geçerli) |
| Karartıcı | `src/document_ai/redactor.py` | Sabit "üst %20" yerine kelime koordinatı bazlı tespit; içerik akışından siler |
| Ön işlemci | `src/nlp/preprocessor.py` | NFC + 6 Türkçe çiftin tamamında katlama; idempotent (hypothesis ile test edilir) |
| Sınıflandırıcı | `src/nlp/models.py` | Sızıntısız protokol; abstain; tek geçişli inference |
| Açıklayıcı | `src/nlp/explainer.py` | Toplanabilirlik ve n-gram→kelime dağıtımı testle kanıtlı |
| Tahmin | `src/predictive/forecasting.py` | Deterministik yükümlülükler ayrı; model seçimi gerçek adaylar üzerinde; yetersiz veride sayı uydurmaz |
| Anomali | `src/predictive/anomaly.py` | `contamination` yok: temiz ekstrede alarm üretmez |
| Kümeleme | `src/clustering/archetypes.py` | Küme kimliği keyfidir → centroid'ler prototiplere Macar algoritmasıyla eşlenir |
| Copilot | `src/copilot/agent.py` | LLM/RAG **değildir**; yanıttaki her tutar araç çıktısından gelir (testli); bilmediğini söyler |

---

## Hızlı başlangıç

```bash
python3 -m venv venv && source venv/bin/activate
make install        # kilitli bağımlılıklar + geliştirme araçları
make train          # sentetik veri → sızıntısız değerlendirme → model + metrik JSON
make check          # ruff + 229 test + %85 kapsama kapısı
make run            # http://localhost:8501
```

Docker (model, kilitli sürümlerle imaj içinde eğitilir; root olmayan kullanıcı):

```bash
docker build -t finwise-ai . && docker run --rm -p 7860:7860 finwise-ai
```

Diğer hedefler: `make benchmark` (Arena tablosunu yeniden ölç) · `make samples` (8 yerleşimde PDF + `truth.json`).

---

## Gizlilik

Gerçek ekstrenizi **yalnızca yerelde** işleyin. Herkese açık bir bulut kurulumuna gerçek ekstre yüklemek KVKK md. 9 kapsamında yurt dışına veri aktarımı sayılabilir; canlı demo yalnızca sentetik örnekler içindir. Proje PCI-DSS ya da KVKK uyumlu olduğunu **iddia etmez**; neyin korunduğu ve neyin korunmadığı [`SECURITY.md`](SECURITY.md) içindedir.

## Bilinen sınırlar

- Banka profilleri ve altın set gerçek verilerle doğrulanmamıştır (sentetik yerleşimler, yazarın elle yazdığı satırlar).
- OCR yoktur: taranmış PDF'ler açık bir hata mesajıyla reddedilir.
- Etiketsiz serbest metindeki kişi adları için NER gerekir.
- Arketip popülasyonu sentetiktir; kümeleme bir yöntem gösterimidir, segmentasyon bulgusu değildir.
- Getiri simülatörü deterministik senaryodur; yatırım tavsiyesi değildir.

## Dokümanlar

[`PROJE_AMACI_VE_DEGERLENDIRME.md`](PROJE_AMACI_VE_DEGERLENDIRME.md) tasarım kararları ve ödünleşimler · [`MODEL_CARD.md`](MODEL_CARD.md) · [`SECURITY.md`](SECURITY.md) · [`OGRENME_REHBERI.md`](OGRENME_REHBERI.md) mülakat anlatımı · [`docs/planning/`](docs/planning/) tarihsel planlama arşivi

**Lisans:** MIT

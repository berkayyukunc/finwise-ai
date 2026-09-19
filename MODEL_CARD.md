# Model Kartı — POS Harcama Sınıflandırıcısı

| | |
| :-- | :-- |
| **Model** | TF-IDF (`char_wb`, 2–5 gram, 20.000 öznitelik) + LightGBM (200 ağaç, 15 yaprak, `colsample_bytree=0.3`) |
| **Görev** | Kredi kartı ekstresindeki işyeri açıklamasını 11 harcama kategorisinden birine atamak; emin değilse **"Diğer / Belirsiz"** demek |
| **Artefakt** | `data/models/pos_lightgbm.joblib` (≈2 MB) · metrikler: `data/models/pos_model_metrics.json` |
| **Yeniden üretim** | `make train` (deterministik: aynı tohum → aynı metrikler) · `make benchmark` |
| **Sürüm kilidi** | scikit-learn 1.9.1 · lightgbm 4.7.0 (artefakt içine yazılır; yüklemede uyuşmazlık uyarısı verilir) |

## Amaçlanan kullanım ve kapsam dışı

- **Amaç:** Tek bir kullanıcının kendi ekstresini yerelde kategorilere ayırması (kişisel bütçe analizi).
- **Kapsam dışı:** Kredi skorlama, dolandırıcılık kararı, herhangi bir otomatik finansal karar. Model çıktısı bir insan için özet üretir; tek başına karar vermez.

## Eğitim verisi — sentetik, ve bunun sonuçları

Gerçek banka verisi KVKK ve banka sırrı nedeniyle kullanılamaz. Eğitim verisi `src/nlp/dataset_generator.py` ile üretilir (18.000 satır, kopyalar silindikten sonra ≈15.000):

| Kaynak | Pay | İçerik | Öğrettiği şey |
| :-- | :-- | :-- | :-- |
| `brand` | ~%55 | ~200 zincir marka + POS gürültüsü (önek, şube no, şehir, unvan, 25 karakter kırpma, sesli düşürme, yazım hatası) | Bilinen markanın gürültülü yazımlarını tanımak |
| `generic` | ~%37 | Rastgele ad + kategori anahtar kelimesi (`OZKAN ECZANESI`) | Marka ezberi yerine anahtar kelimeden genelleme |
| `other` | ~%8 | Havale/EFT, kategori sinyali olmayan iş kolları, sahte marka adları, anlamsız kodlar | "Bilmiyorum" demek |

**Bilinen yanlılık:** Marka listesi ve gürültü modeli yazarın varsayımlarını yansıtır; gerçek POS dağılımı (uzun kuyruktaki küçük esnaf, banka bazlı kısaltma alışkanlıkları) farklıdır.

## Değerlendirme protokolü

Sentetik veride rastgele `train_test_split`, aynı marka şablonunu hem eğitime hem teste koyar (**şablon sızıntısı**). İlk sürümdeki "%99,9 F1" bunun ürünüydü: test satırlarının %35,5'i eğitimde birebir vardı ve markalar ayrıldığında macro-F1 **0,205**'e düşüyordu. Artık üç ayrı soru, üç ayrı metrikle yanıtlanır:

| Metrik | Soru | Yöntem | Sonuç |
| :-- | :-- | :-- | :-- |
| `seen_merchant` | Bildiğim markanın yeni yazımını tanıyor muyum? (iyimser üst sınır) | Kopyalar silinir → tabakalı rastgele ayrım | macro-F1 **0,978** |
| `unseen_merchant` | Hiç görmediğim işyerine genelleyebiliyor muyum? | `GroupShuffleSplit` (test işyerleri eğitimde yok; kodda `assert` ile doğrulanır) | macro-F1 **0,720** |
| **`gold`** (manşet) | Gerçekçi, elle yazılmış satırlarda nasılım? | `data/gold/pos_gold_set.csv`, 170 satır, eğitimde hiç kullanılmaz | macro-F1 **0,828** · doğruluk 0,824 |

`unseen_merchant` kırılımı sonucun nedenini açıklar:

| Test alt kümesi | Doğruluk | Yorum |
| :-- | :-- | :-- |
| `generic` (görülmemiş esnaf + anahtar kelime) | **0,979** | Anahtar kelimeden genelleme çalışıyor |
| `other` (kategori dışı) | **0,948** | Model "bilmiyorum" demeyi öğrenmiş |
| `brand` (görülmemiş zincir marka, anahtar kelime yok) | **0,496** | **Beklenen sınır.** `ZARA` kelimesinden "giyim" çıkarmak bilgi-teorik olarak mümkün değildir; doğru davranış abstain'dir. Bu sayıyı yükseltmenin yolu model değil, **veri**dir (işyeri sözlüğü / MCC kodu / geri bildirim döngüsü) |

Altın set ayrıntısı: kapsama %85,3 · cevaplanan satırlarda doğruluk 0,890 · **emin-ama-yanlış (güven ≥ 0,90 ve hatalı) %4,7** · ECE 0,103 · zorluk: kolay 0,918 / orta 0,800 / zor 0,353 (n=17; `SBUX`, `CRFSA`, `PGS*FLYPGS` gibi ağır kısaltmalar).

### Ölçülmüş karşılaştırma (`make benchmark`, aynı protokol, abstain'siz argmax)

| Model | Görülmemiş işyeri F1 | Altın set F1 | Tekil gecikme (ms) | Batch (ms/satır) | Boyut (MB) |
| :-- | --: | --: | --: | --: | --: |
| Çoğunluk sınıfı (taban) | 0,012 | 0,011 | 0,14 | 0,001 | 0,1 |
| Word TF-IDF + Naive Bayes | 0,626 | 0,775 | 0,22 | 0,001 | 0,9 |
| Char-wb TF-IDF + Logistic Regression | **0,733** | **0,831** | 0,20 | 0,014 | 1,8 |
| Char-wb TF-IDF + LightGBM (üretim) | 0,720 | 0,807 | 0,57 | 0,048 | 5,2 |

**Dürüst okuma:** Logistic Regression ile LightGBM istatistiksel olarak başa baştır (170 satırda fark ≈4 satır) ve doğrusal model daha küçük ve hızlıdır. LightGBM'in üretimde kalma gerekçesi doğruluk değil, doğrusal olmayan n-gram etkileşimleri ve `TreeExplainer` ile kurulmuş açıklama hattıdır. Bir sonraki iterasyonda doğrusal modele geçmek meşru bir sadeleştirmedir (doğrusal modelde SHAP kapalı formdadır). **Türkçe BERT ölçülmemiştir**; bu nedenle hiçbir tabloda sayı olarak yer almaz. Gecikme rakamları Apple Silicon dizüstünde, tek iş parçacığında ölçülmüştür.

## Abstain (kaçınma) politikası

`güven < 0,55` ya da temizlenmiş metin boş → `"Diğer / Belirsiz"`. Eski sürüm boş metne %58,9 güvenle "Giyim", `AHMET YILMAZ HAVALE`'ye %99,4 güvenle "Giyim" diyordu; ikisi de artık abstain eder (`tests/test_nlp.py`).

## Açıklanabilirlik

SHAP `TreeExplainer`, log-odds uzayında. Toplanabilirlik testle doğrulanır: `margin = taban + aktif n-gram'lar + metinde olmayan n-gram'lar` (mutlak hata < 1e-4). Kelime skorları, `char_wb` analizörünün birebir kopyasıyla n-gram geçiş sayısına orantılı dağıtılır; kelime skorlarının toplamı aktif katkıya tam eşittir. "Metinde olmayan n-gram" katkısı arayüzde ayrıca gösterilir — gizlenmesi açıklamayı yanıltıcı kılardı (bir örnekte toplam atfın %80'i buradaydı).

## Sınırlar ve riskler

1. **Altın set yazarı = model yazarı.** Set eğitimde kullanılmaz ve üzerinde hiperparametre ayarı yapılmamıştır; yine de bağımsız değildir ve küçüktür (n=170, ±%6 güven aralığı). Gerçek doğrulama, gönüllülerden toplanmış anonim POS satırları gerektirir.
2. **Kalibrasyon orta düzeyde** (ECE 0,10). Güven skoru olasılık olarak yorumlanmamalı, sıralama/eşik için kullanılmalıdır.
3. **Çok anlamlı işyerleri** (`GETIR`, `AMAZON`, `TRENDYOL YEMEK` vs `TRENDYOL`) tek etiketle temsil edilir.
4. **Dağılım kayması:** Yeni markalar sürekli çıkar. İzlenecek sinyal: abstain oranı ve kullanıcı düzeltme oranı.

## Yol haritası

Kullanıcı düzeltmelerini toplayan geri bildirim döngüsü → aktif öğrenme · işyeri sözlüğü/MCC zenginleştirmesi · sıcaklık ölçekleme ile kalibrasyon · bağımsız altın set.

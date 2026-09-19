# Model Kartı — POS Harcama Sınıflandırıcısı

| | |
| :-- | :-- |
| **Model** | TF-IDF (`char_wb`, 2–5 gram, 20.000 öznitelik) + LightGBM (200 ağaç, 15 yaprak, `colsample_bytree=0.3`) |
| **Görev** | Kredi kartı ekstresindeki işyeri açıklamasını 12 harcama kategorisinden birine atamak; emin değilse **"Diğer / Belirsiz"** demek |
| **Artefakt** | `data/models/pos_lightgbm.joblib` (≈2,2 MB) · metrikler: `data/models/pos_model_metrics.json` |
| **Yeniden üretim** | `make train` (deterministik: aynı tohum → aynı metrikler) · `make benchmark` |
| **Sürüm kilidi** | scikit-learn 1.9.1 · lightgbm 4.7.0 (artefakt içine yazılır; yüklemede uyuşmazlık uyarısı verilir) |

## Amaçlanan kullanım ve kapsam dışı

- **Amaç:** Tek bir kullanıcının kendi ekstresini yerelde kategorilere ayırması (kişisel bütçe analizi).
- **Kapsam dışı:** Kredi skorlama, dolandırıcılık kararı, herhangi bir otomatik finansal karar. Model çıktısı bir insan için özet üretir; tek başına karar vermez.

## Eğitim verisi — sentetik, ve bunun sonuçları

Gerçek banka verisi KVKK ve banka sırrı nedeniyle kullanılamaz. Eğitim verisi `src/nlp/dataset_generator.py` ile üretilir (30.000 satır, kopyalar silindikten sonra ≈24.000):

| Kaynak | Pay | İçerik | Öğrettiği şey |
| :-- | :-- | :-- | :-- |
| `brand` | ~%55 | ~300 zincir marka + POS gürültüsü (önek, şube no, şehir, unvan, 19–30 karakter kırpma (önekten sonra), taksit eki, sesli düşürme, yazım hatası) | Bilinen markanın gürültülü yazımlarını tanımak |
| `generic` | ~%37 | Rastgele ad + kategori anahtar kelimesi (`OZKAN ECZANESI`) | Marka ezberi yerine anahtar kelimeden genelleme |
| `other` | ~%8 | Havale/EFT, kategori sinyali olmayan iş kolları, sahte marka adları, anlamsız kodlar | "Bilmiyorum" demek |

**Bilinen yanlılık:** Marka listesi ve gürültü modeli yazarın varsayımlarını yansıtır; gerçek POS dağılımı (uzun kuyruktaki küçük esnaf, banka bazlı kısaltma alışkanlıkları) farklıdır.

## Değerlendirme protokolü

Sentetik veride rastgele `train_test_split`, aynı marka şablonunu hem eğitime hem teste koyar (**şablon sızıntısı**). İlk sürümdeki "%99,9 F1" bunun ürünüydü: test satırlarının %35,5'i eğitimde birebir vardı ve markalar ayrıldığında macro-F1 **0,205**'e düşüyordu. Artık üç ayrı soru, üç ayrı metrikle yanıtlanır:

| Metrik | Soru | Yöntem | Sonuç |
| :-- | :-- | :-- | :-- |
| `seen_merchant` | Bildiğim markanın yeni yazımını tanıyor muyum? (iyimser üst sınır) | Kopyalar silinir → tabakalı rastgele ayrım | macro-F1 **0,965** |
| `unseen_merchant` | Hiç görmediğim işyerine genelleyebiliyor muyum? | `GroupShuffleSplit` (test işyerleri eğitimde yok; kodda `assert` ile doğrulanır) | macro-F1 **0,762** |
| **`gold`** (manşet) | Gerçekçi, elle yazılmış satırlarda nasılım? | `data/gold/pos_gold_set.csv`, 195 satır, eğitimde hiç kullanılmaz | macro-F1 **0,837** · doğruluk 0,831 |

`unseen_merchant` kırılımı sonucun nedenini açıklar:

| Test alt kümesi | Doğruluk | Yorum |
| :-- | :-- | :-- |
| `generic` (görülmemiş esnaf + anahtar kelime) | **0,949** | Anahtar kelimeden genelleme çalışıyor |
| `other` (kategori dışı) | **0,919** | Model "bilmiyorum" demeyi öğrenmiş |
| `brand` (görülmemiş zincir marka, anahtar kelime yok) | **0,555** | **Beklenen sınır.** `ZARA` kelimesinden "giyim" çıkarmak bilgi-teorik olarak mümkün değildir; doğru davranış abstain'dir. Bu sayıyı yükseltmenin yolu model değil, **veri**dir (işyeri sözlüğü / MCC kodu / geri bildirim döngüsü) |

Altın set ayrıntısı: kapsama %87,2 · cevaplanan satırlarda doğruluk 0,894 · **emin-ama-yanlış (güven ≥ 0,90 ve hatalı) %2,1** · ECE 0,067 · zorluk: kolay 0,895 / orta 0,864 / zor 0,333 (n=21) / **saha 1,000 (n=10)**.

`field` (saha) katmanı, kullanıcının gerçek ekstresinden alınan ve **ilk sürümün tamamını yanlış sınıfladığı** satırlardır
(`MokaUnited/BERSHKA G 1. Taksit`, `STRADIVARIUS IZMIR I`, `Hotel at Booking.com/Amsterdam` …). Bu satırlar eğitimde
kullanılmaz; düzeltmenin gerçekten işe yarayıp yaramadığını ölçen kanıt bunlardır.

### Ölçülmüş karşılaştırma (`make benchmark`, aynı protokol, abstain'siz argmax)

| Model | Görülmemiş işyeri F1 | Altın set F1 | Tekil gecikme (ms) | Batch (ms/satır) | Boyut (MB) |
| :-- | --: | --: | --: | --: | --: |
| Çoğunluk sınıfı (taban) | 0,012 | 0,009 | 0,18 | 0,002 | 0,1 |
| Word TF-IDF + Naive Bayes | 0,625 | 0,826 | 0,31 | 0,002 | 1,6 |
| Char-wb TF-IDF + Logistic Regression | 0,753 | **0,840** | 0,28 | 0,019 | 2,7 |
| Char-wb TF-IDF + LightGBM (üretim) | **0,762** | 0,802 | 0,79 | 0,056 | 6,0 |

**Dürüst okuma:** Logistic Regression ile LightGBM istatistiksel olarak başa baştır (195 satırda fark ≈7 satır; sızıntısız metrikte LightGBM önde, altın sette doğrusal model önde) ve doğrusal model daha küçük ve hızlıdır. LightGBM'in üretimde kalma gerekçesi doğruluk değil, doğrusal olmayan n-gram etkileşimleri ve `TreeExplainer` ile kurulmuş açıklama hattıdır. Bir sonraki iterasyonda doğrusal modele geçmek meşru bir sadeleştirmedir (doğrusal modelde SHAP kapalı formdadır). **Türkçe BERT ölçülmemiştir**; bu nedenle hiçbir tabloda sayı olarak yer almaz. Gecikme rakamları Apple Silicon dizüstünde, tek iş parçacığında ölçülmüştür.

## Abstain (kaçınma) politikası

`güven < 0,55` ya da temizlenmiş metin boş → `"Diğer / Belirsiz"`. Eski sürüm boş metne %58,9 güvenle "Giyim", `AHMET YILMAZ HAVALE`'ye %99,4 güvenle "Giyim" diyordu; ikisi de artık abstain eder (`tests/test_nlp.py`).

## Açıklanabilirlik

SHAP `TreeExplainer`, log-odds uzayında. Toplanabilirlik testle doğrulanır: `margin = taban + aktif n-gram'lar + metinde olmayan n-gram'lar` (mutlak hata < 1e-4). Kelime skorları, `char_wb` analizörünün birebir kopyasıyla n-gram geçiş sayısına orantılı dağıtılır; kelime skorlarının toplamı aktif katkıya tam eşittir. "Metinde olmayan n-gram" katkısı arayüzde ayrıca gösterilir — gizlenmesi açıklamayı yanıltıcı kılardı (bir örnekte toplam atfın %80'i buradaydı).

## Sınırlar ve riskler

1. **Altın set yazarı = model yazarı.** Set eğitimde kullanılmaz ve üzerinde hiperparametre ayarı yapılmamıştır; yine de bağımsız değildir ve küçüktür (n=195, ±%5 güven aralığı). Gerçek doğrulama, gönüllülerden toplanmış anonim POS satırları gerektirir.
2. **Kalibrasyon orta düzeyde** (ECE 0,07). Güven skoru olasılık olarak yorumlanmamalı, sıralama/eşik için kullanılmalıdır.
3. **Çok anlamlı işyerleri** (`GETIR`, `AMAZON`, `TRENDYOL YEMEK` vs `TRENDYOL`) tek etiketle temsil edilir.
4. **Kalan hata deseni (dürüstlük):** Altın sette 6 giyim satırı hâlâ kaçıyor — `LCW 0412 FORUM ISTANBUL`,
   `H&M HENNES MAURITZ TR`, `BOYNER BÜYÜK MAĞAZACILIK` gibi. Marka sözlükte olsa da ek kelimeler güveni eşiğin altına
   düşürüyor; çoğu **"Belirsiz" diyerek** kaçıyor (güvenli yön), ikisi yanlış kategoriye gidiyor. Bunları tek tek
   eğitim verisine eklemek altın seti bozardı (test verisine uydurma), o yüzden yapılmadı.
5. **Dağılım kayması:** Yeni markalar sürekli çıkar. İzlenecek sinyal: abstain oranı ve kullanıcı düzeltme oranı.

## Yol haritası

Kullanıcı düzeltmelerini toplayan geri bildirim döngüsü → aktif öğrenme · işyeri sözlüğü/MCC zenginleştirmesi · sıcaklık ölçekleme ile kalibrasyon · bağımsız altın set.

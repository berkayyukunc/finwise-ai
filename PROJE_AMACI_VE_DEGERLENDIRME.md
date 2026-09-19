# 🧠 FinWise-AI: Proje Amacı, Tasarım Kararları ve Değerlendirme Raporu (v2)

> Bu doküman projenin **ne yaptığını, neden öyle yaptığını ve neyi yapamadığını** teknik bir değerlendirici için özetler.
> İlke: Bu dokümandaki her iddia ya bir testle ya da yeniden üretilebilir bir ölçümle desteklenir; desteklenemeyen iddia yazılmaz.

---

## BÖLÜM 1: Amaç

Piyasadaki CRUD harcama takipçileri ve LLM sarmalayıcıları bir adayın ML, NLP, zaman serisi ve veri mühendisliği derinliğini göstermez. FinWise-AI, bu yetkinlikleri **gerçek dünya kısıtları altında** (gürültülü veri, gizlilik, CPU-only, sıfır API maliyeti) uçtan uca sergileyen bir uygulamalı yapay zeka projesidir.

**Ne değildir:** Bir banka ürünü, KVKK/PCI-DSS uyumlu bir sistem ya da gerçek veriyle doğrulanmış bir model değildir. Tüm veri sentetiktir. Projenin değeri "production-ready" olması değil, **üretimde karşılaşılan metodolojik tuzakların doğru teşhis edilip çözülmüş olmasıdır**.

**"0 TL" ilkesi:** Tüm modeller CPU'da çalışır (sınıflandırıcı 2 MB, tekil çıkarım 0,57 ms). Bu ilkenin dürüst sınırı: ücretsiz *yurt dışı* bulut barındırma ile *gerçek kişisel veri* işleme KVKK md. 9 nedeniyle bir arada savunulamaz; gerçek ekstre yalnızca yerelde işlenir ([`SECURITY.md`](SECURITY.md)).

---

## BÖLÜM 2: v1 → v2 — Denetim bulguları ve çözümleri

İlk sürüm bağımsız bir teknik denetimden 6/10 aldı. Temel eleştiri tek cümleydi: *"Mimari doğru, ama dokümanın iddiaları ile kodun yaptığı arasında açık var."* Aşağıdaki tablo her bulguyu, kök nedeni ve çözümün **kanıtını** listeler.

| # | Bulgu (v1) | Kök neden | Çözüm (v2) | Kanıt |
| :-- | :-- | :-- | :-- | :-- |
| 1 | "%99,9 F1" — test satırlarının %35,5'i eğitimde; markalar ayrılınca F1 **0,205** | Sentetik veride rastgele split = şablon sızıntısı | Kopya silme + `GroupShuffleSplit` + elle yazılmış altın set; üç metrik ayrı raporlanır | Altın set F1 **0,828** · `test_grouped_split_has_no_merchant_leakage` |
| 2 | Boş metne %59, `AHMET YILMAZ HAVALE`'ye %99 güvenle "Giyim" | OOD sınıfı ve abstain yok | "Diğer / Belirsiz" sınıfı + güven eşiği | `test_out_of_domain_inputs_abstain_instead_of_guessing` |
| 3 | `KOÇTAŞ` → Restoran; `ASUS`→`us`, `SBARRO`→`arro`; NFD'de `mi gros` | Yalnızca ı/i katlanıyor; regex'lerde kelime sonu sınırı yok; NFC yok | 6 çiftin tamamında katlama + NFC + `(?!\w)` sınırları | 23 ön işlemci testi (hypothesis ile idempotentlik dahil) |
| 4 | SHAP ısı haritası n-gram'ları alt dize aramasıyla kelimeye bağlıyor; atfın %80'e varan kısmı gizleniyor | Çift sayım; metinde olmayan n-gram katkısı atılıyor | `char_wb` analizörünün kopyasıyla orantılı dağıtım; yok-olan katkı ayrıca gösterilir | `margin == model.predict(raw_score=True)` (hata < 1e-4) |
| 5 | Sanitizer'dan sayfa-düzeyi JavaScript, Launch eylemi, `payload.exe` eki, XFA sağ çıkıyor; 299 KB → 300 MB bomba kabul ediliyor | Yalnızca kök sözlük anahtarları siliniyor; boyut sınırı sıkıştırılmış bayta bakıyor | Tüm nesne ağacı taranır; akışlar sınırlı `zlib` ile ölçülür | `tests/test_security.py` (11 negatif test) |
| 6 | Adres %24'e kayınca, 2. sayfa başlığında ve metadata'da PII sızıyor | Sabit "ilk sayfa üst %20" varsayımı; test PDF'leri PII'yi bilerek o bölgeye koyuyor (döngüsel doğrulama) | Tüm sayfalarda kelime koordinatı bazlı tespit + isim yayılımı + metadata temizliği | 8 yerleşimde sızıntı = 0 (ham içerik akışları dahil) |
| 7 | `checksum_valid=True`, `card_last4="0000"`, dönem `2026-01-01` sabit kodlu | Önce karartma → başlık alanları yok oluyor → stub | Sıra değişti: ayrıştır → sağla → karart. Okunamayan alan `None` döner | `test_checksum_mismatch_is_reported_not_hidden`, `test_missing_header_total_yields_unverified_not_valid` |
| 8 | "8 banka parser'ı" birbirinin kopyası; 8 test PDF'i tek şablon; `VAKIFBANK WORLD` → Yapı Kredi | Strateji deseni isimde; parmak izi tüm metinde `in` ile | Veri güdümlü `BankProfile` + başlıkta ağırlıklı tam-kelime parmak izi; 8 **farklı** yerleşim + `truth.json` | `test_bank_statement_matches_ground_truth[×8]`, `test_fingerprint_collisions` |
| 9 | Walk-forward Holt-Winters'ı değil hareketli ortalamayı ölçüyor; aylık üst sınır günlük sınırların toplamı (+%69, kapsama %100); boş veriye 10.500 TL "tahmin" | Doğrulanan model ≠ kullanılan model; hatalar tam korelasyonlu varsayılıyor | Bileşen tabanlı tahmin; gerçek adaylar üzerinde walk-forward; blok bootstrap; `insufficient_data` | Ampirik kapsama **%92** · `test_insufficient_data_never_fabricates_numbers` |
| 10 | Anomalisiz ekstrede %5-8 işlem işaretleniyor; doküman "5D" diyor, kod 4D | Sabit `contamination`; ortalama/std maskeleme etkisi | Medyan/MAD (log tutar) + mükerrer çekim kuralı + destekleyici IF (mutlak eşik) | Yanlış alarm **%0,5** · `test_masking_effect_small_sample` |
| 11 | 6 arketip etiketinin **6'sı da yanlış**; boş ekstre = "Taksit Mimarı" | K-Means küme kimliği keyfidir; sabit `ARCHETYPE_NAMES[id]` | Centroid'ler prototiplere Macar algoritmasıyla eşlenir; yetersiz veride etiket yok | 6/6 doğru, 4 tohumda · saflık > %95 |
| 12 | `rag/agent.py` = 5 `elif`; `question.lower()` Türkçe I hatası (`GIDA`, `DIŞARI` tanınmıyor); anlaşılmayan soru özete düşüyor | İsimlendirme gerçeği yansıtmıyor | `src/copilot/`: niyet yönlendirici → araçlar → şablon; katlama ile normalizasyon; dürüst "bilmiyorum" | `test_every_amount_in_answer_comes_from_tool_result`, 16 yönlendirme testi |
| 13 | Git yok; `>=` bağımlılıklar "kilitli" diye anılıyor; Dockerfile'da kullanılmayan Tesseract; `print`; global RNG mutasyonu | MLOps iskeleti yok | Git + CI (lint → eğit → test + kapsama kapısı) + tam sürüm kilidi + model kartı + `logging` + yerel RNG | `make check`: ruff temiz, 282 test, %95 kapsama |

Denetimin ortaya çıkardığı, v1 raporunda **olmayan** üç hata da çözüldü: (a) örnek PDF'lerde İ/Ş/Ğ harfleri `·` olarak basılıyordu (`TÜRK·YE ·· BANKASI`) ve bir "font onarımı" eşlemesi bunu gizliyordu; (b) anomali modülünde işyeri anahtarı sayıları maskelediği için farklı şubeler mükerrer çekim sayılıyordu; (c) tahmin modeli seçim ölçütü (14 günlük toplam hata) haftalık mevsimselliği yapısal olarak göremiyordu → günlük RMSE'ye geçildi. Üçü de yeni yazılan testler tarafından yakalandı.

### Saha testi: gerçek ekstreyle ilk temas

v2 yayımlandıktan sonra yazarın kendi Vakıfbank ekstresi yüklendi ve sistem **0 işlem** ayrıştırdı. Sistem bunu gizlemedi ("Hiç işlem satırı ayrıştırılamadı", "Sağlama TUTMADI"), ama sonuç README'deki "profiller gerçek ekstreyle doğrulanmamıştır" sınırının ne kadar gerçek olduğunu gösterdi. Teşhis, içerik görülmeden yapıldı (`scripts/diagnose_statement.py`: harfler `A`, rakamlar `9`). Sentetik yerleşimlerin hiçbirinin modellemediği altı gerçek:

| Gerçek ekstrede görülen | Etkisi | Çözüm |
| :-- | :-- | :-- |
| İngilizce sayı biçimi (`1,234.56`) | Hiçbir tutar tanınmadı → 0 işlem | İki yerel biçim + belge düzeyinde çoğunluk oylaması (`detect_locale`) |
| Çok sütunlu tutarlar (`TL | USD | puan`): `69,90 0,00` | "Son tutarı al" kuralı puan sütununu okurdu | Satır sonundaki kesintisiz tutar koşusunun **ilk** elemanı |
| Açıklamaya gömülü yabancı tutar (`USD 12.99`, `(2400.00 TL İşlemin 1/4 Taksidi)`) | İşlem tutarı sanılırdı | Belgenin yerel biçiminde olmayan sayı işlem tutarı sayılmaz |
| Önceki dönem devri + ödeme satırları | Sağlama formülü tutmazdı | Dönem borcu = devir + tüm hareketler (ödeme/iade eksi) |
| `+` öneki / soneki alacak; `3. Taksit … 3x1,250.00`, `Son Taksit`, `İşlemin 1/4 Taksidi` | Ödemeler harcama, taksit toplamı bilinmez olurdu | İşaret ve taksit desenleri genişletildi |
| İki sütunlu başlıkta etiketsiz adres bloğu | Adres karartılmıyordu (sütunlar iç içe geçince tarama duruyordu) | Hizasız satırı atla + ilk sayfa başlık bölgesi her zaman karartılır |

Sonuç: 5 gerçek Vakıfbank PDF'inin 5'inde sağlama uçtan uca kuruşu kuruşuna tuttu; 4 Ziraat ekstresinde de tuttu, ancak bunlar ekran görüntüsünden elle aktarılan satırlarla sınandı (Ziraat PDF'inin metin çıkarımı doğrulanmadı). Gerçek ekstreler ve onlardan alınan hiçbir değer repoda yer almaz; iki sentetik yerleşim bu yapıları taklit edecek biçimde yeniden kuruldu ve 31 yeni test eklendi. Ders: **sentetik test verisini, test edilen varsayımı bilen kişi üretirse doğrulama döngüseldir** — v1 denetiminde başkasına söylenen bu cümle, v2'nin kendi ayrıştırıcısı için de geçerliydi.

### İkinci saha testi: sınıflandırıcı gerçek işyeri adlarında

Kullanıcı kendi ekstresindeki giyim harcamalarının "Akaryakıt / Ulaşım" olarak etiketlendiğini bildirdi. Tek tek marka
eklemek yerine kök neden ölçüldü — ve dördü de sistemikti:

| Bulgu | Kanıt / kök neden | Çözüm |
| :-- | :-- | :-- |
| Ödeme kuruluşu öneki markayı bastırıyor | `BERSHKA` tek başına **%99,6 Giyim**, `MokaUnited/BERSHKA G` **%88 Ulaşım** | Önek listesi 26 kuruluşa çıkarıldı (MokaUnited, Vallet, Craftgate, Paycell …) |
| `1. Taksit` eki gürültü üretiyor | temizlenmiş metin `… <num> taksit` | Taksit eki temizleniyor (bilgi zaten ayrıştırıcıda) |
| POS alanı kırpması tek harf artığı bırakıyor | `STRADIVARIUS IZMIR I` → şehir silinince dangling `i` | Tek harf artıkları atılıyor |
| Sözlük dar + konaklama kategorisi yok | `STRADIVARIUS` %39 Belirsiz, `Booking.com` %44 Belirsiz | Marka sözlüğü ~100 → ~300; **Seyahat / Konaklama** kategorisi eklendi |

Ek olarak sentetik üretici gerçekçileştirildi: kırpma artık **önek eklendikten sonra** uygulanıyor (gerçekte olan da bu:
`MokaUnited/PULL AND BEAR` → `MokaUnited/PULL AND`) ve taksit ekleri veri setine enjekte ediliyor.

**Ölçülen sonuç:** kullanıcının 10 gerçek satırının tamamı doğru (önce 0/10), sızıntısız metrik 0,720 → **0,762**
(görülmemiş marka 0,496 → 0,555), emin-ama-yanlış oranı %4,7 → **%2,1**, ECE 0,103 → **0,067**.

**Dürüstlük notu:** Bu satırlar altın sete `field` katmanı olarak eklendi ve eğitimde kullanılmıyor. Altın sette hâlâ
kaçan 6 giyim satırı var; bunları tek tek eğitim verisine eklemek ölçüm setini bozardı, o yüzden yapılmadı.

---

## BÖLÜM 3: Model seçimleri ve ödünleşimler

| Alan | Seçim | Reddedilen | Gerekçe ve **dürüst** değerlendirme |
| :-- | :-- | :-- | :-- |
| POS NLP | `char_wb` TF-IDF + LightGBM | Türkçe BERT / LLM | POS metni dilbilgisel cümle değildir; kısaltma ve kırpılmış metinde karakter n-gram'ları güçlüdür. **Ölçüm:** Logistic Regression aynı özniteliklerle başa baş (0,831 vs 0,807; ≈4 satır fark) ve daha hızlı. LightGBM'in gerekçesi doğruluk üstünlüğü değil, etkileşimler ve TreeExplainer hattıdır; doğrusal modele geçiş meşru bir sadeleştirmedir. BERT **ölçülmemiştir**, hakkında sayı verilmez. |
| Tahmin | Yükümlülükler + {ortalama, haftanın günü, Holt-Winters} arasından walk-forward seçimi | Prophet / LSTM | Tek ekstre ≈30 gün veridir; 30 noktayla Prophet/LSTM eğitmek istatistiksel olarak anlamsızdır. Asıl kazanç model değil **ayrıştırma**dır: taksit ve abonelik zaten bilinir. Holt-Winters ancak ≥ 8 hafta veriyle adaydır; tek ekstrede sistem dürüstçe run-rate kullanır. |
| Anomali | Medyan/MAD + kurallar + destekleyici Isolation Forest | Sabit eşik / tek başına IF | n≈40 işlemde IF kararsızdır ve `contamination` her ekstrede alarm üretir. Sağlam istatistik açıklanabilir ("medyanın 47 katı") ve maskelemeye dayanıklıdır. Ekstrede saat yoktur → "gece harcaması" iddiası yoktur. |
| Açıklanabilirlik | SHAP TreeExplainer | LIME / permütasyon | Ağaçlarda tam (exact) ve hızlı. Koşul: toplanabilirliği bozmadan sunmak (Bölüm 2, #4). |
| Soru-cevap | Kural tabanlı yönlendirici + Pandas araçları | Serbest LLM | Sayılar kod tarafından üretilir; bu, endüstrideki "LLM yönlendirir, kod hesaplar" kalıbının LLM'siz alt kümesidir. Bedeli: dar dil kapsamı. Sistem bunu gizlemez, anlamadığını söyler. Yönlendirici katmanı LLM ile değiştirilebilir; araç ve şablon katmanı aynı kalır. |
| Kümeleme | K-Means + Macar eşlemesi + PCA | if-else kuralları | Popülasyon sentetik olduğundan bu bir **yöntem gösterimidir**; arayüzde böyle etiketlenir. |

---

## BÖLÜM 4: Güvenlik ve gizlilik özeti

Ayrıntı: [`SECURITY.md`](SECURITY.md). Özet: sistem PCI-DSS/KVKK uyumu **iddia etmez**; veri minimizasyonunu mühendislik düzeyinde uygular (ham PDF ve ham metin tek fonksiyonun dışına çıkmaz, log'da içerik yoktur, üçüncü taraf çağrısı yoktur). Tehdit modelinin dürüst notu: sunucuda PDF JavaScript'i zaten çalışmaz; asıl risk ayrıştırıcı zafiyetleri ve kaynak tüketimidir — bunlar için gereken süreç yalıtımı (sandbox, bellek limiti) bu repoda **yoktur** ve üretim gereksinimi olarak belgelenmiştir.

---

## BÖLÜM 5: Dosya haritası

```text
├── app.py · pages/                     # Streamlit arayüzü (5 sayfa); metrikleri JSON'dan okur, elle yazılmış sayı içermez
├── src/
│   ├── document_ai/
│   │   ├── sanitizer.py                # Nesne ağacı taraması, sınırlı inflate, şifreli PDF
│   │   ├── redactor.py                 # Kelime koordinatı bazlı PII tespiti, isim yayılımı, metadata
│   │   ├── parser_engine.py            # Sıra: temizle → oku → tanı → ayrıştır+sağla → karart → maskele
│   │   └── bank_parsers/               # BankProfile verisi + tek ayrıştırma motoru + gerçek checksum
│   ├── nlp/                            # preprocessor · dataset_generator · models · explainer
│   ├── predictive/                     # forecasting · anomaly · wealth_simulator
│   ├── clustering/archetypes.py
│   ├── copilot/agent.py                # (eski adı: rag/) niyet yönlendirici + deterministik araçlar
│   └── utils/statement_loader.py       # PDF → DataFrame hattının tek giriş noktası
├── tests/                              # 282 test (aşağıda)
├── scripts/                            # generate_synthetic_pdf (8 yerleşim + truth.json) · benchmark_models
├── data/gold/pos_gold_set.csv          # 170 elle yazılmış değerlendirme satırı (eğitimde kullanılmaz)
├── data/models/                        # model + pos_model_metrics.json + benchmark_results.json
├── MODEL_CARD.md · SECURITY.md · OGRENME_REHBERI.md
├── pyproject.toml · requirements*.txt · Makefile · Dockerfile · .github/workflows/ci.yml
└── docs/planning/                      # Tarihsel tasarım şartnameleri (mevcut kodu anlatmaz)
```

---

## BÖLÜM 6: Test stratejisi

v1'deki 21 test ağırlıklı olarak "çökmüyor mu?" sorusunu soruyordu (`assert "cluster_id" in res`, boş veride `> 0`); bu yüzden 6/6 yanlış etiketi ve uydurma tahmini yakalayamadı. v2 testleri **davranışı** doğrular:

| Dosya | # | Ne doğrular |
| :-- | --: | :-- |
| `test_document_ai.py` | 77 | 8 yerleşim × truth.json eşleşmesi · sayfa/akış/metadata düzeyinde PII sızıntısı · sağlama tutmama/yapılamama · parmak izi çakışmaları · tutar/taksit/satır biçimleri · TC ve Luhn için **özellik testleri** (her tek hane hatası yakalanır) |
| `test_copilot.py` | 36 | Yönlendirme (Türkçe büyük harf tuzakları dahil) · yanıttaki her tutarın araç çıktısından gelmesi · reddetme davranışı · bozuk tablolar |
| `test_predictive.py` | 30 | Uydurma sayı yok · iade/ödeme dışlama (birebir değer) · yükümlülük ayrıştırma · doğrulanan model = kullanılan model · **ampirik aralık kapsaması** · yanlış alarm oranı · maskeleme etkisi · mükerrer çekim istisnaları · Fisher özdeşliği |
| `test_nlp.py` | 27 | Sızıntısız ayrım · sızıntılı > sızıntısız metrik · abstain · altın set **regresyon tabanı** · SHAP toplanabilirliği · XSS |
| `test_preprocessor.py` | 23 | 6 diakritik çifti · NFD · marka bozmayan gürültü regex'i · idempotentlik (hypothesis, 300 örnek) |
| `test_clustering.py` | 17 | Her prototip kendi etiketini alır (4 tohum) · global RNG'ye dokunmama · determinizm |
| `test_security.py` | 11 | Silahlandırılmış PDF · açılma bombası · şifreli/bozuk/aşırı büyük/taranmış PDF |
| `test_integration_pipeline.py` | 8 | 8 yerleşimde uçtan uca sözleşme + çıktıda PII yok |

```text
$ make check
ruff check .            → All checks passed!
python -m pytest --cov  → 273 passed in ~25s · TOTAL coverage 95% (kapı: %85)
```

---

## BÖLÜM 7: Bilinen sınırlar (bir değerlendiricinin sorması gerekenler)

1. **Gerçek veri sınırlı.** Ayrıştırıcı uçtan uca yalnızca tek bankanın (Vakıfbank) 5 gerçek PDF'iyle doğrulandı; Ziraat satır düzeyinde sınandı; diğer 6 yerleşim temsilidir; altın set bağımsız bir etiketleyiciyle doğrulanmamıştır. Altın seti modelin yazarı yazmıştır (n=170, ±%6).
2. **Görülmemiş zincir markada doğruluk 0,50.** Model bunu çözemez; işyeri sözlüğü, MCC kodu ya da kullanıcı geri bildirimi gerekir.
3. **OCR ve NER yok.** Taranmış PDF reddedilir; etiketsiz serbest metindeki kişi adları yakalanmaz.
4. **Süreç yalıtımı yok.** Ayrıştırıcı zafiyetlerine karşı sandbox üretim gereksinimidir.
5. **Tek ekstre = ~30 gün.** Mevsimsel modeller ancak birden çok ekstre birlikte yüklendiğinde (arayüz bunu destekler) devreye girer.
6. **Aralık kapsaması %92** (nominal %95): artıklar örneklem içi olduğundan hafif iyimserdir.
7. **Kümeleme popülasyonu sentetiktir**; getiri simülatörü sabit oranlı senaryodur.

**Yol haritası:** kullanıcı düzeltmeleriyle aktif öğrenme → çoklu ekstre geçmişi (mevsimsellik + yeni işyeri sinyali) → Türkçe NER → ayrıştırıcıyı ayrı, kaynak limitli süreçte çalıştırma → yönlendirici katmanına isteğe bağlı yerel LLM.

---

## BÖLÜM 8: Değerlendirici için istem

> *"Ekteki FinWise-AI reposunu kıdemli bir ML mühendisi gözüyle incele. (1) `PROJE_AMACI_VE_DEGERLENDIRME.md` ve `MODEL_CARD.md` içindeki her sayısal iddiayı `make train && make check` ile doğrula; doğrulayamadığın iddiayı işaretle. (2) Değerlendirme protokolünde kalan sızıntı ya da iyimserlik var mı? (3) `SECURITY.md`'deki 'korunmayanlar' listesi eksiksiz mi; PII karartmayı atlatan bir PDF üretebilir misin? (4) Bilinen sınırlar bölümü dürüst mü, eksik mi?"*

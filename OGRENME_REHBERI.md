# 🎓 FinWise-AI: Mülakat Anlatım Rehberi

**Hedef:** Projeyi, arkasındaki kararları ve **kendi hatalarını nasıl bulup düzelttiğini** bir mülakatta savunabilmek.

> **Altın kural:** Yalnızca ölçtüğün şeyi söyle. "BERT 50 ms sürer" gibi ölçmediğin bir rakamı söylersen, karşındaki kişi "nasıl ölçtün?" diye sorar ve güven o an biter. Bu projedeki her rakam `make train`, `make benchmark` ya da `make test` ile yeniden üretilebilir — mülakatta bunu söyle.

---

## BÖLÜM 1: 1 dakikalık anlatım

> *"FinWise-AI, PDF kredi kartı ekstrelerini uçtan uca işleyen bir uygulamalı ML projesi: güvenlik denetimi, ayrıştırma ve kuruşu kuruşuna sağlama, PII karartma, POS metni sınıflandırma ve SHAP açıklaması, bileşen tabanlı harcama tahmini ve anomali tespiti. Hepsi CPU'da, API maliyeti olmadan ve KVKK nedeniyle tamamen sentetik veriyle çalışıyor.*
>
> *Projenin en öğretici kısmı ilk sürümün hatalarıydı. İlk modelim %99,9 F1 veriyordu. Şüphelendim, test markalarını eğitimden tamamen ayırdım ve F1 0,20'ye düştü: sentetik veride rastgele split şablon sızıntısı yaratıyordu. Değerlendirmeyi grup bazlı ayrım ve elle yazılmış bir altın setle yeniden kurdum; bugünkü dürüst rakam altın sette 0,83. Benzer şekilde kümeleme etiketlerimin altısının da yanlış olduğunu, tahmin aralığımın %100 kapsadığını — yani işe yaramaz genişlikte olduğunu — testle yakaladım ve düzelttim."*

Bu anlatım üç şey gösterir: uçtan uca sistem kurabildiğini, **kendi sonucundan şüphe ettiğini**, ve düzeltmeyi ölçümle kanıtladığını.

---

## BÖLÜM 2: Faz faz kavramlar ve sorular

### FAZ 1 — Document AI ve gizlilik

**Kavramlar:** Vektörel vs taranmış PDF (taranmış PDF'i sistem *reddeder*; OCR yok, varmış gibi davranmıyoruz) · PDF nesne modeli (eylemler kökte değil; sayfa `/AA`, annotation `/A`, `/Names` ağacı) · açılma bombası · redaksiyon ≠ üstünü örtme (`apply_redactions` içerik akışından siler) · T.C. kimlik ve Luhn sağlamaları · sağlama (checksum).

**Soru:** *"PII'yi neden ayrıştırmadan sonra karartıyorsun? Önce karartmak daha güvenli değil mi?"*
**Cevap:** *"İlk sürümde önce karartıyordum ve ilk sayfanın üst %20'sini kapatıyordum. İki sorun çıktı. Birincisi, dönem borcu ve kartın son 4 hanesi de o bölgedeydi; okuyamadığım için bu alanları sabit kodlamış, sağlamayı `True` döndüren bir stub'a çevirmiştim. İkincisi, %20 varsayımı yalnızca kendi ürettiğim test PDF'lerinde tutuyordu: adresi %24'e kaydırınca ya da 2. sayfaya başlık koyunca isim sızıyordu — döngüsel doğrulama. Şimdi sıra: bellek içinde oku → ayrıştır → başlıktaki borçla işlemlerin toplamını Decimal olarak karşılaştır → sonra tüm sayfalarda kelime koordinatlarıyla PII'yi bul ve sil. Ham metin tek fonksiyonun dışına çıkmıyor. Testte 8 farklı yerleşimde ham içerik akışlarına kadar sızıntı arıyorum."*

**Soru:** *"Bu sistem KVKK / PCI-DSS uyumlu mu?"*
**Cevap:** *"Hayır, ve öyle olduğunu iddia etmiyorum. PCI-DSS kart verisi saklayan/işleyen ortamlar içindir; ben saklamıyorum, yalnızca açık PAN görürsem Luhn ile doğrulayıp maskeliyorum. KVKK ise kodla değil süreçle sağlanır: aydınlatma metni, rıza, saklama politikası. Hatta fark ettiğim bir çelişki var: uygulamayı ücretsiz yurt dışı bir buluta kurup gerçek ekstre yüklemek md. 9 kapsamında yurt dışına aktarım olur. Bu yüzden canlı demo yalnızca sentetik veriyle çalışır; gerçek ekstre için yerel çalıştırma öneriyorum ve arayüzde bunu yazıyorum."*

**Soru:** *"Sanitizer'ın tehdit modeli ne?"*
**Cevap:** *"Dürüst olmak gerekirse sunucuda PDF JavaScript'i zaten çalışmaz; bu temizlik, karartılmış PDF'i sonradan açacak kişiyi korur. Sunucu tarafındaki gerçek risk ayrıştırıcı zafiyetleri ve kaynak tüketimi. Boyut, sayfa ve — sınırlı `zlib` açılımıyla — açılmış boyut limiti koydum; 300 KB'lık dosyanın 300 MB'a açıldığı bombayı belleğe almadan reddediyorum. Eksik olan süreç yalıtımı: üretimde ayrıştırıcıyı bellek limitli, ağsız ayrı bir süreçte çalıştırırdım. Bunu `SECURITY.md`'de açıkça yazdım."*

### FAZ 2 — Türkçe POS NLP ve XAI

**Kavramlar:** Şablon sızıntısı ve `GroupShuffleSplit` · altın set · abstain / seçici sınıflandırma (coverage vs accuracy) · kalibrasyon (ECE) · Unicode NFC · diakritik katlama · `char_wb` n-gram · SHAP toplanabilirliği.

**Soru:** *"%99,9'u nasıl ölçtün?"* (İlk sürümde sorulacak ilk soru buydu.)
**Cevap:** *"Yanlış ölçmüşüm. Veri ~200 marka şablonundan üretildiği için rastgele split aynı markayı iki tarafa da koyuyordu; test satırlarının %35'i eğitimde birebir vardı. Markaları gruplayıp ayırınca F1 0,20'ye düştü. Şimdi üç soruyu ayrı ölçüyorum: görülmüş marka (0,98 — iyimser üst sınır), görülmemiş işyeri (0,72) ve elle yazdığım 170 satırlık altın set (0,83 — manşet). Görülmemiş işyeri kırılımı asıl içgörüyü veriyor: 'ÖZKAN ECZANESİ' gibi anahtar kelimeli esnafta 0,98, ama anahtar kelimesi olmayan yeni bir zincir markada 0,50. Bu bir model sorunu değil bilgi sorunu — 'ZARA' kelimesinden giyim çıkarılamaz. Çözüm daha büyük model değil; işyeri sözlüğü ya da kullanıcı geri bildirimi. Model bu durumda 'Belirsiz' demeyi öğrendi."*

**Soru:** *"Neden BERT değil de LightGBM?"*
**Cevap:** *"POS metni cümle değil; `MGRS TR GIDA 042` gibi kırpılmış kodlar. Karakter n-gram'ları buna dayanıklı, model 2 MB ve tekil çıkarım 0,6 ms. BERT'i ölçmedim, o yüzden hakkında rakam vermem. Ölçtüğüm şey şu: aynı özniteliklerle Logistic Regression, LightGBM ile başa baş çıktı — 0,83'e 0,81, 170 satırda dört satır fark — ve daha hızlı. Yani LightGBM'i doğruluk için seçtiğimi söyleyemem; etkileşimleri yakalaması ve TreeExplainer hattı için tuttum. Sadeleştirmek isteseydim doğrusal modele geçerdim; orada SHAP kapalı formda."* ← Bu cevap, "en iyi model benimki" demekten çok daha güçlüdür.

**Soru:** *"I-folding nedir, neden yetmedi?"*
**Cevap:** *"Python'da `'I'.lower()` `'i'` verir ama Türkçede `'ı'` olmalı; `MIGROS` ile `MİGROS` farklı n-gram üretir. İlk sürümde yalnızca ı/i'yi eşitlemiştim. Ama eğitim verim ASCII'ydi: `KOÇTAŞ` yazınca model hiç görmediği 'ç', 'ş' n-gram'larıyla Restoran diyordu. Altı çiftin tamamını katladım, NFC normalizasyonu ekledim — PDF'ler bazen 'İ'yi 'I' + birleştirici nokta olarak veriyor. İronik biçimde aynı hata sohbet modülümde de vardı: `question.lower()` yüzünden 'GIDA' sorusu tanınmıyordu."*

**Soru:** *"SHAP çıktına neden güveneyim?"*
**Cevap:** *"Çünkü toplanabilirliği test ediyorum: taban + aktif n-gram'lar + metinde olmayan n-gram'lar, modelin ham skoruna 1e-4 hassasiyetle eşit. İlk sürümde iki hata vardı: n-gram'ı kelimeye alt dize aramasıyla bağlıyordum — 'ar' her kelimede tekrar sayılıyordu — ve metinde olmayan n-gram'ların katkısını atıyordum; bir örnekte atfın %80'i oradaydı. Ağaç modelinde bir n-gram'ın yokluğu da karardır; bunu gizlemek açıklamayı yanıltıcı yapar."*

### FAZ 3 — Tahmin ve anomali

**Kavramlar:** Walk-forward (genişleyen pencere) · doğrulanan model = kullanılan model · tahmin aralığı vs güven aralığı · aralıkların toplanamazlığı (√n ölçeklemesi) · blok bootstrap · ampirik kapsama · medyan/MAD ve maskeleme etkisi · seyrek (intermittent) seriler · RMSE→ortalama, MAE→medyan.

**Soru:** *"Neden Prophet ya da LSTM değil?"*
**Cevap:** *"Tek ekstre 30 veri noktası; bununla LSTM eğitmek anlamsız. Ama asıl cevap model seçimi değil, problemi doğru ayrıştırmak: kredi kartında gelecek ayın borcunun bir kısmı zaten biliniyor. Ekstrede '3/6 taksit' yazıyorsa 4. taksit kesindir; Netflix her ay gelir. Bunları zaman serisine tahmin ettirmek bilgiyi çöpe atmak. Tahminim: bilinen taksitler + tespit edilen abonelikler + yalnızca kalan isteğe bağlı harcama için istatistiksel model. O model de walk-forward ile ortalama, haftanın-günü ortalaması ve Holt-Winters arasından seçiliyor; Holt-Winters ancak 8 hafta veri varsa aday."*

**Soru:** *"%95 aralığını nasıl hesapladın?"*
**Cevap:** *"İlk sürümde günlük üst sınırları toplamıştım. Bu, 30 günün hatasının aynı yönde olduğunu varsayar; bandı yaklaşık √30 kat şişirir. Simülasyonla ölçtüm: kapsama %100, bant beklenenin +%69'u — yani hiçbir şey söylemeyen bir aralık. Şimdi artıkları 7 günlük bloklarla yeniden örnekleyip 30 günlük toplamı simüle ediyorum; bant +%10, ampirik kapsama %92. Nominalin biraz altında çünkü artıklar örneklem içi; bunu da belgeledim. Kapsama testi CI'da koşuyor."*

**Soru:** *"Model seçim ölçütün ne, neden?"*
**Cevap:** *"Günlük RMSE. Önce 14 günlük toplam hatayı denedim ama bir testim patladı: iki tam haftada haftalık mevsimsellik toplamda sıfırlanıyor, ölçüt mevsimsel modeli ödüllendiremiyor. MAE'yi de seçmedim: MAE'yi minimize eden tahmin medyandır; harcamasız günlerin çok olduğu seyrek seride medyan sıfıra yakındır ve aylık toplamı düşük tahmin ederdi. RMSE ortalamayı hedefler, toplam için yansızdır."*

**Soru:** *"Anomali tespitinde neden yalnızca Isolation Forest değil?"*
**Cevap:** *"İlk sürümde `contamination=0.08` vardı; bu 'her ekstrenin %8'i anomalidir' demek. Tamamen normal veri ürettim, yine 5 işlem işaretledi — 129 TL'lik alışverişe 'örüntü dışı' dedi. İkinci sorun maskeleme: ortalama ve standart sapmayı aykırı değerin kendisi şişirir; 6 işlemde 25.000 TL'nin klasik z-skoru en fazla 2,04 olabilir, hiçbir eşiği geçemez. Medyan/MAD'a geçtim, log tutarda, yalnızca yüksek yön. Üstüne alan kuralı: aynı şubeden 3 gün içinde aynı tutar = olası mükerrer çekim. Isolation Forest hâlâ var ama destekleyici: n ≥ 30 ise çalışır, tek başına işaretleyemez. Temiz ekstrelerde yanlış alarm %0,5'e indi."*

### FAZ 4 — Kaldırılan modül (bunu da anlatabilirsin)

**Soru:** *"Projeden bir şey çıkardın mı?"*
**Cevap:** *"Evet, K-Means + PCA ile çizilen 3B 'harcama galaksisi' sayfasını sildim. İki nedenle: popülasyon
sentetikti — kümeleri ben 6 prototip etrafında üretip K-Means'e 'buldurmuştum', yani doğrulama döngüseldi; ve
kullanıcıya hiçbir eyleme dönüşmeyen bir görselden ibaretti. Silmeden önce içindeki gerçek hatayı bulup
düzeltmiştim, ama doğru olmak ile faydalı olmak aynı şey değil. Bir portföyde, kendi dokümanı 'bu gerçek bir
bulgu değil' diyen bir modülü taşımak zarardır."*

**Silmeden önce bulduğum hata (kavram olarak hâlâ anlatmaya değer):**

**Soru:** *"K-Means çıktısını nasıl etiketledin?"*
**Cevap:** *"İlk sürümde `ISIMLER[kume_id]` yazmıştım. K-Means'in küme numaraları keyfidir; 6 prototip profil verip kontrol ettiğimde altısının da yanlış etiketlendiğini gördüm — kahve profili 'Taksit Mimarı' çıkıyordu. Testim yalnızca `'cluster_id' in sonuç` diye baktığı için yakalayamamıştı. Şimdi küme merkezlerini prototip vektörleriyle ölçeklenmiş uzayda karşılaştırıp Macar algoritmasıyla bire bir eşliyorum; 4 farklı tohumda 6/6."*
**Dürüst not:** *"Popülasyon sentetik — 6 prototip etrafında ürettim, K-Means'in onları bulması döngüsel. Bunu gerçek bir segmentasyon bulgusu olarak sunmuyorum; hattın doğru kurulduğunun gösterimi. Gerçek veriyle k'yi silhouette ile yeniden seçerdim."*

### FAZ 5 — Copilot

**Soru:** *"Bu bir RAG / LLM ajanı mı?"*
**Cevap:** *"Hayır. İlk sürümde klasörün adı `rag`'di ama içinde retrieval da LLM de yoktu; beş `elif`'ti. Adını değiştirdim. Mimari üç katman: Türkçe katlamayla normalleştiren bir niyet yönlendirici, sayıları üreten saf Pandas araçları ve şablon. Garanti ettiğim şey testli: yanıttaki her TL tutarı araç çıktısında var; şablon katmanı aritmetik yapmıyor. Anlamadığı soruda ya da 'geçen aya göre' gibi verisi olmayan soruda tahmin yürütmüyor, söylüyor. Bu, endüstrideki 'LLM yönlendirir, kod hesaplar' kalıbının LLM'siz hali; yönlendiriciyi bir LLM ile değiştirsem araçlar ve garanti aynı kalır."*

---

## BÖLÜM 3: "En büyük hatan neydi?" sorusu için hazır cevaplar

Bu proje bu soruya verilebilecek en iyi malzemeyi içerir. Hepsi [`PROJE_AMACI_VE_DEGERLENDIRME.md`](PROJE_AMACI_VE_DEGERLENDIRME.md) Bölüm 2'de kanıtıyla listelidir:

| Hata | Ders |
| :-- | :-- |
| %99,9 F1 → aslında 0,20 | Fazla iyi sonuç bir başarı değil, bir uyarıdır. Ayrımı verinin üretim sürecine göre yap. |
| 6/6 yanlış küme etiketi, testler yeşil | "Çökmüyor" testi davranışı doğrulamaz. Beklenen **değeri** assert et. |
| %100 kapsayan güven aralığı | Bir aralığın doğruluğu kapsamasıyla, **faydası** genişliğiyle ölçülür; ikisini birlikte test et. |
| Sabit kodlanmış `checksum_valid=True` | Yapamadığın şeyi `None` ile söyle; sahte başarı, eksik özellikten kötüdür. |
| Test PDF'lerinin hepsi aynı şablon | Test verisini, test ettiğin varsayımı bilen kişi üretirse doğrulama döngüsel olur. |
| Bozuk harfleri gizleyen "font onarımı" | Belirtiyi yamamak kök nedeni saklar (üretici İ/Ş/Ğ'yi `·` basıyordu). |

---

## BÖLÜM 4: Çalıştırma

```bash
make install && make train   # kilitli bağımlılıklar, sızıntısız eğitim + metrik JSON
make check                   # ruff + 272 test + kapsama kapısı
make run                     # arayüz
```

Mülakattan önce `make train` çıktısını bir kez oku: söyleyeceğin her rakam orada.

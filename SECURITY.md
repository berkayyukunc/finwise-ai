# Güvenlik, Gizlilik ve Uyum Notları

Bu doküman sistemin **neyi koruduğunu, neyi korumadığını** ve hangi uyum iddialarında **bulunmadığını** açıkça yazar.

## 1. Kapsam ve dürüst konumlandırma

FinWise-AI bir **portföy / araştırma projesidir**; bir banka ürünü ya da ödeme sistemi bileşeni değildir.

- **PCI-DSS:** Sistem kart verisi (PAN) saklamaz, işlemez veya iletmez; PCI-DSS kapsamındaki bir "kart verisi ortamı" değildir ve
  PCI-DSS uyumlu olduğu iddia edilmez. Yapılan şey derinlemesine savunmadır: ekstrede açık bir PAN görülürse Luhn sağlamasıyla
  doğrulanır ve ilk 4 + son 4 hane dışında maskelenir (PCI-DSS Req. 3.4.1'in izin verdiği azami gösterimin — ilk 6/son 4 — altında).
  Türk bankalarının ekstreleri PAN'ı zaten maskeli basar; bu katman istisnai durumlar içindir.
- **KVKK:** Veri minimizasyonu (md. 4) mühendislik düzeyinde uygulanır, ancak KVKK uyumu yalnızca kodla sağlanmaz: aydınlatma metni,
  açık rıza, veri işleme envanteri, saklama/imha politikası ve veri sorumlusu kaydı gerektirir. Bunlar bu repoda **yoktur**.
- **Yurt dışına aktarım (KVKK md. 9):** Uygulamayı Hugging Face Spaces / Streamlit Cloud gibi yurt dışı sunuculu bir ortama kurup
  gerçek ekstre yüklemek, kişisel verinin yurt dışına aktarımıdır. "0 TL bulut barındırma" ile "gerçek kişisel veri işleme" aynı
  anda savunulamaz. Bu nedenle: **bulut kurulumu yalnızca sentetik örneklerle demo içindir; gerçek ekstre yalnızca yerelde
  (`streamlit run app.py` / `docker run`) işlenmelidir.** Arayüz bu uyarıyı kullanıcıya gösterir.

## 2. Veri akışı ve saklama

| Veri | Nerede yaşar | Ömrü |
| :-- | :-- | :-- |
| Ham PDF baytları | Yalnızca istek belleği (`process_pdf` yerel değişkeni) | Fonksiyon dönünce serbest kalır; diske yazılmaz |
| Ham sayfa metni (PII içerir) | `process_pdf` içinde | Fonksiyon dışına çıkmaz |
| Öğrenilen kart sahibi adı | `process_pdf` içinde, maskeleme için | Loglanmaz, `ParsedStatement`'a yazılmaz |
| Maskelenmiş işlemler + özet | Streamlit `session_state` | Tarayıcı oturumu boyunca |
| Loglar | stdout | Yalnızca sayaçlar (işlem sayısı, PII bulgu sayısı); içerik loglanmaz |

Üçüncü taraf çağrısı yoktur: LLM API'si, analitik, harici görsel/CDN isteği yapılmaz (`--browser.gatherUsageStats=false`).

## 3. Tehdit modeli

| Tehdit | Önlem | Kalan risk |
| :-- | :-- | :-- |
| Aktif içerikli PDF (JavaScript, Launch, gömülü dosya, XFA) | Tüm nesne ağacında tarama ve sökme (`sanitizer.py`); testler `tests/test_security.py` | Bilinmeyen yeni eylem türleri. Not: sunucuda JS zaten çalışmaz; bu önlem, karartılmış PDF'i sonradan bir okuyucuda açan kişiyi korur |
| Açılma bombası (decompression bomb) | Akışlar `zlib.decompressobj` ile **sınırlı** açılır: akış başına 50 MB, toplam 150 MB | Flate dışı filtreler (LZW, JBIG2) ham boyutla sayılır |
| Aşırı büyük / çok sayfalı dosya | 15 MB ve 30 sayfa tavanı | — |
| Ayrıştırıcı (MuPDF / QPDF / pdfminer) zafiyeti | Sürümler kilitli; girdi önce QPDF'ten geçip yeniden yazılır | **Süreç düzeyi yalıtım bu repoda yok.** Üretimde: ayrı süreç + bellek/CPU limiti + zaman aşımı + seccomp/gVisor, salt-okunur dosya sistemi, ağsız konteyner |
| Açıklama metni üzerinden XSS | `unsafe_allow_html` yalnızca `html.escape`'ten geçmiş içerikle kullanılır (`explainer.render_html`); test var | — |
| Pickle ile kod çalıştırma | Yalnızca kendi ürettiğimiz model dosyası yüklenir; kullanıcıdan model kabul edilmez | Model dosyasına yazma erişimi olan saldırgan |
| Kötü amaçlı şifreli PDF / şifre kaba kuvveti | Şifre yalnızca kullanıcı girdisinden alınır, saklanmaz | — |

## 4. PII karartma: ne yakalanır, ne yakalanmaz

**Yakalanan (tüm sayfalarda, içerik akışından silinerek):** etiketli ad/adres/TC/müşteri no/telefon/e-posta alanları; sağlaması
tutan T.C. kimlik no; Luhn geçerli PAN; TR IBAN; e-posta; cep telefonu; öğrenilen kart sahibi adının belgedeki tüm tekrarları;
HAVALE/EFT/FAST satırlarındaki karşı taraf adları; PDF `/Info` ve XMP metadata. İlk sayfada hiçbir isim etiketi bulunamazsa ilk işlem
satırının üstündeki bölge tümüyle karartılır (emniyet supabı).

**Yakalanmayan (bilinen sınırlar):**
- Etiketsiz, serbest metin içindeki kişi adları (ör. şahıs adıyla kayıtlı işyeri: `AHMET YILMAZ KUYUMCULUK`). Çözüm: Türkçe NER — yol haritasında.
- Görüntü olarak gömülü PII (logo içine basılmış isim, taranmış sayfa). Taranmış PDF'ler işlenmez, açıkça reddedilir (OCR yok).
- İşyeri adı + tarih + tutar kombinasyonunun kendisi de dolaylı olarak kişiyi tanımlayabilir; maskelenmiş çıktı **anonim değil, takma adlıdır (pseudonymous)**.
- Profiller gerçek banka ekstreleriyle doğrulanmamıştır (bkz. `src/document_ai/bank_parsers/banks.py`).

## 5. Zafiyet bildirimi

Bir güvenlik sorunu bulursanız lütfen herkese açık issue açmak yerine depo sahibine özelden bildirin.

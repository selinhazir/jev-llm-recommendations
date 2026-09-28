# Fast, Safe, Personalized Recommendations: Jev + LLM

## Proje nedir?
Belirli bir firmaya özel olmayan, genel bir yaklaşımın demosu. Sunum İngilizce yapılacak;
veri seti "sample venue catalog (Istanbul)" olarak tanımlanır. Demo şunu göstermeyi amaçlar:

1. Mekânları, **Jev (TypeSafe)** ile kullanıcı türlerine göre **önceden** etiketlemek
   (ör. "Bu mekân tekerlekli sandalye kullanıcısına uygun mu?" → evet/hayır + olasılık).
2. Gezi planı istendiğinde bu etiketlerle mekânları **filtrelemek**.
3. LLM'e yalnızca uygun mekânları göndererek **token ve maliyeti düşürmek**.

## Klasör yapısı
- `data/istanbul_mekanlar.json` — sample venue catalog (Istanbul), 100 mekân
  (id, isim, kategori, semt, fiyat_seviyesi 1-4, aciklama, acilis_saatleri).
  İsim ve semt Türkçe; kategori, açıklama ve saatler İngilizce. Alan adları Türkçe kalır,
  Jev'e/LLM'e giderken İngilizce adlara çevrilir (name, category, description...).
- `data/kullanici_turleri.json` — 12 kullanıcı türü (id, tip, ad, aciklama).
  `tip: "kisit"` → sert filtre ("ciddi engel var mı?", yüksekse elenir);
  `tip: "ilgi"` → sıralama puanı ("keyif alır mı?")
- `etiketle.py` — Jev ile etiketleme (mekân başına 1 çağrı, 12 soru; sorular İngilizce).
  Cache, Jev'e giden girdinin hash'iyle çalışır: soru/açıklama değişince otomatik yeniden etiketler.
- `data/etiketler.json` — Jev sonuçları (erisim_cevaplari, engel_olasiliklari, ilgi_olasiliklari,
  token sayıları, süre)
- `filtrele.py` — kısıt kuralları tek yerde: kod kuralları (bütçe), erişilebilirlik choice'u
  (yaşlı, tekerlekli sandalye), noul eşikleri (evcil hayvan, küçük çocuk)
- `ozet.py` — sunum özeti: tür başına kalan/elenen, veri eksikliği raporu, token/maliyet/süre

- `rota.py` / `karsilastir.py` / `sira_testi.py` — yerel LLM (Ollama qwen2.5:3b) ile rota ve
  filtresiz/filtreli karşılaştırma
- `app.py` — Streamlit sunum arayüzü (İngilizce): `.venv/bin/streamlit run app.py`
  (Streamlit `.venv` içinde; sistem Python'u 3.9, bkz. requirements.txt)
- `dogruluk.py` + `data/altin_etiketler.json` — 20 mekânlık referans etiketlerle doğruluk ölçümü (referans etiketler bir LLM (Claude) tarafından, sadece Türkçe açıklamalara bakarak, Jev sonuçlarını görmeden oluşturuldu)
- Firmalar arası paket: `prompt_paketi.py` (prompts/ klasörüne A/B/C prompt dosyaları + manifest),
  `kaydet.py` (başka servislerin cevaplarını data/vendor_results.json'a girer),
  `vendor_rapor.py` (liste kontrolü, ihlal, alaka, maliyet; fiyatlar FIYATLAR sözlüğünde)
- `sunum_ozeti.py` — sunum için tüm güncel rakamlar tek yerde → `data/sunum_ozeti.json`
- `backup_tr/` — Türkçe sürümün yedeği (kod, veri, sonuçlar)

İlke: Jev yalnızca açıklama metninden karar çıkarmak için kullanılır; sayısal/kategorik
alanlar (ör. fiyat_seviyesi) kodla filtrelenir.

> Not: Veri seti demo amaçlıdır. Mekânlar gerçek olsa da açılış saatleri, fiyat seviyeleri ve
> açıklamalar yaklaşıktır; güncel bilgi olarak kullanılmamalıdır.

## Kurallar (çok önemli)
- **`.env` dosyasını asla okuma, açma veya içeriğini yazdırma.** İçinde `TYPESAFE_API_KEY` var.
  Kod bu anahtarı ortam değişkeni olarak okumalı: `os.environ["TYPESAFE_API_KEY"]`.
- `.env` her zaman `.gitignore` içinde kalmalı.
- Kullanıcı Python'a yeni başlıyor: kod sade olmalı ve **Türkçe yorum satırlarıyla** açıklanmalı.
- Her adımdan sonra ne yapıldığını basitçe açıkla ve kullanıcının onayını bekle.
- **Bütçe çok kısıtlı:** Jev çağrılarını önce 3-5 mekânla test et; sonuçlar doğruysa
  ve kullanıcı onaylarsa tüm mekânlarla çalıştır. Toplu çalıştırmadan önce yaklaşık çağrı
  sayısını söyle (100 mekân × 12 tür = 1200 yargı).

"""
Jev (TypeSafe) ile mekânları kullanıcı türlerine göre etiketler.

- Her mekân için TEK bir API çağrısı yapılır; içinde her kullanıcı türü için bir
  "noul" (evet/hayır olasılığı) sorusu bulunur. Jev bunları paralel cevaplar.
- Kodla filtrelenen türler (ör. bütçe, bkz. constraints.py) Jev'e sorulmaz.
- Sonuçlar data/etiketler.json dosyasına kaydedilir.
- Cache: Jev'e gidecek girdinin (sorular + mekân bilgisi) hash'i saklanır. Girdi
  değişmediyse mekân tekrar etiketlenmez; değiştiyse otomatik olarak yeniden etiketlenir.

Kullanım:
    python3 label.py             -> en fazla 3 yeni mekân etiketler (test için)
    python3 label.py 10          -> en fazla 10 yeni mekân etiketler
    python3 label.py hepsi       -> etiketlenmemiş tüm mekânları etiketler
    python3 label.py --id 12,51  -> sadece bu id'lere sahip mekânları etiketler

API anahtarı TYPESAFE_API_KEY ortam değişkeninden okunur.
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

from constraints import ERISILEBILIRLIK_TURLERI, KOD_KURALLARI, kurallari_kontrol_et

# --- Ayarlar ---
API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
MEKAN_DOSYASI = "data/istanbul_mekanlar.json"
TUR_DOSYASI = "data/kullanici_turleri.json"
ETIKET_DOSYASI = "data/etiketler.json"
DOLAR_PER_MILYON_INPUT_TOKEN = 0.042  # Jev fiyatı: 1 milyon input token = 0.042 $

# Jev'e gönderilecek mekân alanları (semt, saatler, id gibi alanlar gönderilmez).
# Sol: veri dosyasındaki alan adı, sağ: Jev'e giden İngilizce alan adı.
GONDERILECEK_ALANLAR = {
    "isim": "name",
    "kategori": "category",
    "fiyat_seviyesi": "price_level",
    "aciklama": "description",
}

# Erişilebilirlik choice sorusunun seçenekleri. Seçenek adları modele gönderildiği
# için İngilizce; kod içinde (constraints.py, accuracy.py) Türkçe değerlerle çalışıyoruz.
ERISIM_SECENEKLERI = {
    "barrier_present": "engel_var",
    "no_barrier": "engel_yok",
    "no_information": "bilgi_yok",
}


def json_oku(dosya_yolu, varsayilan=None):
    """JSON dosyasını okur. Dosya yoksa 'varsayilan' değerini döndürür."""
    if not os.path.exists(dosya_yolu):
        return varsayilan
    with open(dosya_yolu, encoding="utf-8") as f:
        return json.load(f)


def json_yaz(dosya_yolu, veri):
    """Veriyi okunaklı (girintili) JSON olarak dosyaya yazar."""
    with open(dosya_yolu, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)


def kisit_sorusu(tur):
    """Kısıt türü için soru: mekânda bu kişi için ciddi bir engel var mı?

    Olasılık constraints.py'deki eşiği geçerse mekân bu kullanıcıdan elenir.
    """
    return {
        "type": "noul",
        "instructions": f"Does this venue involve a serious barrier or problem for a '{tur['ad']}'?",
        "criteria": {
            "true": f"Yes, there is a serious barrier or problem. The person and what counts as a problem for them: {tur['aciklama']}",
            "false": "No, there is no serious barrier; this person can visit the venue without problems.",
        },
    }


def erisim_sorusu(tur):
    """Erişilebilirlik türleri (yaşlı, tekerlekli sandalye) için choice sorusu.

    Noul'dan farkı: "engel var" ile "açıklamada bilgi yok" durumlarını ayırır.
    Böylece bilgisi eksik mekânları elemek yerine uyarıyla gösterebiliriz.
    """
    return {
        "type": "choice",
        "instructions": f"Based on the venue's description, is this venue accessible for a '{tur['ad']}'?",
        "criteria": {
            "barrier_present": (
                "The description mentions a concrete barrier for this person (e.g. stairs, steep "
                "slopes, cobblestone, long walks). This option applies even if accessibility "
                f"information is mentioned alongside a barrier. The person and what counts as a barrier for them: {tur['aciklama']}"
            ),
            "no_barrier": (
                "The description includes accessibility information for this person (e.g. ramp, "
                "elevator, flat ground, fully accessible) and mentions no concrete barrier."
            ),
            "no_information": (
                "The description gives no information about accessibility: it mentions neither "
                "a concrete barrier nor accessibility information."
            ),
        },
    }


def ilgi_sorusu(tur):
    """İlgi türü için soru: bu kişi mekândan keyif alır mı?

    Olasılık sıralama puanı olarak kullanılacak (yüksek = daha üstte).
    """
    return {
        "type": "noul",
        "instructions": f"Would a '{tur['ad']}' enjoy this venue?",
        "criteria": {
            "true": f"Yes, they would enjoy it. The person and what they like: {tur['aciklama']}",
            "false": "No, the venue does not match this person's interests or needs.",
        },
    }


def alt_soru(alt):
    """Bir ilgi türünün alt sorusu (ör. yalnız gezgin -> güvenli mi?, sosyal mi?)."""
    return {
        "type": "noul",
        "instructions": alt["soru"],
        "criteria": {"true": alt["evet"], "false": alt["hayir"]},
    }


def alt_soru_id(tur, alt):
    """Alt soru kimliği, ör. 'yalniz_gezgin__sosyal'."""
    return f"{tur['id']}__{alt['id']}"


def sorulari_hazirla(kullanici_turleri):
    """Jev'e sorulacak türler için, tipine göre uygun soruyu oluşturur.

    - kodla filtrelenen kısıt   -> soru yok
    - erişilebilirlik kısıtı     -> choice (engel_var / engel_yok / bilgi_yok)
    - diğer kısıtlar             -> noul ("ciddi engel var mı?")
    - alt soruları olan ilgi türü -> her alt soru için bir noul
    - diğer ilgi türleri         -> noul ("keyif alır mı?")

    Soru kimlikleri (ör. "yasli") modele gönderilmez; bu yüzden türün adını
    ve açıklamasını sorunun metnine ve kriterine açıkça yazıyoruz.
    """
    sorular = {}
    for tur in kullanici_turleri:
        if tur["id"] in KOD_KURALLARI:
            continue  # Bu tür kodla filtreleniyor, Jev'e sormuyoruz
        if tur["tip"] == "kisit" and tur["id"] in ERISILEBILIRLIK_TURLERI:
            sorular[tur["id"]] = erisim_sorusu(tur)
        elif tur["tip"] == "kisit":
            sorular[tur["id"]] = kisit_sorusu(tur)
        elif "alt_sorular" in tur:
            for alt in tur["alt_sorular"]:
                sorular[alt_soru_id(tur, alt)] = alt_soru(alt)
        else:
            sorular[tur["id"]] = ilgi_sorusu(tur)
    return sorular


def girdi_hash(state, sorular):
    """Jev'e gidecek her şeyden kısa bir parmak izi (hash) üretir.

    Soru metinleri, tür açıklamaları veya mekânın gönderilen alanlarından biri
    değişirse hash de değişir ve cache o mekânı yeniden etiketler.
    sort_keys=True: aynı içerik her zaman aynı hash'i versin diye.
    """
    metin = json.dumps({"model": MODEL, "state": state, "sorular": sorular},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(metin.encode("utf-8")).hexdigest()[:12]


def jev_cagir(api_anahtari, mekan_state, sorular):
    """Jev API'sine tek bir istek gönderir ve cevabı sözlük olarak döndürür."""
    govde = {"model": MODEL, "state": mekan_state, "questions": sorular}
    istek = urllib.request.Request(
        API_URL,
        data=json.dumps(govde).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_anahtari}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(istek, timeout=60) as cevap:
        return json.loads(cevap.read().decode("utf-8"))


def main():
    # 1) Komut satırı: kaç mekân veya hangi mekânlar etiketlenecek?
    #    Varsayılan: en fazla 3 mekân (bütçe dostu test).
    secili_idler = None
    limit = 3
    if len(sys.argv) > 2 and sys.argv[1] == "--id":
        secili_idler = [parca.strip() for parca in sys.argv[2].split(",")]
        limit = None
    elif len(sys.argv) > 1:
        limit = None if sys.argv[1] == "hepsi" else int(sys.argv[1])

    # 2) Verileri oku. etiketler.json yoksa boş sözlükle başla.
    mekanlar = json_oku(MEKAN_DOSYASI)
    kullanici_turleri = json_oku(TUR_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI, varsayilan={})
    kurallari_kontrol_et(kullanici_turleri)
    sorular = sorulari_hazirla(kullanici_turleri)

    if secili_idler is not None:
        mekanlar = [m for m in mekanlar if str(m["id"]) in secili_idler]

    # 3) Cache: kayıtlı hash, şu anki girdinin hash'iyle aynıysa mekânı atla.
    bekleyenler = []
    for mekan in mekanlar:
        state = {ingilizce: mekan[alan] for alan, ingilizce in GONDERILECEK_ALANLAR.items()}
        kayit = etiketler.get(str(mekan["id"]))
        if kayit is None or kayit.get("girdi_hash") != girdi_hash(state, sorular):
            bekleyenler.append((mekan, state))
    if limit is not None:
        bekleyenler = bekleyenler[:limit]
    print(f"{len(sorular)} Jev sorusu. Seçilen {len(mekanlar)} mekândan "
          f"{len(bekleyenler)} tanesi etiketlenecek (gerisi cache'te güncel).\n")
    if not bekleyenler:
        return  # Yapılacak iş yok, API'ye hiç bağlanmıyoruz

    # 4) API anahtarını ortam değişkeninden al (anahtar asla ekrana yazdırılmaz)
    api_anahtari = os.environ.get("TYPESAFE_API_KEY")
    if not api_anahtari:
        print("HATA: TYPESAFE_API_KEY ortam değişkeni bulunamadı.")
        sys.exit(1)

    bu_calistirma_token = 0
    for mekan, state in bekleyenler:
        try:
            baslangic = time.perf_counter()  # Çağrının ne kadar sürdüğünü ölçmek için
            cevap = jev_cagir(api_anahtari, state, sorular)
            sure_sn = time.perf_counter() - baslangic
        except urllib.error.HTTPError as hata:
            print(f"HATA ({mekan['isim']}): HTTP {hata.code} - {hata.read().decode('utf-8')}")
            break  # Hata varsa durup kredi harcamaya devam etmiyoruz
        except urllib.error.URLError as hata:
            print(f"BAĞLANTI HATASI ({mekan['isim']}): {hata.reason}")
            break

        # Cevapları türün çeşidine göre ayrı sözlüklere ayır:
        #   erisim_cevaplari     : choice seçimi + confidence (yaşlı, tekerlekli sandalye)
        #   engel_olasiliklari   : noul, constraints.py'deki eşiği geçerse mekân elenir
        #   ilgi_olasiliklari    : sıralama puanı, yüksekse daha üstte
        #   ilgi_alt_olasiliklari: alt soruların ayrı puanları (ortalaması ilgi puanıdır)
        cevaplar = cevap["answers"]
        erisim_cevaplari = {}
        engel_olasiliklari = {}
        ilgi_olasiliklari = {}
        ilgi_alt_olasiliklari = {}
        for tur in kullanici_turleri:
            if tur["id"] in KOD_KURALLARI:
                continue  # Kodla filtrelenen tür, Jev cevabı yok
            if tur["tip"] == "kisit" and tur["id"] in ERISILEBILIRLIK_TURLERI:
                c = cevaplar[tur["id"]]
                erisim_cevaplari[tur["id"]] = {
                    "secim": ERISIM_SECENEKLERI[c["choice"]],  # İngilizce seçenek -> Türkçe değer
                    "confidence": c["confidence"],
                    "olasiliklar": {ERISIM_SECENEKLERI[k]: v for k, v in c["probabilities"].items()},
                }
            elif tur["tip"] == "kisit":
                engel_olasiliklari[tur["id"]] = cevaplar[tur["id"]]["noul"]
            elif "alt_sorular" in tur:
                alt_puanlar = {
                    alt["id"]: cevaplar[alt_soru_id(tur, alt)]["noul"] for alt in tur["alt_sorular"]
                }
                ilgi_alt_olasiliklari[tur["id"]] = alt_puanlar
                # Sıralama puanı: alt soruların ortalaması
                ilgi_olasiliklari[tur["id"]] = sum(alt_puanlar.values()) / len(alt_puanlar)
            else:
                ilgi_olasiliklari[tur["id"]] = cevaplar[tur["id"]]["noul"]

        input_token = cevap["usage"]["input_tokens"]
        bu_calistirma_token += input_token

        etiketler[str(mekan["id"])] = {
            "isim": mekan["isim"],
            "girdi_hash": girdi_hash(state, sorular),
            "model": cevap["model"],
            "input_tokens": input_token,
            "output_tokens": cevap["usage"]["output_tokens"],
            "sure_sn": round(sure_sn, 3),
            "erisim_cevaplari": erisim_cevaplari,
            "engel_olasiliklari": engel_olasiliklari,
            "ilgi_olasiliklari": ilgi_olasiliklari,
            "ilgi_alt_olasiliklari": ilgi_alt_olasiliklari,
        }
        # Her mekândan sonra kaydet: script yarıda kesilse bile emek kaybolmaz
        json_yaz(ETIKET_DOSYASI, etiketler)
        print(f"✓ {mekan['isim']}  ({input_token} input token, {sure_sn:.2f} sn)")

    # 5) Token ve maliyet özeti
    toplam_token = sum(e["input_tokens"] for e in etiketler.values())
    print(f"\nBu çalıştırma : {bu_calistirma_token} input token "
          f"≈ ${bu_calistirma_token / 1_000_000 * DOLAR_PER_MILYON_INPUT_TOKEN:.6f}")
    print(f"Tüm etiketler : {toplam_token} input token ({len(etiketler)} mekân) "
          f"≈ ${toplam_token / 1_000_000 * DOLAR_PER_MILYON_INPUT_TOKEN:.6f}")


if __name__ == "__main__":
    main()

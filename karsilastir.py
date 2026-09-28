"""
Jev filtresinin faydasını ölçer: 5 profil için iki yöntemi karşılaştırır.

    A) Filtresiz: 100 mekânın tamamı LLM'e gider (Jev bilgisi yok)
    B) Filtreli : Jev ile filtrelenmiş ve sıralanmış ilk 10 mekân gider

Her profil ve yöntem için ölçülenler:
- Modele giden input token sayısı ve süre
- İhlal: seçilen mekân, profilin bir kısıtı tarafından ELENİYOR (Jev etiketlerine göre)
- Uyarılı: seçilen mekân için erişilebilirlik doğrulanmamış (bilgi_yok); ihlal sayılmaz
- Uydurma id: modele gönderilmemiş bir id

    python3 karsilastir.py

Sonuçlar data/karsilastirma_sonuclari.json dosyasına da kaydedilir.
"""

from etiketle import ETIKET_DOSYASI, MEKAN_DOSYASI, json_oku, json_yaz
from filtrele import ELENIR, UYARI, filtre_karari
from rota import (ILK_KAC_MEKAN, LLM_MODELI, PROFILLER, filtrele_ve_sirala, llm_icin_mekan,
                  llm_rota_iste, uydurma_idleri_bul)

SONUC_DOSYASI = "data/karsilastirma_sonuclari.json"

# Maliyet varsayımları (yaklaşık!). vendor_rapor.py'deki FIYATLAR["Claude | Claude Opus 5.5"]
# ile aynı input fiyatı; burada sadece input token'lar hesaba katılıyor (basitleştirme).
OPUS_DOLAR_PER_MILYON_INPUT = 4.0  # Claude Opus 5.5, milyon input token başına
GUNLUK_ISTEK = 10_000
AYDAKI_GUN = 30


def aylik_maliyet(istek_basina_token):
    """Günde GUNLUK_ISTEK istek için aylık input token maliyeti (dolar)."""
    return istek_basina_token * GUNLUK_ISTEK * AYDAKI_GUN / 1_000_000 * OPUS_DOLAR_PER_MILYON_INPUT


def rotayi_denetle(rota, gonderilen_idler, profil, mekan_sozlugu, etiketler):
    """Rotadaki her mekânı profilin kısıtlarına göre denetler.

    Dönen sözlük:
      ihlaller : [(mekan, [nedenler]), ...]  en az bir kısıt mekânı eliyor
      uyarililar: [mekan, ...]               elenmiyor ama erişilebilirlik doğrulanmamış
      uydurmalar: [id, ...]                  modele gönderilmemiş id'ler
    """
    ihlaller, uyarililar = [], []
    uydurmalar = uydurma_idleri_bul(rota, gonderilen_idler)
    for mekan_id in rota:
        if mekan_id not in mekan_sozlugu:
            continue  # Veri setinde hiç olmayan id: denetlenemez, uydurma olarak sayıldı
        mekan = mekan_sozlugu[mekan_id]
        etiket = etiketler[str(mekan_id)]
        kararlar = [(k, *filtre_karari(mekan, etiket, k)) for k in profil["kisitlar"]]
        nedenler = [f"{k}: {neden}" for k, karar, neden in kararlar if karar == ELENIR]
        if nedenler:
            ihlaller.append((mekan, nedenler))
        elif any(karar == UYARI for _, karar, _ in kararlar):
            uyarililar.append(mekan)
    return {"ihlaller": ihlaller, "uyarililar": uyarililar, "uydurmalar": uydurmalar}


def yontem_calistir(yontem, profil, llm_mekanlari, mekan_sozlugu, etiketler):
    """Bir yöntemi (A veya B) çalıştırır, rotayı denetler ve ayrıntıları yazdırır."""
    sonuc = llm_rota_iste(profil["istek"], llm_mekanlari)
    gonderilen_idler = {m["id"] for m in llm_mekanlari}
    denetim = rotayi_denetle(sonuc["rota"], gonderilen_idler, profil, mekan_sozlugu, etiketler)

    print(f"\n  [{yontem}] {len(llm_mekanlari)} mekân gönderildi, "
          f"{sonuc['input_token']} token, {sonuc['sure_sn']:.1f} sn")
    for mekan_id in sonuc["rota"]:
        isim = mekan_sozlugu.get(mekan_id, {}).get("isim", "veri setinde yok")
        print(f"      {mekan_id:>3}  {isim}")
    for mekan, nedenler in denetim["ihlaller"]:
        print(f"      ❌ İHLAL: {mekan['isim']} ({'; '.join(nedenler)})")
        print(f"         \"{mekan['aciklama']}\"")
    for mekan in denetim["uyarililar"]:
        print(f"      ⚠️  Uyarılı: {mekan['isim']} (erişilebilirlik doğrulanmadı)")
    if denetim["uydurmalar"]:
        print(f"      ⚠️  Uydurma id: {denetim['uydurmalar']}")
    if not 3 <= len(sonuc["rota"]) <= 4:
        print(f"      ⚠️  Rota {len(sonuc['rota'])} mekân içeriyor (3-4 istenmişti)")

    return {
        "rota": sonuc["rota"],
        "aciklama": sonuc["aciklama"],
        "input_token": sonuc["input_token"],
        "sure_sn": round(sonuc["sure_sn"], 2),
        "ihlal": [m["id"] for m, _ in denetim["ihlaller"]],
        "uyarili": [m["id"] for m in denetim["uyarililar"]],
        "uydurma": denetim["uydurmalar"],
    }


def main():
    mekanlar = json_oku(MEKAN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    mekan_sozlugu = {m["id"]: m for m in mekanlar}

    # Isınma çağrısı: modeli belleğe yükler. Süresi ölçüme katılmaz; yoksa ilk
    # profil, modelin yüklenme süresi yüzünden haksız şekilde yavaş görünür.
    print(f"Model ısınıyor ({LLM_MODELI})...")
    llm_rota_iste("warm-up", [])

    sonuclar = {}
    for profil_adi, profil in PROFILLER.items():
        print("\n" + "=" * 70)
        print(f"PROFİL: {profil_adi}  (kısıt: {profil['kisitlar']}, ilgi: {profil['ilgiler']})")

        # A) Filtresiz: tüm mekânlar, Jev bilgisi olmadan
        a_mekanlari = [llm_icin_mekan(m) for m in mekanlar]
        # B) Filtreli: Jev ile elenenler çıkarılmış, ilgi puanına göre ilk 10
        kalanlar = filtrele_ve_sirala(profil, mekanlar, etiketler)[:ILK_KAC_MEKAN]
        b_mekanlari = [llm_icin_mekan(m, uyari) for m, _, uyari in kalanlar]

        sonuclar[profil_adi] = {
            "A": yontem_calistir("A filtresiz", profil, a_mekanlari, mekan_sozlugu, etiketler),
            "B": yontem_calistir("B filtreli ", profil, b_mekanlari, mekan_sozlugu, etiketler),
        }

    json_yaz(SONUC_DOSYASI, sonuclar)

    # --- Sonuç tablosu: her profil için A ve B yan yana ---
    print("\n" + "=" * 70)
    print("SONUÇ TABLOSU (A = filtresiz, B = Jev filtreli)\n")
    print(f"{'Profil':<24}{'Token A':>9}{'Token B':>9}{'Süre A':>9}{'Süre B':>9}"
          f"{'İhlal A':>9}{'İhlal B':>9}{'Uyarı A':>9}{'Uyarı B':>9}")
    for profil_adi, s in sonuclar.items():
        a, b = s["A"], s["B"]
        print(f"{profil_adi:<24}{a['input_token']:>9}{b['input_token']:>9}"
              f"{a['sure_sn']:>8.1f}s{b['sure_sn']:>8.1f}s"
              f"{len(a['ihlal']):>9}{len(b['ihlal']):>9}{len(a['uyarili']):>9}{len(b['uyarili']):>9}")

    # --- Genel özet ---
    token_a = sum(s["A"]["input_token"] for s in sonuclar.values())
    token_b = sum(s["B"]["input_token"] for s in sonuclar.values())
    ihlal_a = sum(len(s["A"]["ihlal"]) for s in sonuclar.values())
    ihlal_b = sum(len(s["B"]["ihlal"]) for s in sonuclar.values())
    uydurma_a = sum(len(s["A"]["uydurma"]) for s in sonuclar.values())
    uydurma_b = sum(len(s["B"]["uydurma"]) for s in sonuclar.values())
    ort_a = token_a / len(sonuclar)
    ort_b = token_b / len(sonuclar)

    print("\nÖZET")
    print(f"  Token tasarrufu : %{100 * (token_a - token_b) / token_a:.0f}  "
          f"(A toplam {token_a}, B toplam {token_b})")
    print(f"  Toplam ihlal    : A'da {ihlal_a}, B'de {ihlal_b}")
    print(f"  Uydurma id      : A'da {uydurma_a}, B'de {uydurma_b}")
    print(f"  Aylık maliyet (günde {GUNLUK_ISTEK:,} istek, Claude Opus 5.5 input fiyatı ${OPUS_DOLAR_PER_MILYON_INPUT}/M):")
    print(f"    A: ~${aylik_maliyet(ort_a):,.0f}   B: ~${aylik_maliyet(ort_b):,.0f}   "
          f"fark: ~${aylik_maliyet(ort_a) - aylik_maliyet(ort_b):,.0f}/ay")
    print("  Not: Maliyet YAKLAŞIKTIR. Token'lar Qwen tokenizer'ıyla sayıldı (Claude farklı sayar),")
    print("  sadece input token hesaba katıldı (output hariç) ve Jev etiketleme maliyeti (tek seferlik) dahil değil.")


if __name__ == "__main__":
    main()

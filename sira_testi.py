"""
Sıra testi, alaka ölçümü ve ölçek hesabı.

1. Sıra testi: A yöntemi (filtresiz, 100 mekân) her profil için 3 kez, mekân
   listesi farklı rastgele sıralarla karıştırılarak çalıştırılır. Seed'ler
   kaydedilir; aynı seed her zaman aynı sırayı üretir.
   B yöntemi tekrar çalıştırılmaz, sonucu data/karsilastirma_sonuclari.json'dan okunur.
2. Alaka: seçilen mekânların, profilin ilgi puanlarının (Jev) ortalaması.
   Karşılaştırma için "rastgele seçim" (100 mekânın ortalaması) ve
   "en iyi olası" (en yüksek puanlı 3 mekân) değerleri de gösterilir.
3. Ölçek: mekân başına düşen token sayısı, 300.000 mekâna ölçeklenir ve
   1 milyon token'lık context penceresiyle karşılaştırılır.

    python3 sira_testi.py

Sonuçlar data/sira_testi_sonuclari.json dosyasına kaydedilir.
"""

import random

from etiketle import ETIKET_DOSYASI, MEKAN_DOSYASI, json_oku, json_yaz
from karsilastir import SONUC_DOSYASI as AB_SONUC_DOSYASI
from karsilastir import rotayi_denetle
from rota import LLM_MODELI, PROFILLER, llm_icin_mekan, llm_rota_iste

SONUC_DOSYASI = "data/sira_testi_sonuclari.json"
SEEDLER = [11, 22, 33]  # Karıştırma için rastgelelik tohumları (tekrar üretilebilirlik)
OLCEK_MEKAN_SAYISI = 300_000
CONTEXT_PENCERESI = 1_000_000


def ilgi_puani(etiket, profil):
    """Bir mekânın bu profil için ilgi puanı (profilin ilgi türlerinin ortalaması)."""
    return sum(etiket["ilgi_olasiliklari"][i] for i in profil["ilgiler"]) / len(profil["ilgiler"])


def alaka(rota, profil, etiketler):
    """Rotadaki (veri setinde var olan) mekânların ortalama ilgi puanı."""
    puanlar = [ilgi_puani(etiketler[str(i)], profil) for i in rota if str(i) in etiketler]
    return sum(puanlar) / len(puanlar) if puanlar else 0.0


def main():
    mekanlar = json_oku(MEKAN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    ab_sonuclari = json_oku(AB_SONUC_DOSYASI)
    mekan_sozlugu = {m["id"]: m for m in mekanlar}

    print(f"Model ısınıyor ({LLM_MODELI})...")
    llm_rota_iste("warm-up", [])

    sonuclar = {}
    for profil_adi, profil in PROFILLER.items():
        print("\n" + "=" * 70)
        print(f"PROFİL: {profil_adi}")

        # Referans değerler: rastgele seçimin beklenen alakası ve en iyi olası alaka
        tum_puanlar = sorted((ilgi_puani(etiketler[str(m["id"])], profil) for m in mekanlar), reverse=True)
        rastgele_alaka = sum(tum_puanlar) / len(tum_puanlar)
        en_iyi_alaka = sum(tum_puanlar[:3]) / 3

        # Sabit maliyet: mekân listesi boşken modele giden token (sistem mesajı + istek)
        sabit_token = llm_rota_iste(profil["istek"], [])["input_token"]

        # Karıştırılmış A çalıştırmaları
        a_kosulari = []
        for seed in SEEDLER:
            karisik = mekanlar[:]  # kopya; orijinal liste bozulmasın
            random.Random(seed).shuffle(karisik)
            llm_mekanlari = [llm_icin_mekan(m) for m in karisik]
            sonuc = llm_rota_iste(profil["istek"], llm_mekanlari)
            denetim = rotayi_denetle(sonuc["rota"], {m["id"] for m in llm_mekanlari},
                                     profil, mekan_sozlugu, etiketler)
            # Seçilen mekânlar karışık listede kaçıncı sıradaydı? (listenin başından mı seçiyor?)
            siralar = [next((n for n, m in enumerate(karisik, 1) if m["id"] == i), None) for i in sonuc["rota"]]
            kosu = {
                "seed": seed,
                "rota": sonuc["rota"],
                "liste_sirasi": siralar,
                "input_token": sonuc["input_token"],
                "sure_sn": round(sonuc["sure_sn"], 2),
                "ihlal": [m["id"] for m, _ in denetim["ihlaller"]],
                "uyarili": [m["id"] for m in denetim["uyarililar"]],
                "uydurma": denetim["uydurmalar"],
                "alaka": round(alaka(sonuc["rota"], profil, etiketler), 3),
            }
            a_kosulari.append(kosu)
            isimler = ", ".join(mekan_sozlugu.get(i, {}).get("isim", f"?{i}") for i in sonuc["rota"])
            print(f"  A seed={seed:<3} ihlal={len(kosu['ihlal'])} alaka={kosu['alaka']:.2f} "
                  f"liste sırası={siralar}  -> {isimler}")
            for mekan, nedenler in denetim["ihlaller"]:
                print(f"      ❌ {mekan['isim']} ({'; '.join(nedenler)}): \"{mekan['aciklama']}\"")

        a_orijinal = ab_sonuclari[profil_adi]["A"]
        b = ab_sonuclari[profil_adi]["B"]
        sonuclar[profil_adi] = {
            "rastgele_alaka": round(rastgele_alaka, 3),
            "en_iyi_alaka": round(en_iyi_alaka, 3),
            "sabit_token": sabit_token,
            "a_orijinal_sira": {"rota": a_orijinal["rota"], "ihlal": len(a_orijinal["ihlal"]),
                                "alaka": round(alaka(a_orijinal["rota"], profil, etiketler), 3),
                                "input_token": a_orijinal["input_token"]},
            "a_karisik": a_kosulari,
            "b": {"rota": b["rota"], "ihlal": len(b["ihlal"]),
                  "alaka": round(alaka(b["rota"], profil, etiketler), 3)},
        }

    json_yaz(SONUC_DOSYASI, {"seedler": SEEDLER, "profiller": sonuclar})

    # --- 1 & 2) Sıra etkisi ve alaka tablosu ---
    print("\n" + "=" * 70)
    print("İHLAL VE ALAKA (alaka = seçilen mekânların ortalama ilgi puanı, 0-1)\n")
    print(f"{'Profil':<24}{'İhlal A-orj':>12}{'İhlal A-karışık':>17}{'İhlal B':>9}"
          f"{'Alaka A-orj':>13}{'Alaka A-kar.':>13}{'Alaka B':>9}{'Rastgele':>10}{'En iyi':>8}")
    for profil_adi, s in sonuclar.items():
        ihlaller = "/".join(str(len(k["ihlal"])) for k in s["a_karisik"])
        a_kar_alaka = sum(k["alaka"] for k in s["a_karisik"]) / len(s["a_karisik"])
        print(f"{profil_adi:<24}{s['a_orijinal_sira']['ihlal']:>12}{ihlaller:>17}{s['b']['ihlal']:>9}"
              f"{s['a_orijinal_sira']['alaka']:>13.2f}{a_kar_alaka:>13.2f}{s['b']['alaka']:>9.2f}"
              f"{s['rastgele_alaka']:>10.2f}{s['en_iyi_alaka']:>8.2f}")

    tum_kosular = [k for s in sonuclar.values() for k in s["a_karisik"]]
    ihlal_karisik = sum(len(k["ihlal"]) for k in tum_kosular)
    secim_sayisi = sum(len(k["rota"]) for k in tum_kosular)
    ilk_onda = sum(1 for k in tum_kosular for n in k["liste_sirasi"] if n is not None and n <= 10)
    print(f"\n  A-karışık: {len(tum_kosular)} koşuda toplam {ihlal_karisik} ihlal "
          f"({secim_sayisi} seçimin %{100 * ihlal_karisik / secim_sayisi:.0f}'i)")
    print(f"  A-karışık: seçimlerin %{100 * ilk_onda / secim_sayisi:.0f}'i karışık listenin ilk 10 sırasından "
          f"(rastgele seçimde beklenen: %10)")

    # --- 3) Ölçek hesabı ---
    ornek = next(iter(sonuclar.values()))
    mekan_basina = (ornek["a_orijinal_sira"]["input_token"] - ornek["sabit_token"]) / len(mekanlar)
    olcekli = mekan_basina * OLCEK_MEKAN_SAYISI
    print("\nÖLÇEK HESABI")
    print(f"  Mekân başına token      : ~{mekan_basina:.0f} (Qwen tokenizer; isim, kategori, semt, açıklama)")
    print(f"  {OLCEK_MEKAN_SAYISI:,} mekân          : ~{olcekli / 1e6:.1f} milyon token")
    print(f"  1M context penceresine  : ~{olcekli / CONTEXT_PENCERESI:.0f} kat büyük; "
          f"tek pencereye en fazla ~{CONTEXT_PENCERESI / mekan_basina:,.0f} mekân sığar")


if __name__ == "__main__":
    main()

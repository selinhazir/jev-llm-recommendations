"""
Etiketleme sonuçlarının özeti (sunum için).

    python3 labeling_summary.py

Yazdırılanlar:
1. Her kullanıcı türü için kaç mekân kaldı, kaçı elendi
2. Veri eksikliği raporu: yaşlı ve tekerlekli sandalye için "bilgi_yok" çıkan mekânlar
3. Toplam token, maliyet ve çağrı başına ortalama süre
"""

from label import DOLAR_PER_MILYON_INPUT_TOKEN, ETIKET_DOSYASI, MEKAN_DOSYASI, TUR_DOSYASI, json_oku
from constraints import CHOICE_GUVEN_ESIGI, ELENIR, ERISILEBILIRLIK_TURLERI, UYARI, filtre_karari


def main():
    mekanlar = json_oku(MEKAN_DOSYASI)
    kullanici_turleri = json_oku(TUR_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI, varsayilan={})

    # Sadece etiketi olan mekânlarla çalış; eksik varsa haber ver
    etiketli = [m for m in mekanlar if str(m["id"]) in etiketler]
    if len(etiketli) < len(mekanlar):
        print(f"UYARI: {len(mekanlar) - len(etiketli)} mekânın etiketi yok, özete dahil edilmedi.\n")
    toplam = len(etiketli)
    if toplam == 0:
        print("Henüz hiçbir mekân etiketlenmemiş. Önce: python3 label.py")
        return

    # --- 1) Kullanıcı türü başına kalan / elenen ---
    print(f"1) KULLANICI TÜRÜ BAŞINA SONUÇ ({toplam} mekân)\n")
    print(f"{'Tür':<45}{'Kalan':>7}{'(uyarılı)':>11}{'Elenen':>8}")
    for tur in kullanici_turleri:
        if tur["tip"] == "kisit":
            elenen = uyarili = 0
            for mekan in etiketli:
                karar, _ = filtre_karari(mekan, etiketler[str(mekan["id"])], tur["id"])
                if karar == ELENIR:
                    elenen += 1
                elif karar == UYARI:
                    uyarili += 1
            print(f"{'[kısıt] ' + tur['ad']:<45}{toplam - elenen:>7}{uyarili:>11}{elenen:>8}")
        else:
            # İlgi türleri eleme yapmaz, sadece sıralar. En üstteki 3 mekânı gösterelim.
            sirali = sorted(etiketli, key=lambda m: etiketler[str(m["id"])]["ilgi_olasiliklari"][tur["id"]],
                            reverse=True)
            ilk_uc = ", ".join(m["isim"] for m in sirali[:3])
            print(f"{'[ilgi]  ' + tur['ad']:<45}{toplam:>7}{'-':>11}{0:>8}   ilk 3: {ilk_uc}")

    # --- 2) Veri eksikliği raporu ---
    print("\n2) VERİ EKSİKLİĞİ RAPORU (erişilebilirlik)\n")
    for tur_id in ERISILEBILIRLIK_TURLERI:
        tur_adi = next(t["ad"] for t in kullanici_turleri if t["id"] == tur_id)
        sayac = {"engel_var": 0, "engel_yok": 0, "bilgi_yok": 0}
        dusuk_guven = []
        bilgi_yok_mekanlar = []
        for mekan in etiketli:
            cevap = etiketler[str(mekan["id"])]["erisim_cevaplari"][tur_id]
            sayac[cevap["secim"]] += 1
            if cevap["secim"] == "bilgi_yok":
                bilgi_yok_mekanlar.append(mekan["isim"])
            elif cevap["confidence"] < CHOICE_GUVEN_ESIGI:
                dusuk_guven.append(mekan["isim"])
        print(f"{tur_adi}:")
        print(f"  engel_var: {sayac['engel_var']}   engel_yok: {sayac['engel_yok']}   "
              f"bilgi_yok: {sayac['bilgi_yok']} (%{100 * sayac['bilgi_yok'] / toplam:.0f})")
        print(f"  Düşük confidence (< {CHOICE_GUVEN_ESIGI}, bilgi_yok gibi davranıldı): {len(dusuk_guven)}")
        if dusuk_guven:
            print(f"    {', '.join(dusuk_guven)}")
        print(f"  bilgi_yok mekânlar: {', '.join(bilgi_yok_mekanlar)}\n")

    # --- 3) Token, maliyet, süre ---
    kayitlar = [etiketler[str(m["id"])] for m in etiketli]
    toplam_token = sum(k["input_tokens"] for k in kayitlar)
    sureler = [k["sure_sn"] for k in kayitlar if "sure_sn" in k]
    print("3) TOKEN, MALİYET, SÜRE\n")
    print(f"  Çağrı sayısı       : {len(kayitlar)} (mekân başına 1)")
    print(f"  Toplam input token : {toplam_token}  (çağrı başına ort. {toplam_token / len(kayitlar):.0f})")
    print(f"  Tahmini maliyet    : ${toplam_token / 1_000_000 * DOLAR_PER_MILYON_INPUT_TOKEN:.6f}")
    if sureler:
        print(f"  Ortalama süre      : {sum(sureler) / len(sureler):.2f} sn/çağrı "
              f"(en hızlı {min(sureler):.2f}, en yavaş {max(sureler):.2f})")


if __name__ == "__main__":
    main()

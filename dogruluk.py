"""
Jev'in kısıt kararlarını referans (altın) etiketlerle karşılaştırır.

    python3 dogruluk.py

Altın etiketler: data/altin_etiketler.json. Bir LLM (Claude) tarafından, sadece mekân
açıklamalarına bakarak ve Jev'in sonuçlarını görmeden oluşturuldu.
Boş (null) bırakılan alanlar atlanır ve "doldurulmamış" olarak raporlanır.

Hata yönleri:
- GÜVENLİ    : Jev gereğinden temkinli (gereksiz uyarı veya eleme). Kullanıcı bir
               seçeneği kaçırır ama zarar görmez.
- TEHLİKELİ  : Jev gereğinden rahat (engelli mekânı uygun veya daha az riskli
               gösteriyor). Kullanıcı engelle karşılaşabilir.
"""

import json

from etiketle import ETIKET_DOSYASI, json_oku
from filtrele import CHOICE_GUVEN_ESIGI, ENGEL_ESIKLERI

ALTIN_DOSYASI = "data/altin_etiketler.json"

# Erişilebilirlik seçeneklerinin "temkin sırası": sayı büyüdükçe mekân kullanıcıya
# daha zor gösterilir. engel_yok -> normal gösterilir, bilgi_yok -> uyarıyla
# gösterilir, engel_var -> elenir.
# Jev'in sırası altın etiketten BÜYÜKSE hata güvenli yöndedir (fazla temkin),
# KÜÇÜKSE tehlikeli yöndedir (fazla rahatlık).
TEMKIN_SIRASI = {"engel_yok": 0, "bilgi_yok": 1, "engel_var": 2}

# Altın etiket alan adı -> (kullanıcı türü id'si, soru çeşidi)
ALANLAR = {
    "yasli": ("yasli", "choice"),
    "tekerlekli_sandalye": ("tekerlekli_sandalye", "choice"),
    "kucuk_cocuk": ("kucuk_cocuklu_aile", "noul"),
    "evcil_hayvan": ("evcil_hayvan", "noul"),
}


def jev_choice_karari(etiket, tur_id):
    """Jev'in etkin erişilebilirlik kararı: confidence düşükse 'bilgi_yok' sayılır
    (filtrele.py ile aynı mantık)."""
    cevap = etiket["erisim_cevaplari"][tur_id]
    if cevap["confidence"] < CHOICE_GUVEN_ESIGI:
        return "bilgi_yok", f"{cevap['secim']}, confidence {cevap['confidence']:.2f} -> bilgi_yok"
    return cevap["secim"], f"confidence {cevap['confidence']:.2f}"


def jev_noul_karari(etiket, tur_id):
    """Jev'in etkin engel kararı: olasılık eşiği geçiyorsa True (engel var -> elenir)."""
    olasilik = etiket["engel_olasiliklari"][tur_id]
    return olasilik >= ENGEL_ESIKLERI[tur_id], f"engel olasılığı {olasilik:.2f}, eşik {ENGEL_ESIKLERI[tur_id]}"


def hata_yonu(alan_cesidi, altin, jev):
    """Hata güvenli mi tehlikeli mi? (altin != jev olduğu varsayılır)"""
    if alan_cesidi == "choice":
        return "güvenli" if TEMKIN_SIRASI[jev] > TEMKIN_SIRASI[altin] else "tehlikeli"
    # noul: True = engel var. Jev engel dedi ama yok -> gereksiz eleme (güvenli).
    return "güvenli" if jev else "tehlikeli"


def main():
    altin_etiketler = json_oku(ALTIN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)

    for alan, (tur_id, cesit) in ALANLAR.items():
        dogru = 0
        hatalar = {"güvenli": [], "tehlikeli": []}
        doldurulmamis = 0

        for kayit in altin_etiketler:
            altin = kayit[alan]
            if altin is None:
                doldurulmamis += 1
                continue
            etiket = etiketler[str(kayit["id"])]
            if cesit == "choice":
                jev, detay = jev_choice_karari(etiket, tur_id)
            else:
                jev, detay = jev_noul_karari(etiket, tur_id)

            if jev == altin:
                dogru += 1
            else:
                hatalar[hata_yonu(cesit, altin, jev)].append((kayit, altin, jev, detay))

        toplam = dogru + len(hatalar["güvenli"]) + len(hatalar["tehlikeli"])
        print("=" * 70)
        print(f"{alan.upper()}  ({cesit})")
        if toplam == 0:
            print("  Henüz etiketlenmemiş.\n")
            continue
        print(f"  Doğru: {dogru}/{toplam} (%{100 * dogru / toplam:.0f})"
              f"   Güvenli hata: {len(hatalar['güvenli'])}"
              f"   Tehlikeli hata: {len(hatalar['tehlikeli'])}")
        if doldurulmamis:
            print(f"  ({doldurulmamis} mekân doldurulmamış, atlandı)")

        for yon in ["tehlikeli", "güvenli"]:
            if not hatalar[yon]:
                continue
            baslik = ("TEHLİKELİ (engelli mekân daha uygun gösterildi)" if yon == "tehlikeli"
                      else "GÜVENLİ (gereksiz uyarı / eleme)")
            print(f"\n  {baslik}:")
            for kayit, altin, jev, detay in hatalar[yon]:
                print(f"    - {kayit['isim']}: referans={altin}, Jev={jev} ({detay})")
                print(f"      \"{kayit['aciklama']}\"")
        print()


if __name__ == "__main__":
    main()

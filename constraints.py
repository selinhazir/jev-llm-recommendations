"""
Kısıt türleri için sert filtre: bir mekân hangi kullanıcı türlerinden elenir?

Her kısıt türü aşağıdaki üç kural çeşidinden TAM OLARAK birini kullanır:
- KOD_KURALLARI: Sayısal veya kategorik alanlardan (ör. fiyat_seviyesi) kodla karar verilir.
  Jev'e hiç sorulmaz. Kural: Jev'i sadece açıklama metninden karar çıkarmak için kullanıyoruz.
- ERISILEBILIRLIK_TURLERI: Jev'e bir "choice" sorusu sorulur:
  engel_var / engel_yok / bilgi_yok. Böylece "engel var" ile "bilmiyoruz" ayrışır.
- ENGEL_ESIKLERI: Jev'e bir "noul" sorusu sorulur; engel olasılığı eşiği geçerse elenir.

Her kural üç karardan birini verir:
    ELENIR    -> mekân bu kullanıcıya gösterilmez
    UYARI     -> gösterilir ama "erişilebilirlik doğrulanmadı" uyarısıyla
    GOSTERILIR-> sorun yok
"""

ELENIR = "elenir"
UYARI = "uyari"
GOSTERILIR = "gosterilir"


def butce_icin_pahali_mi(mekan):
    """Bütçe dostu gezgin için: fiyat seviyesi 3 veya 4 ise fazla pahalıdır."""
    return mekan["fiyat_seviyesi"] >= 3


# Kodla karar verilen kısıt türleri: tür id'si -> "elensin mi?" fonksiyonu
KOD_KURALLARI = {
    "genc_butce": butce_icin_pahali_mi,
}

# Erişilebilirlik türleri: Jev'e choice sorusu sorulur.
# Hatalar eşit değil: bu kişilere merdivenli bir yer önermek, uygun bir yeri
# gizlemekten çok daha kötü. Bu yüzden emin olmadığımız her durumu (bilgi_yok
# veya düşük confidence) "doğrulanmadı" uyarısıyla gösteriyoruz; sessizce
# "uygun" demiyoruz.
ERISILEBILIRLIK_TURLERI = ["yasli", "tekerlekli_sandalye"]

# Choice confidence bu değerin altındaysa Jev'in seçimine güvenmeyiz ve
# "bilgi_yok" gibi davranırız. 0.5, TypeSafe dokümanının önerdiği başlangıç değeri.
CHOICE_GUVEN_ESIGI = 0.5

# Noul ile karar verilen kısıt türleri: engel olasılığı >= eşik ise mekân elenir.
# Esnek (yüksek) eşik: bu kişiler küçük sorunları çoğu zaman kendileri çözebilir
# (köpeği dışarıda bekletmek, bebek arabasını katlamak). Bu yüzden sadece Jev
# engelden oldukça eminse eleriz; çok fazla mekân gizlemek istemiyoruz.
ENGEL_ESIKLERI = {
    "evcil_hayvan": 0.6,
    "kucuk_cocuklu_aile": 0.6,
}


def filtre_karari(mekan, etiket, tur_id):
    """Bir mekânın bir kısıt türü için kararını ve kısa nedenini döndürür.

    Dönen değer: (karar, neden)   ör. ("elenir", "fiyat seviyesi 4")
    mekan : istanbul_mekanlar.json'daki kayıt
    etiket: etiketler.json'daki bu mekâna ait kayıt
    """
    if tur_id in KOD_KURALLARI:
        if KOD_KURALLARI[tur_id](mekan):
            return ELENIR, f"fiyat seviyesi {mekan['fiyat_seviyesi']}"
        return GOSTERILIR, "kod kuralı"

    if tur_id in ERISILEBILIRLIK_TURLERI:
        cevap = etiket["erisim_cevaplari"][tur_id]
        if cevap["confidence"] < CHOICE_GUVEN_ESIGI:
            return UYARI, f"düşük confidence ({cevap['confidence']:.2f})"
        if cevap["secim"] == "engel_var":
            return ELENIR, "açıklamada engel var"
        if cevap["secim"] == "engel_yok":
            return GOSTERILIR, "açıklamada uygunluk bilgisi var"
        return UYARI, "erişilebilirlik doğrulanmadı"

    olasilik = etiket["engel_olasiliklari"][tur_id]
    if olasilik >= ENGEL_ESIKLERI[tur_id]:
        return ELENIR, f"engel {olasilik:.2f} >= {ENGEL_ESIKLERI[tur_id]}"
    return GOSTERILIR, f"engel {olasilik:.2f}"


def kurallari_kontrol_et(kullanici_turleri):
    """Her kısıt türünün tam olarak bir kural çeşidi olduğundan emin olur."""
    for tur in kullanici_turleri:
        if tur["tip"] != "kisit":
            continue
        kural_sayisi = (
            (tur["id"] in KOD_KURALLARI)
            + (tur["id"] in ERISILEBILIRLIK_TURLERI)
            + (tur["id"] in ENGEL_ESIKLERI)
        )
        if kural_sayisi != 1:
            raise ValueError(f"'{tur['id']}' için tam olarak bir kural tanımlanmalı (şu an {kural_sayisi}).")

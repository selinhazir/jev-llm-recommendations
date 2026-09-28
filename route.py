"""
Bir kullanıcı profili için Jev filtresiyle mekân listesi hazırlar ve yerel LLM'den
(Ollama, qwen2.5:3b) 3-4 mekânlık bir rota ister.

Adımlar:
1. constraints.py ile profilin kısıtlarına göre elenen mekânları çıkar
2. Kalanları profilin ilgi puanlarına göre sırala, ilk 10'u al
3. Bu 10 mekânı LLM'e gönder, JSON rota iste: {"route": [id'ler], "explanation": "..."}
4. Dönen id'ler gönderilen listede mi? Uydurma id varsa işaretle.

Kullanım:
    python3 route.py                 -> varsayılan profil (yasli_kultur)
    python3 route.py yasli_kultur    -> belirli bir profil

Ollama'nın çalışıyor olması gerekir (http://localhost:11434).
"""

import json
import sys
import time
import urllib.request

from label import ETIKET_DOSYASI, MEKAN_DOSYASI, json_oku
from constraints import ELENIR, UYARI, filtre_karari

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODELI = "qwen2.5:3b"
ILK_KAC_MEKAN = 10

# LLM'e her mekân için gönderilen alanlar (filtreli ve filtresiz yöntemde aynı).
# Sol: veri dosyasındaki alan adı, sağ: LLM'e giden İngilizce alan adı.
LLM_ALANLARI = {"id": "id", "isim": "name", "kategori": "category", "semt": "district", "aciklama": "description"}
UYARI_NOTU = "accessibility not verified"

# Kullanıcı profilleri.
#   kisitlar: constraints.py'deki kısıt türleri (sert filtre)
#   ilgiler : sıralama için kullanılan ilgi türleri (puanların ortalaması alınır)
#   istek   : kullanıcının kendi ağzından isteği; LLM'in gördüğü tek profil bilgisi budur
PROFILLER = {
    "yasli_kultur": {
        "kisitlar": ["yasli"],
        "ilgiler": ["kultur_meraklisi"],
        "istek": "I'm 72 years old, and long walks and stairs tire me out a lot. "
                 "I love historic sites and museums. Can you suggest a one-day route?",
    },
    "tekerlekli_gastronomi": {
        "kisitlar": ["tekerlekli_sandalye"],
        "ilgiler": ["gastronomi"],
        "istek": "I use a wheelchair. I'm coming to Istanbul for the food: I want to try local flavors, "
                 "long-established restaurants and street food. Can you suggest a one-day route?",
    },
    "kucuk_cocuk_doga": {
        "kisitlar": ["kucuk_cocuklu_aile"],
        "ilgiler": ["doga_macera"],
        "istek": "We have two kids aged 3 and 5 and we use a stroller. We love spending time outdoors "
                 "in nature and doing active things. Can you suggest a one-day route?",
    },
    "evcil_hayvan_yalniz": {
        "kisitlar": ["evcil_hayvan"],
        "ilgiler": ["yalniz_gezgin"],
        "istek": "I'm traveling alone with my dog and I want to take him everywhere with me. "
                 "I like social places where I can meet people. Can you suggest a one-day route?",
    },
    "butce_yalniz": {
        "kisitlar": ["genc_butce"],
        "ilgiler": ["yalniz_gezgin"],
        "istek": "I'm a 23-year-old student on a very tight budget. I'm traveling alone and I like lively, "
                 "social places where I can meet people. Can you suggest a one-day route?",
    },
}

# LLM'in döndürmesi gereken JSON'un şeması. Ollama bu şemaya uygun çıktı üretir.
# maxLength: küçük model bazen açıklamayı hiç bitirmeden yazmaya devam ediyor (10 dakikayı
# aşan bir çağrı gördük). Sınır, açıklamayı 400 karakterde keser; JSON yine geçerli kalır.
ROTA_SEMASI = {
    "type": "object",
    "properties": {
        "route": {"type": "array", "items": {"type": "integer"}},
        "explanation": {"type": "string", "maxLength": 400},
    },
    "required": ["route", "explanation"],
}

SISTEM_MESAJI = (
    "You are a trip planner. Based on the user's request, choose 3 or 4 venues ONLY from "
    "the given venue list. Do not use any venue or id that is not in the list. "
    "Pay attention to the user's physical constraints and needs. "
    'Return only this JSON: {"route": [venue ids], "explanation": "short explanation"}'
)


def filtrele_ve_sirala(profil, mekanlar, etiketler):
    """Profilin kısıtlarına göre elenenleri çıkarır, kalanları ilgi puanına göre sıralar.

    Dönen liste: [(mekan, ilgi_puani, uyari_var_mi), ...]  (en yüksek puan başta)
    """
    kalanlar = []
    for mekan in mekanlar:
        etiket = etiketler[str(mekan["id"])]
        kararlar = [filtre_karari(mekan, etiket, k)[0] for k in profil["kisitlar"]]
        if ELENIR in kararlar:
            continue  # En az bir kısıt mekânı eliyorsa bu kullanıcıya gösterilmez
        # Birden fazla ilgi varsa puanların ortalaması
        puan = sum(etiket["ilgi_olasiliklari"][i] for i in profil["ilgiler"]) / len(profil["ilgiler"])
        kalanlar.append((mekan, puan, UYARI in kararlar))
    kalanlar.sort(key=lambda satir: satir[1], reverse=True)
    return kalanlar


def llm_icin_mekan(mekan, uyari_var_mi=False):
    """Mekânın sadece LLM'e gidecek alanlarını seçer; gerekirse uyarı notu ekler."""
    kayit = {ingilizce: mekan[alan] for alan, ingilizce in LLM_ALANLARI.items()}
    if uyari_var_mi:
        kayit["note"] = UYARI_NOTU
    return kayit


def kullanici_mesaji_olustur(istek, llm_mekanlari):
    """LLM'e giden kullanıcı mesajı: istek + mekân listesi (JSON)."""
    return (
        f"User request: {istek}\n\n"
        f"Venue list (JSON):\n{json.dumps(llm_mekanlari, ensure_ascii=False)}"
    )


def llm_rota_iste(istek, llm_mekanlari):
    """Ollama'ya rota isteği gönderir.

    Dönen sözlük: rota, aciklama, input_token, output_token, sure_sn, ham_cevap
    """
    kullanici_mesaji = kullanici_mesaji_olustur(istek, llm_mekanlari)
    govde = {
        "model": LLM_MODELI,
        "messages": [
            {"role": "system", "content": SISTEM_MESAJI},
            {"role": "user", "content": kullanici_mesaji},
        ],
        "format": ROTA_SEMASI,
        "stream": False,
        # temperature 0: aynı girdiye aynı cevap (karşılaştırma adil olsun)
        # num_ctx: bağlam penceresi. Varsayılan küçük olabilir; 100 mekânlık liste
        # sığmazsa Ollama baştan keser ve model listenin bir kısmını hiç görmez.
        # num_predict: cevap için en fazla 400 token (güvenlik sınırı; normal cevap ~120 token).
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 400},
    }
    istek_nesnesi = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(govde).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    baslangic = time.perf_counter()
    with urllib.request.urlopen(istek_nesnesi, timeout=600) as cevap:
        sonuc = json.loads(cevap.read().decode("utf-8"))
    sure_sn = time.perf_counter() - baslangic

    icerik = sonuc["message"]["content"]
    try:
        rota_json = json.loads(icerik)
    except json.JSONDecodeError:
        rota_json = {"route": [], "explanation": f"INVALID JSON: {icerik[:200]}"}

    return {
        "rota": rota_json.get("route", []),
        "aciklama": rota_json.get("explanation", ""),
        "input_token": sonuc.get("prompt_eval_count", 0),  # modele giden token sayısı
        "output_token": sonuc.get("eval_count", 0),
        "sure_sn": sure_sn,
        "ham_cevap": icerik,
    }


def uydurma_idleri_bul(rota, gonderilen_idler):
    """Rotadaki id'lerden modele gönderilmemiş (uydurulmuş) olanları döndürür."""
    return [mekan_id for mekan_id in rota if mekan_id not in gonderilen_idler]


def main():
    profil_adi = sys.argv[1] if len(sys.argv) > 1 else "yasli_kultur"
    profil = PROFILLER[profil_adi]

    mekanlar = json_oku(MEKAN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    mekan_sozlugu = {m["id"]: m for m in mekanlar}

    # 1-2) Filtrele ve sırala, ilk 10'u al
    kalanlar = filtrele_ve_sirala(profil, mekanlar, etiketler)
    secilenler = kalanlar[:ILK_KAC_MEKAN]
    print(f"Profil: {profil_adi}  (kısıt: {profil['kisitlar']}, ilgi: {profil['ilgiler']})")
    print(f"100 mekândan {len(kalanlar)} tanesi filtreden geçti, ilk {len(secilenler)} tanesi LLM'e gidiyor:\n")
    for mekan, puan, uyari in secilenler:
        not_metni = "  [erişilebilirlik doğrulanmadı]" if uyari else ""
        print(f"  {mekan['id']:>3}  {puan:.2f}  {mekan['isim']}{not_metni}")

    # 3) LLM'den rota iste
    llm_mekanlari = [llm_icin_mekan(m, uyari) for m, _, uyari in secilenler]
    sonuc = llm_rota_iste(profil["istek"], llm_mekanlari)

    # 4) Uydurma id kontrolü
    gonderilen_idler = {m["id"] for m in llm_mekanlari}
    uydurmalar = uydurma_idleri_bul(sonuc["rota"], gonderilen_idler)

    print(f"\nLLM rotası ({LLM_MODELI}, {sonuc['input_token']} input token, {sonuc['sure_sn']:.1f} sn):")
    for mekan_id in sonuc["rota"]:
        if mekan_id in uydurmalar:
            isim = mekan_sozlugu.get(mekan_id, {}).get("isim", "veri setinde yok")
            print(f"  ⚠️  {mekan_id}: UYDURMA ID (listede yoktu; {isim})")
        else:
            print(f"  ✓ {mekan_id}: {mekan_sozlugu[mekan_id]['isim']}")
    print(f"\nAçıklama: {sonuc['aciklama']}")
    if not 3 <= len(sonuc["rota"]) <= 4:
        print(f"⚠️  Rota {len(sonuc['rota'])} mekân içeriyor (3-4 istenmişti).")
    if uydurmalar:
        print(f"⚠️  {len(uydurmalar)} uydurma id bulundu: {uydurmalar}")


if __name__ == "__main__":
    main()

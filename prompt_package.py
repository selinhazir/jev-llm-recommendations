"""
Firmalar arası karşılaştırma paketi: 3 profil × 3 prompt tipi = 9 metin dosyası (prompts/).

    A_full_list    : 100 mekânın tamamı + istek (id'lerle cevap istenir)
    B_jev_filtered : Jev ile filtrelenmiş ilk 10 mekân + aynı istek (id'lerle cevap istenir)
    C_no_list      : sadece istek, liste yok (mekân isimleriyle cevap istenir)

Metinler, compare.py'de yerel modele giden metinle birebir aynıdır (aynı fonksiyonlar).
Her dosyanın token sayısı Qwen tokenizer'ıyla (Ollama) sayılır; diğer servislerin
tokenizer'ları farklıdır, bu yüzden sayılar yaklaşıktır.

    python3 prompt_package.py            -> dosyalar + token sayıları (Ollama açık olmalı)
    python3 prompt_package.py --tokensiz -> sadece dosyalar (token sayısı sonra eklenir)

Çıktılar:
    prompts/<profil>__<tip>.txt
    prompts/manifest.json   (her dosya için profil, tip, gönderilen id'ler, token sayısı)
"""

import json
import os
import sys
import urllib.request

from label import ETIKET_DOSYASI, MEKAN_DOSYASI, json_oku, json_yaz
from route import (ILK_KAC_MEKAN, LLM_MODELI, PROFILLER, SISTEM_MESAJI, filtrele_ve_sirala,
                  kullanici_mesaji_olustur, llm_icin_mekan)

PROMPT_KLASORU = "prompts"
MANIFEST_DOSYASI = os.path.join(PROMPT_KLASORU, "manifest.json")

# Dosya adı -> route.py'deki profil
PAKET_PROFILLERI = {
    "wheelchair_food": "tekerlekli_gastronomi",
    "small_kids_nature": "kucuk_cocuk_doga",
    "elderly_culture": "yasli_kultur",
}

# C tipi: liste yok, bu yüzden id yerine mekân isimleri istenir
SISTEM_MESAJI_C = (
    "You are a trip planner. Based on the user's request, recommend 3 or 4 real venues in Istanbul. "
    "Pay attention to the user's physical constraints and needs. "
    'Return only this JSON: {"route": ["venue names"], "explanation": "short explanation"}'
)


def prompt_metni(sistem, kullanici):
    """Tek bir metin dosyası: sistem talimatı + kullanıcı mesajı.
    Sohbet arayüzüne tek mesaj olarak yapıştırılabilir; API'de ikiye bölünebilir."""
    return f"{sistem}\n\n{kullanici}\n"


def token_say(metin):
    """Metnin Qwen tokenizer'ına göre token sayısı (Ollama).

    Ollama, bir önceki istekle ortak baştaki kısmı (prefix) önbellekten kullanıp
    saymayabilir. Bunu önlemek için önce alakasız kısa bir metin gönderiyoruz.
    raw=True: sohbet şablonu eklenmez, sadece metnin kendisi sayılır.
    """
    def gonder(prompt):
        govde = {"model": LLM_MODELI, "prompt": prompt, "raw": True, "stream": False,
                 "options": {"num_predict": 1, "num_ctx": 16384}}
        istek = urllib.request.Request("http://localhost:11434/api/generate",
                                       data=json.dumps(govde).encode("utf-8"),
                                       headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(istek, timeout=600) as cevap:
            return json.loads(cevap.read().decode("utf-8")).get("prompt_eval_count", 0)

    gonder("#")  # önbelleği "sıfırla"
    return gonder(metin)


def main():
    tokenleri_say = "--tokensiz" not in sys.argv
    mekanlar = json_oku(MEKAN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    os.makedirs(PROMPT_KLASORU, exist_ok=True)

    manifest = {}
    for dosya_profili, profil_adi in PAKET_PROFILLERI.items():
        profil = PROFILLER[profil_adi]
        a_mekanlari = [llm_icin_mekan(m) for m in mekanlar]
        kalanlar = filtrele_ve_sirala(profil, mekanlar, etiketler)[:ILK_KAC_MEKAN]
        b_mekanlari = [llm_icin_mekan(m, uyari) for m, _, uyari in kalanlar]

        tipler = {
            "A_full_list": (prompt_metni(SISTEM_MESAJI, kullanici_mesaji_olustur(profil["istek"], a_mekanlari)),
                            [m["id"] for m in a_mekanlari]),
            "B_jev_filtered": (prompt_metni(SISTEM_MESAJI, kullanici_mesaji_olustur(profil["istek"], b_mekanlari)),
                               [m["id"] for m in b_mekanlari]),
            "C_no_list": (prompt_metni(SISTEM_MESAJI_C, f"User request: {profil['istek']}"), []),
        }
        for tip, (metin, idler) in tipler.items():
            dosya_adi = f"{dosya_profili}__{tip}.txt"
            with open(os.path.join(PROMPT_KLASORU, dosya_adi), "w", encoding="utf-8") as f:
                f.write(metin)
            tokenler = token_say(metin) if tokenleri_say else None
            manifest[dosya_adi] = {
                "paket_profili": dosya_profili, "profil": profil_adi, "tip": tip,
                "gonderilen_idler": idler, "token_qwen": tokenler, "karakter": len(metin),
            }
            print(f"{dosya_adi:<42} {str(tokenler):>6} token  {len(metin):>7} karakter")

    json_yaz(MANIFEST_DOSYASI, manifest)
    print(f"\nManifest: {MANIFEST_DOSYASI}")


if __name__ == "__main__":
    main()

"""
Başka servislerde (ör. farklı LLM sağlayıcıları) elle çalıştırılan promptların
sonuçlarını terminalden girip data/vendor_results.json dosyasına kaydeder.

    python3 record.py

Sorulanlar: servis adı, model adı, profil, prompt tipi, ölçtüğün süre (saniye),
modelin cevabı. Cevap çok satırlı olabilir: yapıştır, sonra BOŞ bir satırda Enter'a bas.
(Not: cevabın içinde boş satır varsa, giriş o satırda biter. JSON cevaplarda genelde
boş satır olmaz; olursa boş satırları silip yapıştır.)
"""

import datetime

from label import json_oku, json_yaz
from prompt_package import PAKET_PROFILLERI

SONUC_DOSYASI = "data/vendor_results.json"
PROMPT_TIPLERI = ["A_full_list", "B_jev_filtered", "C_no_list"]


def secim_yap(baslik, secenekler):
    """Numaralı bir liste gösterir, geçerli bir numara girilene kadar sorar."""
    print(f"\n{baslik}")
    for n, secenek in enumerate(secenekler, 1):
        print(f"  {n}) {secenek}")
    while True:
        giris = input("Numara: ").strip()
        if giris.isdigit() and 1 <= int(giris) <= len(secenekler):
            return secenekler[int(giris) - 1]
        print("  Geçersiz seçim, tekrar dene.")


def bos_olmayan(soru):
    """Boş bırakılamayan bir metin sorar."""
    while True:
        cevap = input(soru).strip()
        if cevap:
            return cevap
        print("  Boş bırakılamaz.")


def sayi_sor(soru):
    """Ondalıklı bir sayı sorar (virgül de kabul edilir, ör. 3,5)."""
    while True:
        giris = input(soru).strip().replace(",", ".")
        try:
            return float(giris)
        except ValueError:
            print("  Bir sayı gir (ör. 4.2).")


def cok_satirli_cevap():
    """Boş bir satır girilene kadar satırları okur."""
    print("\nModelin cevabını yapıştır. Bitince boş bir satırda Enter'a bas:")
    satirlar = []
    while True:
        satir = input()
        if satir.strip() == "":
            if satirlar:
                break
            continue  # Henüz hiçbir şey yapıştırılmadıysa baştaki boş satırları atla
        satirlar.append(satir)
    return "\n".join(satirlar)


def main():
    kayitlar = json_oku(SONUC_DOSYASI, varsayilan=[])

    while True:
        servis = bos_olmayan("\nServis adı (ör. OpenAI, Google, Anthropic): ")
        model = bos_olmayan("Model adı: ")
        profil = secim_yap("Profil:", list(PAKET_PROFILLERI))
        tip = secim_yap("Prompt tipi:", PROMPT_TIPLERI)
        sure = sayi_sor("\nÖlçtüğün süre (saniye): ")
        cevap = cok_satirli_cevap()

        kayit = {
            "servis": servis, "model": model, "profil": profil, "tip": tip,
            "sure_sn": sure, "cevap": cevap,
            "prompt_dosyasi": f"prompts/{profil}__{tip}.txt",
            "zaman": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        kayitlar.append(kayit)
        json_yaz(SONUC_DOSYASI, kayitlar)  # Her kayıttan sonra hemen yaz
        print(f"\n✓ Kaydedildi ({len(kayitlar)} kayıt): {servis} / {model} / {profil} / {tip}")

        if input("\nBaşka bir sonuç girecek misin? (e/h): ").strip().lower() not in ("e", "evet", "y", "yes"):
            break


if __name__ == "__main__":
    main()

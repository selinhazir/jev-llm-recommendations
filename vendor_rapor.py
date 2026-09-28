"""
Firmalar arası karşılaştırma raporu (çıktı İngilizce, sunum için).

data/vendor_results.json'daki (kaydet.py ile girilen) her cevap için:
- A ve B: dönen id'ler gönderilen listede mi? Kısıt ihlalleri (Jev etiketlerine göre,
  açıklamalarıyla), uyarılı mekânlar, alaka puanı.
- C: önerilen mekân isimlerinden kaçı veri setimizde var? (takma adlarla eşleştirilir)
  Eşleşenler için ihlal ve alaka da hesaplanır.
- Maliyet: FIYATLAR sözlüğündeki servis fiyatlarıyla (yeni servis eklenince oraya fiyat ekle).

    python3 vendor_rapor.py
"""

import json
import re
import unicodedata

from etiketle import ETIKET_DOSYASI, MEKAN_DOSYASI, TUR_DOSYASI, json_oku
from karsilastir import AYDAKI_GUN, GUNLUK_ISTEK, rotayi_denetle
from kaydet import SONUC_DOSYASI
from prompt_paketi import MANIFEST_DOSYASI
from rota import PROFILLER
from sira_testi import alaka

TAKMA_AD_DOSYASI = "data/mekan_takma_adlari.json"

# --- Servis fiyatları: milyon token başına dolar. Anahtar: "Servis | Model".
# Eşleştirme büyük/küçük harf duyarsızdır ("ChatGPt" = "ChatGPT"). Raporda servis
# adı buradaki yazımla gösterilir.
FIYATLAR = {
    "ChatGPT | GPT-5.6 Luna": {"input": 0.20, "output": 1.20},
    "Gemini | Gemini 3.1 Pro": {"input": 2.00, "output": 12.00},
    "Claude | Claude Opus 5.5": {"input": 4.00, "output": 20.00},
}

# Output token sayısı servisten alınmadığı için kabaca tahmin edilir
# (İngilizce metinde ~4 karakter ≈ 1 token).
KARAKTER_PER_TOKEN = 4


def anahtar_normalize(servis, model):
    """Büyük/küçük harf ve baştaki/sondaki boşluklardan bağımsız "servis | model" anahtarı."""
    return f"{servis.strip().lower()} | {model.strip().lower()}"


# Normalize anahtar -> (fiyat, FIYATLAR'daki yazımıyla servis adı, model adı)
FIYAT_TABLOSU = {
    anahtar_normalize(*anahtar.split(" | ")): (fiyat, *anahtar.split(" | "))
    for anahtar, fiyat in FIYATLAR.items()
}


def tablo_yazdir(satirlar):
    """Sözlük listesini hizalı bir metin tablosu olarak yazdırır."""
    sutunlar = list(satirlar[0])
    genislik = {s: max(len(s), *(len(str(r[s])) for r in satirlar)) for s in sutunlar}
    print("  ".join(s.ljust(genislik[s]) for s in sutunlar))
    print("  ".join("-" * genislik[s] for s in sutunlar))
    for r in satirlar:
        print("  ".join(str(r[s]).ljust(genislik[s]) for s in sutunlar))


def ortalama(degerler):
    """None olmayan değerlerin ortalaması; hiç değer yoksa None."""
    degerler = [d for d in degerler if d is not None]
    return sum(degerler) / len(degerler) if degerler else None


def yuzde_fark(a, b):
    """A'ya göre B'nin yüzde farkı, ör. -88% (B, A'dan %88 az)."""
    if not a or b is None:
        return "-"
    return f"{100 * (b - a) / a:+.0f}%"


# ---------------------------------------------------------------- cevap çözme
def cevap_coz(metin):
    """Cevaptaki JSON'dan (route, explanation) çıkarır. Kod bloğu (```json) ve
    JSON dışındaki metinler tolere edilir. Dönen: (route listesi, açıklama, json_gecerli_mi)"""
    temiz = re.sub(r"```(?:json)?", "", metin)
    bas, son = temiz.find("{"), temiz.rfind("}")
    if bas != -1 and son > bas:
        try:
            veri = json.loads(temiz[bas:son + 1])
            return list(veri.get("route", [])), str(veri.get("explanation", "")), True
        except json.JSONDecodeError:
            pass
    # Yedek yol: "route": [ ... ] kısmını düzenli ifadeyle bul
    eslesme = re.search(r'"route"\s*:\s*\[(.*?)\]', temiz, re.DOTALL)
    if eslesme:
        ogeler = [o.strip().strip('"').strip("'") for o in eslesme.group(1).split(",") if o.strip()]
        return ogeler, "", False
    return [], "", False


def id_listesi(route):
    """route öğelerini tam sayı id'ye çevirir; çevrilemeyenleri ayrı döndürür."""
    idler, gecersiz = [], []
    for oge in route:
        try:
            idler.append(int(str(oge).strip()))
        except ValueError:
            gecersiz.append(oge)
    return idler, gecersiz


def ingilizce_neden(neden, tur_adlari):
    """filtrele.py'nin Türkçe nedenini İngilizce rapora çevirir.
    ör. 'yasli: açıklamada engel var' -> 'Elderly traveler: barrier in description'"""
    tur_id, _, metin = neden.partition(": ")
    metin = (metin.replace("açıklamada engel var", "barrier in description")
                  .replace("fiyat seviyesi", "price level")
                  .replace("engel ", "barrier probability "))
    return f"{tur_adlari.get(tur_id, tur_id)}: {metin}"


# ---------------------------------------------------------------- isim eşleştirme (C)
def normalize(metin):
    """Küçük harf, Türkçe karakterleri sadeleştir (ı->i, ş->s...), noktalama -> boşluk."""
    metin = metin.replace("ı", "i").replace("İ", "i").lower()
    metin = unicodedata.normalize("NFKD", metin)
    metin = "".join(h for h in metin if not unicodedata.combining(h))
    metin = re.sub(r"[^a-z0-9]+", " ", metin)
    return " ".join(metin.split())


def aday_isimler(mekan, takma_adlar):
    """Bir mekân için eşleştirmede kullanılacak tüm isim biçimleri."""
    isimler = [mekan["isim"], re.sub(r"\(.*?\)", "", mekan["isim"])]
    isimler += re.findall(r"\((.*?)\)", mekan["isim"])  # parantez içi, ör. "Mavi Cami"
    isimler += takma_adlar.get(str(mekan["id"]), [])
    return {normalize(i) for i in isimler if normalize(i)}


def isimle_eslestir(oneri, mekanlar, takma_adlar):
    """Önerilen isim hangi mekâna ait? En uzun eşleşen isim kazanır. Yoksa None."""
    hedef = normalize(str(oneri))
    en_iyi, en_uzun = None, 0
    for mekan in mekanlar:
        for aday in aday_isimler(mekan, takma_adlar):
            if re.search(rf"\b{re.escape(aday)}\b", hedef) and len(aday) > en_uzun:
                en_iyi, en_uzun = mekan, len(aday)
    return en_iyi


# ---------------------------------------------------------------- rapor
def main():
    kayitlar = json_oku(SONUC_DOSYASI, varsayilan=[])
    manifest = json_oku(MANIFEST_DOSYASI, varsayilan={})
    mekanlar = json_oku(MEKAN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    takma_adlar = json_oku(TAKMA_AD_DOSYASI, varsayilan={})
    mekan_sozlugu = {m["id"]: m for m in mekanlar}
    tur_adlari = {t["id"]: t["ad"] for t in json_oku(TUR_DOSYASI)}

    if not kayitlar:
        print(f"No results yet. Add some with: python3 kaydet.py  ({SONUC_DOSYASI})")
        return
    if not manifest:
        print(f"{MANIFEST_DOSYASI} not found. Run: python3 prompt_paketi.py")
        return

    tablo = []
    olcumler = []  # servis özeti için sayısal değerler
    eksik_fiyatlar = set()
    for n, kayit in enumerate(kayitlar, 1):
        dosya = f"{kayit['profil']}__{kayit['tip']}.txt"
        bilgi = manifest[dosya]
        profil = PROFILLER[bilgi["profil"]]
        route, aciklama, json_gecerli = cevap_coz(kayit["cevap"])

        print("=" * 78)
        print(f"#{n}  {kayit['servis']} | {kayit['model']} | {kayit['profil']} | {kayit['tip']}"
              f"  ({kayit['sure_sn']} s){'' if json_gecerli else '  [!] JSON could not be parsed cleanly'}")

        if kayit["tip"] == "C_no_list":
            # İsimleri veri setiyle eşleştir
            eslesen_idler, eslesmeyen = [], []
            for oneri in route:
                mekan = isimle_eslestir(oneri, mekanlar, takma_adlar)
                if mekan:
                    eslesen_idler.append(mekan["id"])
                    print(f"    ✓ '{oneri}' -> {mekan['isim']} (id {mekan['id']})")
                else:
                    eslesmeyen.append(oneri)
                    print(f"    ✗ '{oneri}' -> not in our catalog")
            denetim = rotayi_denetle(eslesen_idler, set(eslesen_idler), profil, mekan_sozlugu, etiketler)
            kapsam = f"{len(eslesen_idler)}/{len(route)} in catalog"
            puan_idleri = eslesen_idler
            katalog_disi = len(eslesmeyen)
        else:
            idler, gecersiz = id_listesi(route)
            gonderilen = set(bilgi["gonderilen_idler"])
            denetim = rotayi_denetle(idler, gonderilen, profil, mekan_sozlugu, etiketler)
            for mekan_id in idler:
                isim = mekan_sozlugu.get(mekan_id, {}).get("isim", "not in catalog")
                durum = "✓" if mekan_id in gonderilen else "✗ NOT IN LIST"
                print(f"    {durum} {mekan_id}: {isim}")
            for oge in gecersiz:
                print(f"    ✗ '{oge}': not an id")
            listede = len(idler) - len(denetim["uydurmalar"])
            kapsam = f"{listede}/{len(route)} in list"
            puan_idleri = [i for i in idler if i in gonderilen]
            katalog_disi = None

        for mekan, nedenler in denetim["ihlaller"]:
            print(f"    ❌ VIOLATION: {mekan['isim']} ({'; '.join(ingilizce_neden(n, tur_adlari) for n in nedenler)})")
            print(f"       \"{mekan['aciklama']}\"")
        for mekan in denetim["uyarililar"]:
            print(f"    ⚠️  Flagged: {mekan['isim']} (accessibility not verified)")
        if not 3 <= len(route) <= 4:
            print(f"    ⚠️  Route has {len(route)} venues (3-4 requested)")

        # Maliyet
        # Token sayısı henüz ölçülmediyse (prompt_paketi.py --tokensiz) karakterden tahmin et
        input_token = bilgi["token_qwen"] or round(bilgi["karakter"] / KARAKTER_PER_TOKEN)
        output_token = round(len(kayit["cevap"]) / KARAKTER_PER_TOKEN)
        fiyat_kaydi = FIYAT_TABLOSU.get(anahtar_normalize(kayit["servis"], kayit["model"]))
        if fiyat_kaydi:
            fiyat, servis_adi, model_adi = fiyat_kaydi
            maliyet = (input_token * fiyat["input"] + output_token * fiyat["output"]) / 1_000_000
            maliyet_metni = f"${maliyet:.5f}"
        else:
            servis_adi, model_adi = kayit["servis"].strip(), kayit["model"].strip()
            eksik_fiyatlar.add(f"{servis_adi} | {model_adi}")
            maliyet, maliyet_metni = None, "price not set"

        alaka_puani = alaka(puan_idleri, profil, etiketler) if puan_idleri else None
        olcumler.append({
            "servis": servis_adi, "model": model_adi, "tip": kayit["tip"].split("_")[0],
            "input_token": input_token, "sure": kayit["sure_sn"], "ihlal": len(denetim["ihlaller"]),
            "alaka": alaka_puani, "maliyet": maliyet,
            "oneri_sayisi": len(route), "katalog_disi": katalog_disi,
            "listede_olmayan": len(denetim["uydurmalar"]) if katalog_disi is None else None,
        })
        tablo.append({
            "Service": servis_adi, "Model": model_adi, "Profile": kayit["profil"],
            "Type": kayit["tip"].split("_")[0], "Time s": kayit["sure_sn"],
            "In tokens": input_token, "Coverage": kapsam,
            "Violations": len(denetim["ihlaller"]), "Flagged": len(denetim["uyarililar"]),
            "Relevance": "-" if alaka_puani is None else f"{alaka_puani:.2f}", "Cost": maliyet_metni,
        })

    # --- Özet tablo
    print("\n" + "=" * 78)
    print("SUMMARY\n")
    tablo_yazdir(tablo)

    # --- Servis başına özet: A, B, C için ortalamalar + B'nin A'ya göre farkı
    print("\n" + "=" * 78)
    print(f"PER-SERVICE SUMMARY (averages over profiles; monthly = {GUNLUK_ISTEK:,} requests/day × {AYDAKI_GUN} days)")
    servisler = []
    for o in olcumler:
        if (o["servis"], o["model"]) not in servisler:
            servisler.append((o["servis"], o["model"]))
    for servis, model in servisler:
        print(f"\n{servis} | {model}")
        satirlar, ozet = [], {}
        for tip in ["A", "B", "C"]:
            grup = [o for o in olcumler if (o["servis"], o["model"], o["tip"]) == (servis, model, tip)]
            if not grup:
                continue
            maliyet = ortalama(o["maliyet"] for o in grup)
            alaka_ort = ortalama(o["alaka"] for o in grup)
            ozet[tip] = {"token": ortalama(o["input_token"] for o in grup), "sure": ortalama(o["sure"] for o in grup)}
            satirlar.append({
                "Type": tip, "Runs": len(grup),
                "Avg in tokens": f"{ozet[tip]['token']:,.0f}",
                "Avg time s": f"{ozet[tip]['sure']:.2f}",
                "Violations (sum)": sum(o["ihlal"] for o in grup),
                "Avg relevance": "-" if alaka_ort is None else f"{alaka_ort:.2f}",
                "Cost / request": "price not set" if maliyet is None else f"${maliyet:.5f}",
                "Monthly cost": "-" if maliyet is None else f"${maliyet * GUNLUK_ISTEK * AYDAKI_GUN:,.0f}",
            })
        tablo_yazdir(satirlar)
        if "A" in ozet and "B" in ozet:
            print(f"  B vs A: tokens {yuzde_fark(ozet['A']['token'], ozet['B']['token'])}, "
                  f"time {yuzde_fark(ozet['A']['sure'], ozet['B']['sure'])}")

    print("\nNotes:")
    print("  - Input tokens are counted with the Qwen tokenizer; each vendor's tokenizer differs (approximate).")
    print(f"  - Output tokens are estimated from answer length (~{KARAKTER_PER_TOKEN} chars/token).")
    print("  - Violations and relevance are based on Jev labels; C answers are matched to the catalog by name.")
    print("  - Costs are computed without prompt caching; cost includes input + estimated output tokens.")
    print("  - Times are measured manually by the user (wall-clock, including network).")
    if eksik_fiyatlar:
        print("\n  Prices missing in FIYATLAR (vendor_rapor.py) for: " + ", ".join(sorted(eksik_fiyatlar)))
    return olcumler  # sunum_ozeti.py kullanır


if __name__ == "__main__":
    main()

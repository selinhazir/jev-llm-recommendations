"""
Sunum için tek özet: tüm güncel (İngilizce) rakamları kaynak dosyalardan hesaplar.

1. Servis karşılaştırması (vendor_results.json): her servis için A/B/C satırları,
   B'nin A'ya göre token/süre/aylık maliyet farkı, C'de katalog dışı öneriler
2. Jev etiketleme: çağrı başına token ve süre, toplam maliyet
3. Doğruluk tablosu (altin_etiketler.json)
4. Veri eksikliği oranları (erişilebilirlik bilgisi yok)
5. Yerel karşılaştırma ve sıra testi (qwen2.5:3b), ölçek hesabı

    python3 sunum_ozeti.py

Çıktı: data/sunum_ozeti.json (+ terminale İngilizce özet)
"""

import contextlib
import io

import dogruluk
import vendor_rapor
from etiketle import DOLAR_PER_MILYON_INPUT_TOKEN, ETIKET_DOSYASI, MEKAN_DOSYASI, TUR_DOSYASI, json_oku, json_yaz
from filtrele import ERISILEBILIRLIK_TURLERI
from karsilastir import AYDAKI_GUN, GUNLUK_ISTEK
from karsilastir import SONUC_DOSYASI as AB_SONUC_DOSYASI
from sira_testi import CONTEXT_PENCERESI, OLCEK_MEKAN_SAYISI
from sira_testi import SONUC_DOSYASI as SIRA_SONUC_DOSYASI

OZET_DOSYASI = "data/sunum_ozeti.json"


def ortalama(degerler):
    degerler = [d for d in degerler if d is not None]
    return sum(degerler) / len(degerler) if degerler else None


def servis_ozeti():
    """vendor_rapor.py'nin ölçümlerinden servis başına A/B/C özetini çıkarır."""
    with contextlib.redirect_stdout(io.StringIO()):  # ayrıntılı raporu ekrana basma
        olcumler = vendor_rapor.main() or []
    servisler = {}
    for o in olcumler:
        servisler.setdefault(f"{o['servis']} | {o['model']}", []).append(o)

    sonuc = {}
    for ad, kayitlar in servisler.items():
        satirlar = {}
        for tip in ["A", "B", "C"]:
            grup = [o for o in kayitlar if o["tip"] == tip]
            if not grup:
                continue
            maliyet = ortalama(o["maliyet"] for o in grup)
            satirlar[tip] = {
                "runs": len(grup),
                "avg_input_tokens": ortalama(o["input_token"] for o in grup),
                "avg_time_s": ortalama(o["sure"] for o in grup),
                "violations": sum(o["ihlal"] for o in grup),
                "avg_relevance": ortalama(o["alaka"] for o in grup),
                "cost_per_request": maliyet,
                "monthly_cost": None if maliyet is None else maliyet * GUNLUK_ISTEK * AYDAKI_GUN,
                "suggestions": sum(o["oneri_sayisi"] for o in grup),
                "off_catalog": sum(o["katalog_disi"] or 0 for o in grup) if tip == "C" else None,
                "not_in_list": sum(o["listede_olmayan"] or 0 for o in grup) if tip != "C" else None,
            }
        fark = None
        if "A" in satirlar and "B" in satirlar:
            a, b = satirlar["A"], satirlar["B"]
            fark = {
                "tokens_pct": 100 * (b["avg_input_tokens"] - a["avg_input_tokens"]) / a["avg_input_tokens"],
                "time_pct": 100 * (b["avg_time_s"] - a["avg_time_s"]) / a["avg_time_s"],
                "time_s": b["avg_time_s"] - a["avg_time_s"],
                "monthly_cost_pct": None, "monthly_cost_usd": None,
            }
            if a["monthly_cost"] and b["monthly_cost"] is not None:
                fark["monthly_cost_pct"] = 100 * (b["monthly_cost"] - a["monthly_cost"]) / a["monthly_cost"]
                fark["monthly_cost_usd"] = b["monthly_cost"] - a["monthly_cost"]
        sonuc[ad] = {"rows": satirlar, "b_vs_a": fark}
    return sonuc


def jev_ozeti():
    etiketler = json_oku(ETIKET_DOSYASI)
    kayitlar = list(etiketler.values())
    token = sum(k["input_tokens"] for k in kayitlar)
    sureler = [k["sure_sn"] for k in kayitlar]
    return {
        "calls": len(kayitlar),
        "questions_per_call": 12,
        "total_input_tokens": token,
        "avg_tokens_per_call": token / len(kayitlar),
        "avg_time_s": sum(sureler) / len(sureler),
        "min_time_s": min(sureler), "max_time_s": max(sureler),
        "total_cost_usd": token / 1_000_000 * DOLAR_PER_MILYON_INPUT_TOKEN,
        "price_per_m_input": DOLAR_PER_MILYON_INPUT_TOKEN,
    }


def dogruluk_ozeti():
    """dogruluk.py ile aynı mantık: her kısıt için doğru / güvenli hata / tehlikeli hata."""
    altin = json_oku(dogruluk.ALTIN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    tur_adlari = {t["id"]: t["ad"] for t in json_oku(TUR_DOSYASI)}
    sonuc = {}
    for alan, (tur_id, cesit) in dogruluk.ALANLAR.items():
        dogru, guvenli, tehlikeli = 0, 0, 0
        for kayit in altin:
            if kayit[alan] is None:
                continue
            etiket = etiketler[str(kayit["id"])]
            jev = (dogruluk.jev_choice_karari if cesit == "choice" else dogruluk.jev_noul_karari)(etiket, tur_id)[0]
            if jev == kayit[alan]:
                dogru += 1
            elif dogruluk.hata_yonu(cesit, kayit[alan], jev) == "güvenli":
                guvenli += 1
            else:
                tehlikeli += 1
        toplam = dogru + guvenli + tehlikeli
        sonuc[tur_adlari[tur_id]] = {"correct": dogru, "total": toplam, "accuracy": dogru / toplam if toplam else None,
                                     "safe_errors": guvenli, "unsafe_errors": tehlikeli, "question": cesit}
    return sonuc


def veri_eksikligi():
    etiketler = json_oku(ETIKET_DOSYASI)
    tur_adlari = {t["id"]: t["ad"] for t in json_oku(TUR_DOSYASI)}
    sonuc = {}
    for tur_id in ERISILEBILIRLIK_TURLERI:
        secimler = [e["erisim_cevaplari"][tur_id]["secim"] for e in etiketler.values()]
        sonuc[tur_adlari[tur_id]] = {
            "barrier_present": secimler.count("engel_var"),
            "no_barrier": secimler.count("engel_yok"),
            "no_information": secimler.count("bilgi_yok"),
            "no_information_pct": 100 * secimler.count("bilgi_yok") / len(secimler),
        }
    return sonuc


def yerel_karsilastirma():
    ab = json_oku(AB_SONUC_DOSYASI)
    sira = json_oku(SIRA_SONUC_DOSYASI)["profiller"]
    mekan_sayisi = len(json_oku(MEKAN_DOSYASI))
    token_a = ortalama(s["A"]["input_token"] for s in ab.values())
    token_b = ortalama(s["B"]["input_token"] for s in ab.values())
    kosular = [k for s in sira.values() for k in s["a_karisik"]]
    secimler = [n for k in kosular for n in k["liste_sirasi"] if n is not None]
    ornek = next(iter(sira.values()))
    mekan_basina = (ornek["a_orijinal_sira"]["input_token"] - ornek["sabit_token"]) / mekan_sayisi
    return {
        "model": "qwen2.5:3b (local)", "profiles": len(ab),
        "avg_tokens_A": token_a, "avg_tokens_B": token_b, "token_savings_pct": 100 * (token_a - token_b) / token_a,
        "violations_A": sum(len(s["A"]["ihlal"]) for s in ab.values()),
        "violations_B": sum(len(s["B"]["ihlal"]) for s in ab.values()),
        "shuffled_runs": len(kosular),
        "shuffled_violation_rate_pct": 100 * sum(len(k["ihlal"]) for k in kosular) / sum(len(k["rota"]) for k in kosular),
        "shuffled_first10_pct": 100 * sum(1 for n in secimler if n <= 10) / len(secimler),
        "tokens_per_venue": mekan_basina,
        "scale_venues": OLCEK_MEKAN_SAYISI,
        "scale_tokens": mekan_basina * OLCEK_MEKAN_SAYISI,
        "scale_x_context": mekan_basina * OLCEK_MEKAN_SAYISI / CONTEXT_PENCERESI,
        "venues_fit_in_context": CONTEXT_PENCERESI / mekan_basina,
    }


def main():
    ozet = {
        "services": servis_ozeti(),
        "assumptions": {"requests_per_day": GUNLUK_ISTEK, "days_per_month": AYDAKI_GUN},
        "jev_labeling": jev_ozeti(),
        "accuracy": dogruluk_ozeti(),
        "data_gaps": veri_eksikligi(),
        "local_comparison": yerel_karsilastirma(),
    }
    json_yaz(OZET_DOSYASI, ozet)

    # --- Terminale kısa İngilizce özet
    print("VENDOR COMPARISON")
    for ad, s in ozet["services"].items():
        print(f"\n{ad}")
        for tip, r in s["rows"].items():
            ekstra = f"  off-catalog {r['off_catalog']}/{r['suggestions']}" if tip == "C" else ""
            print(f"  {tip}: tokens {r['avg_input_tokens']:,.0f}  time {r['avg_time_s']:.2f}s  "
                  f"violations {r['violations']}  relevance {r['avg_relevance']:.2f}  "
                  f"cost/req ${r['cost_per_request']:.5f}  monthly ${r['monthly_cost']:,.0f}{ekstra}")
        f = s["b_vs_a"]
        if f:
            print(f"  B vs A: tokens {f['tokens_pct']:+.0f}%, time {f['time_pct']:+.0f}% ({f['time_s']:+.2f}s), "
                  f"monthly cost {f['monthly_cost_pct']:+.0f}% (${f['monthly_cost_usd']:+,.0f})")
    j = ozet["jev_labeling"]
    print(f"\nJEV LABELING: {j['calls']} calls, {j['avg_tokens_per_call']:,.0f} tokens/call, "
          f"{j['avg_time_s']:.2f}s/call, total ${j['total_cost_usd']:.4f}")
    print("\nACCURACY")
    for ad, d in ozet["accuracy"].items():
        print(f"  {ad}: {d['correct']}/{d['total']} ({100 * d['accuracy']:.0f}%), "
              f"safe errors {d['safe_errors']}, unsafe errors {d['unsafe_errors']}")
    print("\nDATA GAPS")
    for ad, d in ozet["data_gaps"].items():
        print(f"  {ad}: no information {d['no_information']}/100 ({d['no_information_pct']:.0f}%)")
    y = ozet["local_comparison"]
    print(f"\nLOCAL ({y['model']}): tokens {y['avg_tokens_A']:,.0f} -> {y['avg_tokens_B']:,.0f} "
          f"(-{y['token_savings_pct']:.0f}%), violations {y['violations_A']} -> {y['violations_B']}, "
          f"shuffled violation rate {y['shuffled_violation_rate_pct']:.0f}%, "
          f"first-10 picks {y['shuffled_first10_pct']:.0f}%")
    print(f"SCALE: ~{y['tokens_per_venue']:.0f} tokens/venue, {y['scale_venues']:,} venues = "
          f"{y['scale_tokens'] / 1e6:.1f}M tokens ({y['scale_x_context']:.0f}x a 1M context)")
    print(f"\nSaved: {OZET_DOSYASI}")


if __name__ == "__main__":
    main()

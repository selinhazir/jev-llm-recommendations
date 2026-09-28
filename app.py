"""
Sunum arayüzü (Streamlit) — "Fast, Safe, Personalized Recommendations: Jev + LLM".
Arayüz metinleri İngilizce; kod yorumları Türkçe.

Çalıştırma:
    .venv/bin/streamlit run app.py

- Kenar çubuğundan kısıt ve ilgi seçilir, "Build route"a basılır.
- Filtreleme ve sıralama anında yapılır (Jev etiketleri önceden hazır, API çağrısı yok).
- Rota için iki mod var:
    Saved result: daha önce üretilmiş rota gösterilir (Ollama gerekmez)
    Run live    : yerel LLM'e (Ollama, qwen2.5:3b) istek gönderilir ve sonuç kaydedilir
- Filtresiz yöntem arayüzde canlı çalıştırılmaz (çok uzun sürer); "Comparison"
  sekmesinde kayıtlı sonuçları gösterilir.
"""

import html
import re
import urllib.error

import pandas as pd
import streamlit as st

from label import ERISIM_SECENEKLERI, ETIKET_DOSYASI, MEKAN_DOSYASI, TUR_DOSYASI, json_oku, json_yaz
from constraints import CHOICE_GUVEN_ESIGI, ELENIR, UYARI, filtre_karari
import json

from compare import AYDAKI_GUN, GUNLUK_ISTEK
from compare import SONUC_DOSYASI as AB_SONUC_DOSYASI
from route import ILK_KAC_MEKAN, LLM_MODELI, PROFILLER, filtrele_ve_sirala, llm_icin_mekan, llm_rota_iste
from shuffle_test import SONUC_DOSYASI as SIRA_SONUC_DOSYASI
from vendor_report import FIYATLAR, KARAKTER_PER_TOKEN

ROTA_KAYIT_DOSYASI = "data/rota_kayitlari.json"  # Canlı çalıştırılan rotalar buraya kaydedilir

# Açıklamada vurgulanacak ipucu kelimeleri. \b: kelime sınırı, \w*: ekler
# (ör. "stair" -> "stairs", "staircase"; "slope" -> "sloped").
IPUCU_KALIPLARI = [
    r"\bstair\w*", r"\bsteps?\b", r"\bsteep\w*", r"\bslope\w*", r"\bslipper\w*",
    r"\bcobblestone\w*", r"\bnarrow\w*", r"\bcrowd\w*", r"\blong walks?\b", r"\bnot allowed\b",
]
IPUCU_REGEX = re.compile("|".join(IPUCU_KALIPLARI), re.IGNORECASE)

# Serbest seçilen türler için LLM'e gidecek istek cümleleri.
# (Hazır 5 profil için route.py'deki istek metinleri kullanılır.)
ISTEK_CUMLELERI = {
    "yasli": "I'm elderly; long walks and stairs tire me out a lot.",
    "tekerlekli_sandalye": "I use a wheelchair.",
    "kucuk_cocuklu_aile": "We're traveling with small kids and use a stroller.",
    "evcil_hayvan": "I'm traveling with my dog and want to take him everywhere.",
    "genc_butce": "I'm a student on a very tight budget.",
    "kultur_meraklisi": "I love history, art and museums.",
    "gastronomi": "I want to try local flavors and long-established restaurants.",
    "doga_macera": "I love the outdoors, nature and active things.",
    "balayi": "We're on our honeymoon and want romantic, scenic places.",
    "ergen_cocuklu_aile": "We're traveling with teenagers and want exciting places they won't find boring.",
    "is_seyahati": "I'm here on business and want short, central visits of 1-2 hours.",
    "yalniz_gezgin": "I'm traveling alone and like social places where I can meet people.",
}

# Maliyet: vendor_report.py ile aynı fiyat ve aynı output tahmini
MALIYET_MODELI = "Claude | Claude Opus 5.5"
MALIYET_NOTU = "Claude Opus 5.5 price, input + estimated output, no prompt caching."


def istek_maliyeti(rota):
    """Bir rota isteğinin dolar maliyeti: input token + tahmini output token.
    Output, vendor_report.py'deki gibi cevabın uzunluğundan tahmin edilir
    (cevap = modelin döndürdüğü JSON; ~4 karakter ≈ 1 token)."""
    fiyat = FIYATLAR[MALIYET_MODELI]
    cevap = json.dumps({"route": rota["rota"], "explanation": rota["aciklama"]}, ensure_ascii=False)
    output_token = len(cevap) / KARAKTER_PER_TOKEN
    return (rota["input_token"] * fiyat["input"] + output_token * fiyat["output"]) / 1_000_000


def aylik(istek_basina_dolar):
    """Günde GUNLUK_ISTEK istek, AYDAKI_GUN gün."""
    return istek_basina_dolar * GUNLUK_ISTEK * AYDAKI_GUN


KARAR_ETIKETI = {ELENIR: "❌ excluded", UYARI: "⚠️ not verified", "gosterilir": "✅ suitable"}
# Kod içindeki Türkçe choice değerleri -> ekranda gösterilecek İngilizce seçenek adı
SECIM_ETIKETI = {tr: en for en, tr in ERISIM_SECENEKLERI.items()}


# ---------------------------------------------------------------- veri yükleme
@st.cache_data
def verileri_yukle():
    """Veri dosyalarını bir kez okur (Streamlit her tıklamada script'i baştan çalıştırır)."""
    mekanlar = json_oku(MEKAN_DOSYASI)
    etiketler = json_oku(ETIKET_DOSYASI)
    turler = json_oku(TUR_DOSYASI)
    ab_sonuclari = json_oku(AB_SONUC_DOSYASI, varsayilan={})
    sira_sonuclari = json_oku(SIRA_SONUC_DOSYASI, varsayilan={})
    return mekanlar, etiketler, turler, ab_sonuclari, sira_sonuclari


def profil_anahtari(kisitlar, ilgiler):
    """Seçim kombinasyonu için sabit bir anahtar, ör. 'kisit=yasli|ilgi=kultur_meraklisi'."""
    return f"kisit={','.join(sorted(kisitlar))}|ilgi={','.join(sorted(ilgiler))}"


def kayitli_rotalar(ab_sonuclari):
    """Kayıtlı rotalar: karşılaştırmadaki B sonuçları + canlı çalıştırmada kaydedilenler."""
    kayitlar = {}
    for profil_adi, profil in PROFILLER.items():
        if profil_adi in ab_sonuclari:
            kayitlar[profil_anahtari(profil["kisitlar"], profil["ilgiler"])] = ab_sonuclari[profil_adi]["B"]
    kayitlar.update(json_oku(ROTA_KAYIT_DOSYASI, varsayilan={}))
    return kayitlar


def istek_metni(kisitlar, ilgiler):
    """LLM'e gidecek kullanıcı isteği. Hazır profillerden biriyse onun metnini kullanır."""
    for profil in PROFILLER.values():
        if sorted(profil["kisitlar"]) == sorted(kisitlar) and sorted(profil["ilgiler"]) == sorted(ilgiler):
            return profil["istek"]
    cumleler = [ISTEK_CUMLELERI[t] for t in kisitlar + ilgiler]
    return " ".join(cumleler) + " Can you suggest a one-day route?"


def ipuclarini_vurgula(metin):
    """Açıklamadaki ipucu kelimelerini <mark> ile vurgular (HTML güvenli)."""
    guvenli = html.escape(metin)
    return IPUCU_REGEX.sub(lambda e: f"<mark>{e.group(0)}</mark>", guvenli)


def jev_karar_metni(mekan, etiket, tur_id):
    """Jev'in (veya kod kuralının) kararını olasılık/güven değeriyle birlikte yazar."""
    if tur_id in etiket.get("erisim_cevaplari", {}):
        c = etiket["erisim_cevaplari"][tur_id]
        return f"Jev choice: **{SECIM_ETIKETI[c['secim']]}** (confidence {c['confidence']:.2f})"
    if tur_id in etiket.get("engel_olasiliklari", {}):
        return f"Jev barrier probability: **{etiket['engel_olasiliklari'][tur_id]:.2f}**"
    return f"Code rule: price level **{mekan['fiyat_seviyesi']}** (not asked to Jev)"


# ---------------------------------------------------------------- sayfa
st.set_page_config(page_title="Jev + LLM Recommendations", layout="wide")
mekanlar, etiketler, turler, ab_sonuclari, sira_sonuclari = verileri_yukle()
mekan_sozlugu = {m["id"]: m for m in mekanlar}
tur_adi = {t["id"]: t["ad"] for t in turler}

# --- Kenar çubuğu
with st.sidebar:
    st.header("Traveler profile")
    st.caption("Constraints are hard filters (they exclude venues). Interests are ranking scores.")
    st.subheader("Constraints")
    secili_kisitlar = [t["id"] for t in turler if t["tip"] == "kisit" and st.checkbox(t["ad"], key=f"k_{t['id']}")]
    st.subheader("Interests")
    secili_ilgiler = [t["id"] for t in turler if t["tip"] == "ilgi" and st.checkbox(t["ad"], key=f"i_{t['id']}")]
    st.divider()
    mod = st.radio("Route source", ["Show saved result", "Run live"],
                   help=f"Live: sends a request to the local {LLM_MODELI} model (Ollama must be running).")
    olustur = st.button("Build route", type="primary", width="stretch")
    st.caption("Preset profiles (saved results available): " +
               "; ".join(" + ".join(tur_adi[t] for t in p["kisitlar"] + p["ilgiler"]) for p in PROFILLER.values()))

st.title("Jev + LLM Recommendations")
st.caption(f"Sample venue catalog (Istanbul) · {len(mekanlar)} venues pre-labeled with Jev · "
           f"top {ILK_KAC_MEKAN} sent to the LLM")

rota_sekmesi, karsilastirma_sekmesi = st.tabs(["Route", "Comparison (full list vs Jev-filtered)"])

# ---------------------------------------------------------------- Rota sekmesi
with rota_sekmesi:
    if olustur:
        if not secili_ilgiler:
            st.warning("Select at least one interest for ranking.")
        else:
            anahtar = profil_anahtari(secili_kisitlar, secili_ilgiler)
            profil = {"kisitlar": secili_kisitlar, "ilgiler": secili_ilgiler}
            kalanlar = filtrele_ve_sirala(profil, mekanlar, etiketler)
            adaylar = kalanlar[:ILK_KAC_MEKAN]

            rota_sonucu, hata = None, None
            if mod == "Run live":
                try:
                    with st.spinner(f"{LLM_MODELI} is building a route..."):
                        llm_mekanlari = [llm_icin_mekan(m, uyari) for m, _, uyari in adaylar]
                        s = llm_rota_iste(istek_metni(secili_kisitlar, secili_ilgiler), llm_mekanlari)
                    rota_sonucu = {k: s[k] for k in ["rota", "aciklama", "input_token", "sure_sn"]}
                    tum_kayitlar = json_oku(ROTA_KAYIT_DOSYASI, varsayilan={})
                    tum_kayitlar[anahtar] = rota_sonucu
                    json_yaz(ROTA_KAYIT_DOSYASI, tum_kayitlar)
                except (urllib.error.URLError, ConnectionError):
                    hata = "Could not connect to Ollama. Is `ollama serve` running?"
            else:
                rota_sonucu = kayitli_rotalar(ab_sonuclari).get(anahtar)
                if rota_sonucu is None:
                    hata = "No saved route for this combination. Select 'Run live' and try again."

            # Sonucu oturumda sakla: onay kutuları değişince sayfa sıfırlanmasın
            st.session_state["sonuc"] = {
                "anahtar": anahtar, "profil": profil, "kalanlar": kalanlar,
                "adaylar": adaylar, "rota": rota_sonucu, "hata": hata, "mod": mod,
            }

    sonuc = st.session_state.get("sonuc")
    if sonuc is None:
        st.info("Select constraints and interests on the left, then click **Build route**.")
    else:
        profil, kalanlar, adaylar, rota = sonuc["profil"], sonuc["kalanlar"], sonuc["adaylar"], sonuc["rota"]
        if sonuc["anahtar"] != profil_anahtari(secili_kisitlar, secili_ilgiler):
            st.info("Your selection has changed. Click **Build route** again for a new result.")
        st.markdown("**Profile:** " + " + ".join(tur_adi[t] for t in profil["kisitlar"] + profil["ilgiler"]))

        # Her mekânın her kısıt için kararı (elenen / uyarılı listeleri için)
        kararlar = {
            m["id"]: {k: filtre_karari(m, etiketler[str(m["id"])], k)[0] for k in profil["kisitlar"]}
            for m in mekanlar
        }
        elenen_idler = [i for i, k in kararlar.items() if ELENIR in k.values()]
        uyarili = [(m, puan) for m, puan, uyari in kalanlar if uyari]

        # 1) Özet kartları
        st.subheader("Summary")
        c = st.columns(4)
        c[0].metric("Total venues", len(mekanlar))
        c[1].metric("Excluded", len(elenen_idler))
        c[2].metric("Flagged (among remaining)", len(uyarili))
        c[3].metric("Sent to the model", len(adaylar))
        c = st.columns(4)
        if rota:
            maliyet = istek_maliyeti(rota)
            c[0].metric("Input tokens", f"{rota['input_token']:,}")
            c[1].metric("Time", f"{rota['sure_sn']:.1f} s")
            c[2].metric("Estimated cost / request", f"${maliyet:.5f}")
            c[3].metric(f"Monthly ({GUNLUK_ISTEK:,} requests/day)", f"~${aylik(maliyet):,.0f}")
            st.caption(f"{MALIYET_NOTU} Source: {sonuc['mod'].lower()}.")

        # 2) Önerilen rota
        st.subheader("Recommended route")
        if sonuc["hata"]:
            st.error(sonuc["hata"])
        elif rota:
            aday_idler = {m["id"] for m, _, _ in adaylar}
            satirlar = []
            for sira, mekan_id in enumerate(rota["rota"], 1):
                mekan = mekan_sozlugu.get(mekan_id)
                if mekan is None or mekan_id not in aday_idler:
                    satirlar.append({"#": sira, "Venue": f"⚠️ Hallucinated id: {mekan_id}"})
                    continue
                etiket = etiketler[str(mekan_id)]
                puan = sum(etiket["ilgi_olasiliklari"][i] for i in profil["ilgiler"]) / len(profil["ilgiler"])
                durum = ", ".join(f"{tur_adi[k]}: {KARAR_ETIKETI[v]}" for k, v in kararlar[mekan_id].items())
                satirlar.append({"#": sira, "Venue": mekan["isim"], "District": mekan["semt"],
                                 "Interest score": round(puan, 2), "Constraint status": durum or "—"})
            st.dataframe(pd.DataFrame(satirlar), hide_index=True, width="stretch")
            st.markdown(f"**Model's explanation** (one text for the whole route): {rota['aciklama']}")

        # 3) Aday listesi
        st.subheader(f"Candidate list ({len(adaylar)} venues sent to the model)")
        rotadakiler = set(rota["rota"]) if rota else set()
        st.dataframe(pd.DataFrame([{
            "#": n, "Venue": m["isim"], "Category": m["kategori"], "District": m["semt"],
            "Interest score": round(puan, 2), "Flag": "⚠️ not verified" if uyari else "",
            "In route": "✅" if m["id"] in rotadakiler else "",
        } for n, (m, puan, uyari) in enumerate(adaylar, 1)]), hide_index=True, width="stretch")

        # 4) Uyarılı mekânlar
        st.subheader(f"Flagged venues — accessibility not verified ({len(uyarili)})")
        if uyarili:
            st.caption("Not excluded, but the description has no accessibility information (or Jev's "
                       f"confidence is below {CHOICE_GUVEN_ESIGI}). Shown to the user with a warning.")
            st.dataframe(pd.DataFrame([{
                "Venue": m["isim"], "Category": m["kategori"], "Interest score": round(puan, 2),
                "In candidate list": "✅" if any(a[0]["id"] == m["id"] for a in adaylar) else "",
            } for m, puan in uyarili]), hide_index=True, width="stretch")
        else:
            st.write("No flagged venues.")

        # 5) Elenen mekânlar — kısıt türüne göre gruplu
        st.subheader(f"Excluded venues ({len(elenen_idler)})")
        st.caption("Note: Jev does not generate explanations; it returns only a decision and a probability. "
                   "The highlights below are keyword matches marked by code and may not be the exact reason "
                   "for Jev's decision.")
        for kisit in profil["kisitlar"]:
            bu_kisitla_elenen = [mekan_sozlugu[i] for i in elenen_idler if kararlar[i][kisit] == ELENIR]
            with st.expander(f"{tur_adi[kisit]} — {len(bu_kisitla_elenen)} venues"):
                for mekan in bu_kisitla_elenen:
                    st.markdown(f"**{mekan['isim']}** · {mekan['kategori']} · "
                                f"{jev_karar_metni(mekan, etiketler[str(mekan['id'])], kisit)}")
                    st.markdown(f"<small>Cues in the description:</small><br>"
                                f"{ipuclarini_vurgula(mekan['aciklama'])}", unsafe_allow_html=True)
                    st.divider()
        if not profil["kisitlar"]:
            st.write("No constraints selected; no venues were excluded.")

# ---------------------------------------------------------------- Karşılaştırma sekmesi
with karsilastirma_sekmesi:
    st.info("This tab shows **saved results** (produced by compare.py). The full-list method sends "
            f"all {len(mekanlar)} venues to the model, so it is not run live in the interface.")
    if not ab_sonuclari:
        st.warning(f"{AB_SONUC_DOSYASI} not found. Run `python3 compare.py` first.")
    else:
        def rota_isimleri(ids):
            return ", ".join(mekan_sozlugu.get(i, {}).get("isim", f"?{i}") for i in ids)

        satirlar = []
        for profil_adi, s in ab_sonuclari.items():
            p = PROFILLER[profil_adi]
            satirlar.append({
                "Profile": " + ".join(tur_adi[t] for t in p["kisitlar"] + p["ilgiler"]),
                "Tokens (full)": s["A"]["input_token"], "Tokens (filtered)": s["B"]["input_token"],
                "Time s (full)": s["A"]["sure_sn"], "Time s (filtered)": s["B"]["sure_sn"],
                "Violations (full)": len(s["A"]["ihlal"]), "Violations (filtered)": len(s["B"]["ihlal"]),
                "Flagged (full)": len(s["A"]["uyarili"]), "Flagged (filtered)": len(s["B"]["uyarili"]),
            })
        tablo = pd.DataFrame(satirlar)

        token_a, token_b = tablo["Tokens (full)"].sum(), tablo["Tokens (filtered)"].sum()
        c = st.columns(4)
        c[0].metric("Token savings", f"{100 * (token_a - token_b) / token_a:.0f}%")
        c[1].metric("Violations (full → filtered)",
                    f"{tablo['Violations (full)'].sum()} → {tablo['Violations (filtered)'].sum()}")
        # Profil başına istek maliyetlerinin ortalaması × günde istek × gün
        maliyet_a = sum(istek_maliyeti(s["A"]) for s in ab_sonuclari.values()) / len(ab_sonuclari)
        maliyet_b = sum(istek_maliyeti(s["B"]) for s in ab_sonuclari.values()) / len(ab_sonuclari)
        c[2].metric("Monthly cost, full list", f"~${aylik(maliyet_a):,.0f}")
        c[3].metric("Monthly cost, filtered", f"~${aylik(maliyet_b):,.0f}")
        st.dataframe(tablo, hide_index=True, width="stretch")

        st.subheader("Selected routes")
        st.dataframe(pd.DataFrame([{
            "Profile": r["Profile"],
            "Full-list route": rota_isimleri(s["A"]["rota"]),
            "Filtered route": rota_isimleri(s["B"]["rota"]),
        } for r, s in zip(satirlar, ab_sonuclari.values())]), hide_index=True, width="stretch")

        # Sıra testi sonucu varsa: karışık listelerde seçimlerin kaçı ilk 10 sıradan geldi?
        sira_notu = ""
        kosular = [k for s in sira_sonuclari.get("profiller", {}).values() for k in s["a_karisik"]]
        secimler = [n for k in kosular for n in k["liste_sirasi"] if n is not None]
        if secimler:
            oran = 100 * sum(1 for n in secimler if n <= 10) / len(secimler)
            sira_notu = (f"In the order test (shuffled lists), {oran:.0f}% of the full-list picks came from the "
                         "first 10 positions of the list (10% expected by chance), so violation counts "
                         "depend on this small model. ")
        st.caption(f"Model: {LLM_MODELI} (local, 3B). {sira_notu}Token savings are model-independent. "
                   f"Monthly cost: {GUNLUK_ISTEK:,} requests/day × {AYDAKI_GUN} days; {MALIYET_NOTU}")

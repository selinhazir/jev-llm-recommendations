# Jev + LLM Recommendations

A small, vendor-neutral demo: pre-label a venue catalog once with **Jev** ([TypeSafe](https://typesafe.ai)'s System One model), then use those labels to filter and rank candidates before ever calling an LLM for trip planning. The question it answers: *does a cheap, deterministic pre-filter actually help once you put a real LLM behind it?*

**[→ Read the presentation (PDF)](docs/presentation.pdf)** — 17 slides: the problem, the design, vendor comparison (ChatGPT, Claude), accuracy, data gaps, scale, limitations and next steps.

An interactive version of the headline numbers is also available as a [live page](https://claude.ai/artifact/QFvwWyDM1bYW4q5LycKhf5) (private by default; ask the owner for access).

## The idea

```
100 venues  ──Jev──▶  per-venue labels   ──code──▶  10 filtered venues  ──LLM──▶  route
 (1 call        (constraint pass/fail       (hard filter +
  per venue)      + interest score)          ranking, no model call)
```

1. **Label once.** Each venue gets one Jev call with ~12 questions: a handful of hard *constraints* (does this place have a barrier for a wheelchair user? is it pet-friendly?) and several *interest* scores (would a food lover enjoy this?). Results are cached by an input hash, so re-running only re-labels what changed.
2. **Filter with code, not tokens.** At request time, constraints exclude venues deterministically; venues with no accessibility information are kept but flagged, never silently marked "fine". Interest scores rank what's left. No model call happens at this step — it's a plain Python filter over already-computed labels.
3. **Send only the shortlist to the LLM.** The model (local or a vendor API) picks a route from ~10 pre-vetted venues instead of reading all 100 and guessing at the constraints itself.

## Headline numbers

Measured on a 100-venue sample catalog (Istanbul) with 12 traveler profiles (5 hard constraints, 7 interest types). Full method notes and per-vendor breakdown are in the [write-up](https://claude.ai/artifact/QFvwWyDM1bYW4q5LycKhf5).

| | |
|---|---|
| Input tokens per request, full list → filtered | ~87–88% fewer, across every vendor tested and the local model |
| Constraint violations, full list → filtered | 8 → 0 (local model), 1 → 0 per vendor (ChatGPT, Claude) |
| Jev labeling cost (one-time, 100 venues) | $0.0073 (100 calls, ~1,737 tokens/call, ~0.43 s/call) |
| Accuracy vs. reference labels (20 venues) | 70–100% by constraint, see write-up for direction of errors |
| Catalog scale | ~69 tokens/venue → 300k venues ≈ 21× a 1M-token context window |

None of this includes prompt caching, and the vendor comparison is a small sample (3 profiles × 1 run per vendor) — see the write-up's "what's robust vs. what depends on the model" section before quoting these numbers elsewhere.

## Repo layout

```
data/                    sample catalog, labels, and every experiment's results (JSON)
prompts/                 generated prompt files for the vendor comparison (A/B/C per profile)
etiketle.py              labels every venue with Jev (TypeSafe API)
filtrele.py              the constraint filter — the one place thresholds and rules live
dogruluk.py              accuracy check against reference labels
ozet.py                  labeling summary (excluded/flagged counts, data gaps, cost)
rota.py                  builds one route with a local LLM (Ollama) from a filtered shortlist
karsilastir.py           A/B: full list vs. Jev-filtered, run against the local model
sira_testi.py            shuffled-list robustness test + catalog-scale projection
prompt_paketi.py         exports the A/B/C prompts used for the vendor comparison
kaydet.py                records a vendor's pasted response (service, model, timing, answer)
vendor_rapor.py          scores recorded vendor responses: coverage, violations, relevance, cost
sunum_ozeti.py           pulls every number above into one JSON + console summary
app.py                   Streamlit UI: build a route, inspect why venues were excluded/flagged
backup_tr/                pre-translation snapshot of the code and data (see note below)
```

## Setup

Requires Python 3.9+, a [TypeSafe](https://typesafe.ai) API key, and — for the local-model scripts — [Ollama](https://ollama.com) with `qwen2.5:3b` pulled.

```bash
# TypeSafe API key, for etiketle.py only
echo "TYPESAFE_API_KEY=..." > .env

# Streamlit app dependency (separate venv; the rest of the scripts use plain stdlib + urllib)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# local model, for rota.py / karsilastir.py / sira_testi.py / prompt_paketi.py
ollama pull qwen2.5:3b
```

## Running it

Most of this is already computed and checked into `data/`; you don't need to re-run anything to explore the results.

```bash
# 1. Label the catalog with Jev (cheap; only re-labels what changed)
python3 etiketle.py hepsi

# 2. Sanity-check labeling
python3 ozet.py            # coverage, exclusions, data gaps, cost
python3 dogruluk.py        # accuracy against data/altin_etiketler.json

# 3. Local-model experiments (needs Ollama running)
python3 karsilastir.py     # full list vs. filtered, 5 profiles
python3 sira_testi.py      # shuffled-list robustness + scale projection

# 4. Vendor comparison (manual — paste responses from other providers)
python3 prompt_paketi.py   # writes prompts/*.txt + manifest.json
python3 kaydet.py          # record a pasted vendor response
python3 vendor_rapor.py    # score it: coverage, violations, relevance, cost

# roll every number above into data/sunum_ozeti.json + a console summary
python3 sunum_ozeti.py

# interactive UI
.venv/bin/streamlit run app.py
```

## Notes on the data

- The catalog is illustrative sample data (`data/istanbul_mekanlar.json`): venue names are real, but descriptions, hours and price levels are approximate and shouldn't be treated as current information.
- Reference labels for the accuracy check (`data/altin_etiketler.json`) were produced by an LLM (Claude) reading only the venue descriptions, without seeing Jev's output — so `dogruluk.py` measures agreement between two independent methods, not ground truth verified by a person.
- `backup_tr/` is a pre-translation snapshot of the project (Turkish UI copy, an earlier working name in some files/comments). It's kept for reference; decide before publishing whether it belongs in a public or shared repo.
- All costs are approximate: no prompt caching, and input tokens for vendor comparisons are counted with the Qwen tokenizer (each vendor's own tokenizer differs slightly).

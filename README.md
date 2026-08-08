# Landed

**No cost lands without its evidence.**

SGTDP Hackathon 2026 — *AI Manufacturing Decision Copilot*
**Primary track: Track 2 — Quotation & Landed-Cost Intelligence**

---

## What this is

Supplier quotations arrive in incompatible shapes. One is priced per piece, another
per thousand. One is FOB Shanghai, another DDP. One quotes USD with no exchange-rate
date. Before anyone can say which supplier is cheaper, those quotes have to be made
comparable — and often they simply *can't* be, because a required term is missing or
two sources contradict each other.

**Landed** normalizes supplier quotations, computes comparable landed cost with full
provenance, and — the part that matters — tells you which comparisons you are **not
yet entitled to make**, and exactly what is missing.

> It doesn't tell you which supplier is cheapest.
> It tells you whether you're in a position to ask.

## The three states

Every quote resolves to exactly one state. These are the product's core model, and
they map onto the three demonstration cases the brief requires.

| State | Meaning | Brief's required case |
|---|---|---|
| ✅ **LANDED** | All required fields present and sourced. Total issued. | Success case |
| ⚠️ **CONTESTED** | Sources disagree. Both values shown, no winner picked. | Ambiguous / conflicting case |
| ⛔ **NOT LANDED** | A required field is missing. **No total issued.** | Failure / fallback case |

`NOT LANDED` is a feature, not a limitation. Guessing a freight term to produce a
tidy number is exactly the failure mode the brief warns against.

## Design rules

These are non-negotiable and every module obeys them.

1. **The LLM extracts. Python calculates.** No arithmetic ever passes through a model.
2. **Every field carries provenance** — `{value, unit, currency, source_file, page, confidence}`.
   There are no bare values anywhere in the system.
3. **Missing required field → refuse to total.** Never impute silently.
4. **Schema-first, never column-first.** The challenge pack schema is unknown until
   kickoff; our schema is not. Extraction maps arbitrary documents onto a fixed contract.
5. **Extracted fact ≠ team assumption ≠ model output.** These are visually and
   structurally distinct everywhere they appear.

## Architecture

```
 documents            ingest.py        extract.py         normalize.py
 (PDF/XLSX/CSV) ───▶ text + page ───▶ LLM → schema ───▶ units, currency,
                     anchors           + citations       price basis
                                                              │
                                             ┌────────────────┴────────────────┐
                                             ▼                                 ▼
                                       conflicts.py                     cost_engine.py
                                    missing / contradiction              PURE PYTHON
                                    unit mismatch / undated              deterministic
                                             │                                 │
                                             └────────────────┬────────────────┘
                                                              ▼
                                                    LANDED / CONTESTED / NOT LANDED
                                                              │
                                                     app/app.py (Streamlit)
```

Full detail: [`docs/architecture.md`](docs/architecture.md)

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env         # then add your API key
```

## Run

```bash
streamlit run app/app.py
```

Reproduce the reported evaluation numbers:

```bash
python scripts/run_eval.py --pack packs/official --out results/
```

## Repository layout

```
landed/           core library
  schema.py         the extraction contract — read this first
  ingest.py         documents → text with page anchors
  extract.py        LLM → schema, citation required per field
  normalize.py      units, currency, per-piece vs per-1000
  cost_engine.py    deterministic landed-cost math + refusal guard
  conflicts.py      missing / contradiction / unit mismatch / undated currency
  mutate.py         seeded perturbation harness for robustness evaluation
  evaluate.py       scoring against organizer reference calculations
app/              Streamlit interface
packs/synthetic/  our dev pack (tracked — clearly labelled synthetic)
packs/official/   organizer pack (gitignored; manifest is tracked)
tests/            unit tests — cost engine and conflict detectors
docs/             required written deliverables
results/          scoring output, threshold records, run configs
scripts/          entry points
```

## Data handling

The organizer challenge pack is **not committed to this repository**. Only its
manifest — version, file list, and SHA-256 checksums — is tracked, which records
exactly which data version produced our numbers without redistributing the data.

See [`docs/data-manifest.md`](docs/data-manifest.md).

## Deliverables index

| Required deliverable | Where |
|---|---|
| Working prototype | `app/`, `landed/` |
| Source + reproducible setup | this README, `requirements.txt` |
| Architecture & data-flow | [`docs/architecture.md`](docs/architecture.md) |
| Data/source manifest | [`docs/data-manifest.md`](docs/data-manifest.md) |
| Baseline comparison & results | [`docs/evaluation.md`](docs/evaluation.md), `results/` |
| Three demonstration cases | [`docs/demo-cases.md`](docs/demo-cases.md) |
| Intended use, limits, approval points | [`docs/safety-statement.md`](docs/safety-statement.md) |
| Presentation | [`docs/pitch.md`](docs/pitch.md) |

## Scope boundary

Decision support only. Landed does not contact suppliers, issue requests for
quotation, approve vendors, or place orders. Every consequential action stays behind
explicit human confirmation. See [`docs/safety-statement.md`](docs/safety-statement.md).

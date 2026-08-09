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
                                                  landed/web (FastAPI + Jinja)
```

Full detail: [`docs/architecture.md`](docs/architecture.md)

## Setup

Requires **Python 3.11+** (developed on 3.12). On Windows, `python` may resolve to
an older interpreter — use the launcher to be explicit.

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env         # then add your API key
```

### Database

PostgreSQL, and only PostgreSQL. Supporting a second engine would mean testing
against something other than what ships, and would rule out the features the schema
depends on — JSONB with a GIN index so conflict filters are an indexed lookup rather
than a scan, and CITEXT so an email is one account whatever its capitalisation.

```bash
docker compose up -d
alembic upgrade head
```

Tests run against a real server too, in a `landed_test` database created on first
run. Each test is wrapped in a transaction that is rolled back afterwards, so the
schema is built once rather than per test. With no server reachable, the database
tests skip with an explicit reason and the rest of the suite still runs.

### Extraction provider

Extraction is the only stage that calls a model. Set `LANDED_PROVIDER` to
`anthropic`, `gemini`, or `ollama`. Note that the Anthropic API is billed separately
from a Claude Pro subscription; Gemini has a free tier and Ollama runs locally.

## Run

```bash
uvicorn landed.web.app:app --reload
```

### The flow

1. **Create a project** and state the order quantity. Everything downstream is a
   function of it — tooling is amortized across the order, so the cheapest supplier
   at 5,000 is routinely not the cheapest at 50,000.
2. **Add a supplier** for each one you are considering. The supplier list is what the
   comparison is built from; a supplier with no quotation yet shows as an empty column
   rather than silently not appearing.
3. **Upload each supplier's quotation** into that supplier's column, and their profile
   if you have one. Filenames are not interpreted — you say which supplier a file
   belongs to.
4. **State the cost assumptions**: freight, duty, insurance, cost of capital, FX rate
   and its date. No quotation contains these — a supplier prices goods, not your
   logistics — so they come from you, carry your name, and are shown as assumptions
   everywhere they appear. Leave one blank and the comparison refuses to total rather
   than guessing it.
5. **Compare.** Each run is a new version; earlier ones are never rewritten.

Compare a pack headlessly, without the web layer:

```bash
python -m landed.cli compare --pack packs/synthetic --quantity 10000
```

Reproduce the reported evaluation numbers:

```bash
python scripts/run_eval.py --pack packs/official --out results/
```

## Repository layout

```
landed/
  core/             domain — knows nothing of HTTP, users, or the database
    schema.py         the extraction contract — read this first
    ingest.py         documents → text or page images, with anchors
    extract.py        LLM → schema, citation required per field
    normalize.py      units, currency, per-piece vs per-1000
    cost_engine.py    deterministic landed-cost math + refusal guard
    conflicts.py      missing / contradiction / unit mismatch / undated currency
  services/         orchestration: run a comparison, persist a version, diff
  db/               SQLAlchemy models and session
  web/              FastAPI routes, Jinja templates, auth
  eval/             mutation harness and scoring — imports core only
  cli.py            headless comparison, no web layer needed
packs/synthetic/  our dev pack (tracked — clearly labelled synthetic)
packs/official/   organizer pack (gitignored; manifest is tracked)
tests/            layering, cost engine, conflict detectors
docs/             required written deliverables
results/          scoring output and run configs
scripts/          evaluation and checksum entry points
```

`core/` never imports from `services/`, `db/`, `web/`, or `eval/`. That constraint
is enforced by [`tests/test_layering.py`](tests/test_layering.py) and is what lets
the evaluation harness score the engine headlessly.

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

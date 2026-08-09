# Landed

**No cost lands without its evidence.**

SGTDP Hackathon 2026 — *AI Manufacturing Decision Copilot*
**Primary track: Track 2 — Quotation & Landed-Cost Intelligence**

---

## What this is

Supplier quotations arrive in incompatible shapes. One is FOB Shenzhen, another DDP
Karachi. One charges 14,500 for tooling, another 38,000. One is priced per piece,
another per thousand. Before anyone can say which supplier is cheaper, those quotes
have to be made comparable — and often they simply *can't* be, because a required
term is missing or two sources contradict each other.

**Landed** normalizes supplier quotations, computes comparable landed cost with full
provenance, and — the part that matters — tells you which comparisons you are **not
yet entitled to make**, and exactly what is missing.

> It doesn't tell you which supplier is cheapest.
> It tells you whether you're in a position to ask.

### The result that makes the case

Five real-shaped quotations for one part at 20,000 pieces:

| Rank | Supplier | Incoterm | Quoted / piece | **Landed / piece** |
|---|---|---|---:|---:|
| **1** | **Pune Precision Polymers** | DDP Karachi | 4.55 | **5.08** |
| 2 | Konya Kalip Sanayi | EXW Konya | 3.75 | 5.10 |
| 3 | Bac Ninh Moulding JSC | CIF Karachi | 4.10 | 5.22 |
| 4 | Zhongshan Polymer Works | FOB Shenzhen | 3.85 | 5.34 |
| 5 | PT Batam Injection Molding | FOB Batam | 3.42 | 6.04 |

**The dearest quotation wins. The cheapest finishes last.** Sorting by unit price —
which is what a spreadsheet does — picks exactly the wrong supplier. DDP puts ocean
freight, marine insurance and import duty on the seller, so 4.55 delivered beats 3.42
at the factory gate.

Re-run the same five at 100,000 pieces and **the winner changes to Batam**, because
its 38,000 tooling charge falls from 1.90 a unit to 0.38. Nothing on any quotation
says this. It only appears once tooling is amortized across the actual order.

## The three states

Every quote resolves to exactly one state. These map onto the three demonstration
cases the brief requires.

| State | Meaning | Brief's required case |
|---|---|---|
| ✅ **LANDED** | All required fields present and sourced. Total issued. | Success case |
| ⚠️ **CONTESTED** | Sources disagree. Both values shown, no winner picked. | Ambiguous / conflicting case |
| ⛔ **NOT LANDED** | A required field is missing. **No total issued.** | Failure / fallback case |

`NOT LANDED` is a feature, not a limitation. Guessing a freight term to produce a
tidy number is exactly the failure mode the brief warns against.

## Design rules

Non-negotiable, and every module obeys them.

1. **The LLM extracts. Python calculates.** No arithmetic ever passes through a model.
2. **Every field carries provenance** — `{value, unit, currency, source_file, page,
   confidence}`. There are no bare values anywhere in the system.
3. **Missing required field → refuse to total.** Never impute silently.
4. **Schema-first, never column-first.** Extraction maps arbitrary documents onto a
   fixed contract, so a new pack is an adapter change and nothing else.
5. **Extracted fact ≠ team assumption ≠ model output.** Visually and structurally
   distinct everywhere they appear.

## Setup

Requires **Python 3.11+** (developed on 3.12) and **PostgreSQL**.

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
docker compose up -d
alembic upgrade head
```

### Extraction provider

Extraction is the only stage that calls a model. Set both values in `.env`:

```bash
LANDED_PROVIDER=groq
LANDED_EXTRACTION_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=your_key_here
```

`anthropic`, `gemini`, `groq`, `openai`, and `ollama` are supported. Groq and Gemini
have free tiers; Ollama runs locally and sends nothing off the machine.

## Run

```bash
.venv/Scripts/python.exe -m uvicorn landed.web.app:app --reload --reload-include .env --port 8123
```

`--reload-include .env` matters: without it a changed API key is not picked up until
the server is restarted by hand.

Compare a pack headlessly, without the web layer:

```bash
python -m landed.cli compare --pack packs/demo --quantity 20000
```

## The flow

1. **Create a project** and state the order quantity. Everything downstream is a
   function of it.
2. **Add a supplier** for each one under consideration. The comparison is built from
   this list, so a supplier with no quotation yet shows as an empty column rather than
   silently not appearing.
3. **Upload each supplier's quotation** into that supplier's column, plus a profile if
   there is one. Filenames are never interpreted — you say which supplier a file
   belongs to.
4. **Compare.** Each run is a new version; earlier ones are never rewritten, so a
   report already sent still says what it said.

Cost assumptions — freight, duty, insurance, cost of capital, payment days — are
fixed for the demo, shown read-only on the project page, and labelled as team
assumptions wherever they appear. No supplier quotes them, so they can never be
presented as evidence from a document.

## Demo pack

Eight documents across PDF, DOCX, XLSX and CSV, in
[`packs/demo/`](packs/demo/README.md) — five complete, valid, mutually consistent
quotations where the ranking turns purely on commercial values. Expected results and
the full arithmetic for any total are in that README.

[`packs/synthetic/`](packs/synthetic/README.md) is the defect pack: seeded
contradictions, a missing Incoterm, a per-1000 price in EUR, an image-only quote with
no text layer, and a prompt-injection attempt in a supplier profile. It is the fixture
set for every conflict detector.

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

```
landed/
  core/             domain — knows nothing of HTTP, users, or the database
    schema.py         the extraction contract — read this first
    ingest.py         documents → text or page images, with anchors
    extract.py        LLM → schema, citation required per field
    labelled.py       deterministic read of well-labelled documents, no model call
    normalize.py      units, currency, per-piece vs per-1000
    cost_engine.py    deterministic landed-cost math + refusal guard
    conflicts.py      missing / contradiction / unit mismatch / undated currency
  services/         orchestration: run a comparison, persist a version, diff
  db/               SQLAlchemy models and session
  web/              FastAPI routes, Jinja templates, auth
  eval/             mutation harness and scoring — imports core only
packs/demo/       five clean suppliers; the decision demo
packs/synthetic/  seeded defects; the detector fixtures
docs/             required written deliverables
```

`core/` never imports from `services/`, `db/`, `web/`, or `eval/`. That constraint is
enforced by [`tests/test_layering.py`](tests/test_layering.py) and is what lets the
engine be scored headlessly.

### Extraction cost

Well-labelled documents are read by a deterministic pass with **no model call at all**
— a full five-supplier comparison over the demo pack completes in under a second and
consumes no API quota. The model is invoked only when that pass cannot find a required
field, which keeps a live demo immune to a rate limit.

## Safety

- Decision support only. Landed does not contact suppliers, issue RFQs, approve
  vendors, or place orders.
- Document text is fenced before it reaches the model, so instruction-shaped content
  inside an uploaded file reads as evidence *about* the document rather than as a
  directive. Injection attempts are flagged and change no ranking.
- Uploaded files are served under `Content-Security-Policy: sandbox` with
  `X-Content-Type-Options: nosniff`, so an HTML or SVG upload cannot run script
  against a logged-in session.
- Any change the assistant proposes is inert until a person confirms it.
- Values a person supplies are recorded as assumptions against their name, applied on
  the *next* run, and are reversible — the version a report was issued from is never
  altered after the fact.

See [`docs/safety-statement.md`](docs/safety-statement.md).

## Tests

```bash
python -m pytest -q
```

342 tests covering the cost engine, conflict detectors, normalization, layering,
provider handling, and the web flow. Database tests run against a real PostgreSQL
server in a `landed_test` database, each wrapped in a rolled-back transaction; with no
server reachable they skip with an explicit reason and the rest still runs.

## Deliverables index

| Required deliverable | Where |
|---|---|
| Working prototype | `landed/`, run instructions above |
| Source + reproducible setup | this README, `requirements.txt` |
| Architecture & data-flow | [`docs/architecture.md`](docs/architecture.md) |
| Data/source manifest | [`docs/data-manifest.md`](docs/data-manifest.md) |
| Three demonstration cases | [`docs/demo-cases.md`](docs/demo-cases.md), `packs/synthetic/` |
| Intended use, limits, approval points | [`docs/safety-statement.md`](docs/safety-statement.md) |
| Baseline comparison & evaluation results | [`docs/evaluation.md`](docs/evaluation.md) |
| Presentation | [`docs/pitch.md`](docs/pitch.md) |

## Limitations

Stated plainly, because an imperfect metric reported honestly is worth more than a
suspiciously clean one.

- **The quantitative evaluation is not complete.** The metric definitions and the
  mutation harness are scaffolded in `landed/eval/`, but the scoring run against
  organizer reference calculations has not been executed — the official pack was not
  available. Every number quoted in this README comes from our own demo pack and is
  reproducible from it, not from organizer data.
- **No timed human baseline** has been recorded yet, so the efficiency claim is
  unquantified.
- Extraction runs synchronously inside the request. A large pack takes tens of seconds
  with no progress indicator.
- One product category, five suppliers, one destination. No claim of generalization to
  other packs or to production sourcing.

## Data handling

The organizer challenge pack is **not committed to this repository**. Only its
manifest — version, file list, and SHA-256 checksums — is tracked, which records
exactly which data version produced our numbers without redistributing the data. See
[`docs/data-manifest.md`](docs/data-manifest.md).

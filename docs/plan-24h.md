# 24-Hour Build Plan

## Non-negotiables

1. LLM extracts. Python calculates.
2. Every field carries provenance.
3. Missing required field → refuse to total.
4. Schema-first, never column-first.

---

## Block A — Foundation (H0–H3)

| H | Work | Done when |
|---|---|---|
| 0–1 | **Pack recon.** Open every file. Inventory formats, field names, where reference calcs live. Run `checksum_pack.py`. | One page of notes: what's there, what's missing, which fields drive cost |
| 0–1 | *(parallel)* Repo scaffold, schema frozen, deps installed | `import landed` works |
| 1–2 | **Synthetic dev pack** — 3 suppliers, seeded defects | Pipeline has something to chew on regardless of pack state |
| 2–3 | `ingest.py` — PDF/XLSX → text with page anchors | Any pack file returns text + page numbers |

> If the official pack isn't out yet, synthetic carries you to H12. Nothing blocks.

## Block B — Core pipeline (H3–H12)

| H | Work | Done when |
|---|---|---|
| 3–6 | `extract.py` — LLM → schema, citation per field, one retry then `missing` | All pack quotes → valid JSON with page-level citations |
| 6–9 | `cost_engine.py` — guard clause first, then terms | Deterministic total + itemized breakdown |
| 9–12 | `conflicts.py` — four detectors | Every synthetic defect caught with a readable reason |

## ⛔ CHECKPOINT — Hour 12

**End-to-end must run on one real pack quote and produce a cited total.**

If it doesn't: cut freight/duty to the pack's flat stated assumptions and move on.
A correct simple total beats an incomplete sophisticated one. Do not let the cost
engine eat Block C.

## Block C — Product surface (H12–H17)

| H | Work | Done when |
|---|---|---|
| 12–15 | Comparison view — suppliers as columns, breakdown as rows, provenance on click | Readable in 10 seconds |
| 15–16 | **Conflict panel** — the headline | Reads as "here's what you can't compare yet" |
| 16–17 | Break-even chart | Crossover visible |

Streamlit defaults. No custom CSS.

## Block D — Evidence (H17–H21) ← where the marks are

| H | Work |
|---|---|
| 17–19 | `mutate.py` — 50 seeded variants from one pack |
| 19–21 | `evaluate.py` — run vs reference calcs + mutation suite, populate `evaluation.md` |

Hand-check citations on 30 samples. Time-boxed, defensible.

## Block E — Deliverables (H21–H24)

| H | Work |
|---|---|
| 21–22 | README polish, `architecture.md`, `data-manifest.md` |
| 22–23 | `safety-statement.md`, `demo-cases.md` |
| 23–24 | `pitch.md` + timed rehearsal of all three cases |

---

## Cut list, in order

1. Duty/HS-code modelling → pack's flat stated rates
2. Break-even chart → static table at two quantities
3. Prompt-injection demo → keep detection, drop the demo slot
4. Multi-currency → single currency + declared assumption
5. **Never cut:** citations, the refusal path, reference-calc comparison

## Team split

**3–4 people:** extract / cost engine / UI, one floating to evaluation. Schema is
the interface — frozen at H1, so the three streams never block each other.

**Solo:** 10 hand-written mutations instead of 50, drop the break-even chart.

**Sleep:** stagger 4-hour shifts from H12. Hour 20 with a fried team is where
working demos get broken.

# Architecture & Data Flow

> Required deliverable: *"A concise architecture and data-flow explanation."*

## Pipeline

```
documents ──▶ ingest ──▶ extract ──▶ normalize ──┬──▶ conflicts ──┐
(PDF/XLSX/    text +      LLM →       units,     │                ├──▶ state
 CSV)         page        schema      currency,  └──▶ cost_engine ┘    + UI
              anchors     + cites     basis           (pure Python)
```

## The boundary that matters

| Stage | May call an LLM? | Why |
|---|---|---|
| ingest | No | Mechanical text extraction |
| extract | **Yes — only here** | Reconciling heterogeneous documents is the task models are genuinely good at |
| normalize | No | Deterministic conversions |
| conflicts | No | Rule-based detection |
| cost_engine | **Never** | Arithmetic must be testable and diffable against reference calculations |

`tests/test_cost_engine.py::TestDeterminism::test_no_model_client_imported`
enforces the bottom row rather than relying on discipline.

## Provenance chain

<!-- TODO: trace one value end to end — quotation PDF page 2, cell B14,
     through extraction, normalization, into the total, and back out via the
     provenance drawer. One worked example beats three paragraphs of prose. -->

## State derivation

<!-- TODO: the exact rule mapping missing[] and conflicts[] onto
     LANDED / CONTESTED / NOT_LANDED -->

## Trust boundaries

Supplier documents are untrusted input. Document text is data, never instruction.

<!-- TODO: where injection scanning runs, what happens on a hit, and why flagging
     beats both obeying and silently dropping -->

## What we deliberately did not build

<!-- TODO: supplier contact, RFQ issuance, vendor approval, order placement — all
     out of scope by the brief's submission boundary. Also: no external tariff
     lookups, no inferred HS codes. -->

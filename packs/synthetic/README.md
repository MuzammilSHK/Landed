# Synthetic Development Pack

**Authored by us. Not organizer data. Never used for any reported metric.**

Built so the pipeline had something to run against before the official pack was
released, and kept afterwards because its deliberate defects double as fixtures for
the conflict detectors.

Every number in [`../../docs/evaluation.md`](../../docs/evaluation.md) comes from
the official pack. If a figure in this project ever traces back to this directory,
that is a bug in the evaluation harness.

## Planned contents

Three suppliers for one product, with defects seeded on purpose:

| Supplier | Defect | Expected state |
|---|---|---|
| A | none — clean baseline | ✅ LANDED |
| B | MOQ contradicts the supplier profile | ⚠️ CONTESTED |
| C | freight terms absent | ⛔ NOT LANDED |

Plus, for the detectors that need them:

- a quote priced per-1000 alongside per-piece quotes (unit mismatch)
- a foreign-currency quote with no rate date (undated currency)
- a supplier profile containing instruction-shaped text (injection scan)

Also needed: a product brief with mandatory requirements, a small BOM, and stated
freight/duty assumptions — enough structure to exercise the whole pipeline.

<!-- TODO: author these files -->

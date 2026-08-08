# Synthetic Development Pack

**Authored by us. Not organizer data. Never used for any reported metric.**

Built so the pipeline had something to run against before the official pack was
released, and kept afterwards because its deliberate defects are the fixtures for
every conflict detector.

Every number in [`../../docs/evaluation.md`](../../docs/evaluation.md) comes from the
official pack. If a reported figure ever traces back to this directory, that is a bug
in the evaluation harness.

## Regenerating

```bash
python packs/synthetic/build.py
```

The documents are generated rather than committed as opaque binaries, so what is
wrong with each supplier is readable in [`build.py`](build.py) instead of requiring
you to open five PDFs to find out.

Rebuilds are byte-identical. Office formats stamp the clock into the file on every
save — both the ZIP entry times and `dcterms:modified` — and `build.py` normalises
both. A diff in this directory therefore always means somebody changed something.
[`tests/test_synthetic_pack.py`](../../tests/test_synthetic_pack.py) asserts it.

## Suppliers

| | Supplier | Documents | Seeded defect | Expected |
|---|---|---|---|---|
| **A** | Shenzhen Precision Metalworks | `quote_a.pdf` | none — clean baseline | ✅ LANDED |
| **B** | Guangzhou Hardline Industrial | `quote_b.pdf`, `profile_b.docx` | MOQ 5,000 in the quote, 10,000 in the profile | ⚠️ CONTESTED |
| **C** | Ningbo Castworks | `quote_c.pdf` | no delivery terms stated anywhere | ⛔ NOT LANDED |
| **D** | Hanoi Precision Housing | `quote_d.xlsx`, `profile_d.pdf` | priced per 1000 in EUR; profile contains a prompt-injection attempt | ✅ LANDED + injection flagged |
| **E** | Istanbul Metal Form | `quote_e.png` | image only, no text layer | ✅ LANDED via vision, flagged for verification |

Supplier D is not defective — a per-1000 price in a foreign currency is perfectly
ordinary, and normalization should handle it silently. It is here to prove the
comparison survives it, and to carry the injection attempt somewhere the ranking
would be tempting to alter.

## Shared documents

| File | Purpose |
|---|---|
| `product_brief.docx` | target quantity, destination, mandatory requirements |
| `bom.csv` | bill of materials |
| `assumptions.xlsx` | freight, duty, insurance, cost of capital, dated EUR rate |
| `manifest.json` | machine-readable expectations, asserted by the test suite |

`manifest.json` exists so tests check declared expectations rather than numbers
copied by hand into an assertion.

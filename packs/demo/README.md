# Demo Pack — CTR-H220 Controller Housing

**Authored by us. Not organizer data. Never used for any reported metric.**

Five suppliers, all complete and valid. Nothing is missing, nothing contradicts, no
document is unreadable. **The ranking is decided entirely by commercial values.**
This pack answers *which supplier should we go with*; defect handling has its own
fixtures in [`packs/synthetic`](../synthetic/README.md).

Regenerate with:

```bash
python packs/demo/build.py
```

Deterministic — every embedded timestamp is pinned, so a rebuild is byte-identical.

## The scenario

Meridian Controls Ltd. is sourcing **CTR-H220**, an ABS+PC controller housing, at
**20,000 pieces**, delivered to Karachi, comparing in **USD**. Five suppliers replied
to the same enquiry, `MC-RFQ-2026-041`.

## What to upload where

| Supplier | Country | File | Upload as |
|---|---|---|---|
| Zhongshan Polymer Works | China | `Zhongshan Polymer Works - Quotation ZPW-2026-0812.pdf` | Quotation |
| Pune Precision Polymers | India | `Pune Precision Polymers - Quotation PPP-EXP-2026-0447.docx` | Quotation |
| PT Batam Injection Molding | Indonesia | `PT Batam Injection - Quotation BIM-Q-2026-0812.xlsx` | Quotation |
| Bac Ninh Moulding JSC | Vietnam | `Bac Ninh Moulding - Quotation BNM-Q-2291.pdf` | Quotation |
| Bac Ninh Moulding JSC | Vietnam | `Bac Ninh Moulding - Company Profile 2026.docx` | Supplier profile |
| Konya Kalip Sanayi | Turkiye | `Konya Kalip - Teklif KKS-2026-118.pdf` | Quotation |

`Product Brief - CTR-H220.docx` and `Bill of Materials - CTR-H220.csv` go in the
shared-material box. Filenames follow no convention — you say which column a file
belongs in.

## The result at 20,000 units

| Rank | Supplier | Incoterm | Unit price | Tooling | **Landed / unit** |
|---|---|---|---:|---:|---:|
| **1** | **Pune Precision Polymers** | DDP Karachi | 4.55 | 9,500 | **5.08** |
| 2 | Konya Kalip Sanayi | EXW Konya | 3.75 | 12,000 | 5.10 |
| 3 | Bac Ninh Moulding JSC | CIF Karachi | 4.10 | 16,000 | 5.22 |
| 4 | Zhongshan Polymer Works | FOB Shenzhen | 3.85 | 14,500 | 5.34 |
| 5 | PT Batam Injection Molding | FOB Batam | 3.42 | 38,000 | 6.04 |

**The supplier with the highest unit price wins. The supplier with the lowest unit
price finishes last.** Sorting the quotations by unit price picks exactly the wrong
supplier, and that is the entire reason a landed-cost tool exists.

Pune quotes 33% more per piece than Batam and lands 16% cheaper, because DDP puts
ocean freight, marine insurance and import duty on the seller — the three lines that
get added on top of everybody else's price.

## The same pack at 100,000 units

| Rank | Supplier | Landed / unit |
|---|---|---:|
| **1** | **PT Batam Injection Molding** | **4.17** |
| 2 | Konya Kalip Sanayi | 4.27 |
| 3 | Zhongshan Polymer Works | 4.40 |
| 4 | Bac Ninh Moulding JSC | 4.58 |
| 5 | Pune Precision Polymers | 4.70 |

**The winner changes.** Batam's 38,000 tooling is 1.90 a unit at 20,000 and 0.38 a
unit at 100,000. Nothing on any quotation says this; it only appears once the tooling
is amortised across the actual order. Change the quantity, re-run, and use the version
diff to show the recommendation moving.

## Zhongshan's arithmetic, if a judge asks you to prove a total

| Term | Amount | From |
|---|---:|---|
| Goods | 77,000.00 | 3.85 × 20,000 |
| Tooling | 14,500.00 | one-off, spread across the order |
| Freight | 8,200.00 | team assumption, flat |
| Insurance | 426.00 | 0.5% of goods + freight |
| Duty | 5,565.69 | 6.5% of CIF value |
| Financing | 1,012.60 | 8% annual over 60 days |
| **Total** | **106,704.29** | |
| **Per unit** | **5.34** | ÷ 20,000 |

Freight, duty, insurance and cost of capital are standing team assumptions, not
figures any supplier quoted — which is why they are listed under the comparison table
rather than presented as evidence from a document.

Compare against Pune, where freight, insurance and duty are all **0.00** with the note
*"DDP: seller bears freight / insurance / import duty"*. That contrast is the clearest
thirty seconds in the demo.

## Extraction cost

Every document in this pack is read by the deterministic labelled pass — **no model
calls at all**, so a full five-supplier run takes under a second and consumes no API
quota. That makes the demo repeatable and immune to a rate limit on the day.

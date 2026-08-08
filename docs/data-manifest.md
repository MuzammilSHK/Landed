# Data & Source Manifest

> Required deliverable: *"A data/source manifest covering the challenge pack and
> any external inputs."*

## Organizer challenge pack

**Not committed to this repository.** The brief prohibits uploading case materials
to unapproved external services; a hosted git remote is one. This manifest records
the pack's identity so every reported number is traceable to the exact data that
produced it, without redistributing that data.

| | |
|---|---|
| Pack version | *TODO — as published by organizers* |
| Received | *TODO* |
| Archive SHA-256 | *TODO* |
| Verified against organizer download page | ☐ |

### File checksums

<!-- GENERATED: python scripts/checksum_pack.py --pack packs/official -->

| File | Bytes | SHA-256 |
|---|---|---|
| *TODO* | | |

<!-- END GENERATED -->

## Synthetic development pack

`packs/synthetic/` — **authored by us, not organizer data.** Built before the
official pack was released so the pipeline had something to run against.

Committed to this repo because it is ours to share, and because the deliberate
defects it contains double as fixtures for the conflict detectors.

**Never used for any reported metric.** Every number in
[`evaluation.md`](evaluation.md) comes from the official pack.

| File | Purpose | Deliberate defect |
|---|---|---|
| *TODO* | | |

## External sources

Anything not from the challenge pack, with the citation the brief requires:
URL, publisher, retrieval date, licence, and how it influenced the result.

| Source | Publisher | Retrieved | Licence | Influence on result |
|---|---|---|---|---|
| *TODO — none so far* | | | | |

## Models & services

| Component | Model / version | What it does | Data sent |
|---|---|---|---|
| extract | *TODO* | Document → schema mapping | Quotation text |
| cost_engine | — | Arithmetic | **None — no external calls** |

## Data handling

- No confidential case material is committed to version control
- Secrets live in `.env` (gitignored); `.env.example` documents the shape
- Pack contents are read locally; only extracted text reaches the extraction model
- <!-- TODO: state the minimum data actually transmitted, and whether any
     redaction runs before transmission -->

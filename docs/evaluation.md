# Evaluation Report

> Required deliverable: *"A baseline comparison and quantitative evaluation results."*

Reproduce everything below with:

```bash
python scripts/run_eval.py --pack packs/official --out results/
```

| | |
|---|---|
| Pack version / SHA-256 | *TODO* |
| Model version | *TODO* |
| Seed | 42 |
| Run date | *TODO* |

## Baseline

The comparison must be against something real. Time a team member doing one
landed-cost comparison by hand, from the same pack, and record the actual figure.
An invented baseline is the easiest claim in the room to puncture.

| | Manual | Landed |
|---|---|---|
| Time to compare N suppliers | *TODO* min | *TODO* s |
| Conflicts detected | *TODO* | *TODO* |
| Fields cited to source | *TODO* | *TODO* |

## Accuracy vs reference calculations

| Metric | Result |
|---|---|
| Cost — mean absolute error | *TODO* |
| Cost — exact matches | *TODO* / *TODO* |
| Lead time — MAE (days) | *TODO* |

## Grounding

| Metric | Result | Method |
|---|---|---|
| Citation coverage | *TODO*% | Automated — fields carrying a resolvable source |
| Citation correctness | *TODO*% | **Hand-checked, 30 samples** |
| Unsupported-claim rate | *TODO*% | Target 0 — the system refuses instead |

Citation correctness is hand-checked deliberately. A model grading its own
citations is not evidence.

## Constraint & agreement

| Metric | Result |
|---|---|
| Mandatory-constraint satisfaction rate | *TODO*% |
| Recommendation agreement with published procedure | *TODO*% |

## Robustness — mutation harness

One pack, N seeded defects. The property under test is not "did it still answer"
but "did it either answer correctly or degrade honestly."

| Mutation | Runs | Correct adjusted answer | Honest degradation | **Silent wrong total** |
|---|---|---|---|---|
| drop_field | | | | |
| flip_price_basis | | | | |
| strip_currency_date | | | | |
| inject_contradiction | | | | |
| swap_incoterm | | | | |
| inject_prompt | | | | |
| **Total** | | | | |

Silent wrong totals are the only true failures. Report the count even if it isn't
zero — an honest non-zero reads as rigour; a suspicious zero invites the question
that unravels the demo.

## Failure analysis

<!-- TODO: the cases that went wrong, why, and what would fix them. Judges
     reward this more than a clean sweep. -->

## Limitations

<!-- TODO: what these numbers do and do not establish. One pack, one product
     category, N suppliers. No claim of generalization to other packs or to
     production sourcing. -->

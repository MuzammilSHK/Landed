# Pitch

> Required deliverable: *"A short presentation explaining the decision, evidence,
> and business value."*

Rubric weight: 10% Pitch & Clarity — but the pitch is also how judges perceive the
other 90%. Evidence they don't understand scores as evidence you don't have.

---

## Open (30s)

Lead with the problem in concrete terms, not abstraction.

> *"Three suppliers, three quotes. One priced per piece, one per thousand, one FOB
> Shanghai with no exchange-rate date. Before anyone can say which is cheaper,
> someone has to spend half a day making these comparable — and sometimes they
> simply aren't."*

## The turn (30s)

State what the product actually is. This is the line that differentiates the
submission from a parser bolted to a spreadsheet.

> *"Landed doesn't tell you which supplier is cheapest. It tells you whether
> you're in a position to ask."*

## Demo (2–3 min)

Three states, in order. See [`demo-cases.md`](demo-cases.md).

1. ✅ LANDED — the cost breakdown, and a click through to a source page
2. ⚠️ CONTESTED — both values, no winner picked
3. ⛔ NOT LANDED — no total issued, and why that's correct

Spend the most time on 3. It's the one nobody else will demo.

## Evidence (1 min)

The numbers from [`evaluation.md`](evaluation.md). Lead with what's checkable:

- Cost error vs the organizer's reference calculations
- Citation coverage, and correctness hand-checked on 30 samples
- Robustness across N seeded mutations — including the silent-wrong-total count

> *"One challenge pack, N seeded defects. Every mutation either produced the
> correct adjusted answer or degraded honestly."*

## Architecture (30s)

One sentence, because it's the defensible design choice:

> *"The model reads documents. Python does the arithmetic. That's why our numbers
> match the reference calculations exactly and why we can unit-test them."*

## Limits & next step (30s)

Name the boundary before a judge does. One pack, one category, no generalization
claim. Then say what validation comes next.

---

## Anticipated questions

| Question | Answer |
|---|---|
| Why not let the LLM compute the total? | *TODO — testability, reproducibility, reference-match* |
| How do you know the citations are right? | *TODO — hand-checked sample, state the number* |
| What if the pack had 50 suppliers? | *TODO — throughput figure* |
| Isn't this just a spreadsheet? | *TODO — the conflict engine; a spreadsheet can't tell you it shouldn't be trusted* |
| What breaks it? | *TODO — name a real failure. Having one ready reads as confidence.* |

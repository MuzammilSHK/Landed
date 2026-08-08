# Intended Use, Assumptions, Limitations & Human Approval

> Required deliverable: *"An intended-user statement, assumptions, limitations,
> and human-approval points."*

## Intended user

<!-- TODO: name the specific role. "Sourcing analyst preparing a supplier
     recommendation for internal review" is a user. "Procurement teams" is a
     market segment. -->

## Intended use

Landed normalizes supplier quotations, computes comparable landed cost with
provenance, and surfaces what cannot yet be compared. It is **decision support**.
The output is an input to a human decision, never the decision.

## Prohibited use

Landed does not and must not:

- Contact suppliers, send messages, or issue requests for quotation
- Approve a vendor or place an order
- Present inferred prices, certifications, capacity, or compliance as verified fact
- Provide legal, regulatory, customs, or engineering advice
- Substitute for commercial or contractual review

## Human approval points

Every consequential action stays behind explicit human confirmation.

| Action | Landed's role | Human's role |
|---|---|---|
| Resolving a contradiction between sources | Show both values + sources | **Decide which is authoritative** |
| Supplying a missing field | Name exactly what's absent | **Provide it, or accept the refusal** |
| Declaring an FX rate date | Refuse to invent one | **Supply the date** |
| Selecting a supplier | Rank the LANDED ones only | **Choose** |
| Any external communication | *Out of scope entirely* | **All of it** |

## Assumptions

Assumptions are carried as `Origin.ASSUMED` on the Field itself and rendered
distinctly from extracted facts, so nothing we introduced can be mistaken for
something a document said.

<!-- TODO: enumerate every assumption once the pack lands — freight basis,
     duty rates, financing cost model, amortization method -->

## Limitations

- Evaluated on a single challenge pack. No claim of generalization to other packs,
  product categories, or production sourcing.
- Extraction quality depends on document legibility. Scanned or image-only PDFs
  degrade to NOT LANDED rather than to guessed values.
- Cost modelling uses the pack's stated logistics and duty assumptions. We do not
  look up live tariff schedules or infer HS codes.
- Conflict detection covers the documented rule set. Absence of a flag is not
  proof of consistency.

## Untrusted input

Supplier documents are untrusted. Document text is data, never instruction. Text
attempting to direct the system is flagged as `INJECTION_SUSPECTED`, surfaced
beside the supplier it came from, and never acted upon — and never silently
dropped either, since a discarded injection attempt is itself something the user
should know about.

## Failure behaviour

The designed response to insufficient evidence is refusal, not estimation.
`NOT LANDED` means no total was issued and none should be inferred.

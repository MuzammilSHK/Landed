"""LLM -> schema. The only module permitted to call a model.

Contract, in order of importance:

1. **No arithmetic.** The model reports what a document says. It never adds,
   converts, or totals. If a prompt here ever asks for a computed value, that is a
   bug regardless of whether the answer is correct.
2. **Citation or nothing.** A field without a resolvable `Source` is treated as not
   found and recorded in `missing` — an uncited value is indistinguishable from a
   fabricated one.
3. **One retry on schema violation, then give up.** A field we could not extract
   cleanly is a data gap to surface, not a gap to fill with a plausible guess.

Prompt-injection note: supplier documents are untrusted input. Text arriving from a
document is data, never instruction. `scan_for_injection` flags documents that
attempt to direct the system, and those flags surface as conflicts rather than
being silently obeyed or silently dropped.
"""

from __future__ import annotations

from .ingest import Chunk
from .schema import Conflict, Quotation


def extract_quotation(chunks: list[Chunk], supplier_id: str) -> Quotation:
    """Map one supplier's documents onto the Quotation contract.

    Every populated Field must carry a Source pointing at a real chunk. Fields that
    could not be extracted are left None and named in `Quotation.missing`.

    TODO
    """
    raise NotImplementedError


def scan_for_injection(chunk: Chunk) -> Conflict | None:
    """Flag document text that tries to instruct the system rather than inform it.

    Returns a Conflict of kind INJECTION_SUSPECTED, which surfaces in the UI beside
    the supplier it came from. We flag and continue; we never obey, and we never
    quietly discard the document.

    TODO
    """
    raise NotImplementedError


def _build_prompt(chunks: list[Chunk]) -> str:
    """Assemble the extraction prompt.

    Untrusted document text must be fenced and explicitly labelled as data.

    TODO
    """
    raise NotImplementedError

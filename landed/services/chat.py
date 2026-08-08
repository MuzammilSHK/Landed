"""The assistant.

Answers questions about a comparison and proposes changes to it. Two rules make it
safe to put in front of evidence a decision rests on.

**It never computes.** Asked what 20,000 units would cost, it does not produce a
number — it proposes a re-run, and `cost_engine` produces the number. Every figure a
user sees comes from the same arithmetic the report and the CLI use.

**It never acts.** Anything that would change the evidence comes back as a *proposal*
the person has to confirm. The model interpreting "freight is about twenty four
hundred" into a recorded assumption without a confirmation step would put a value
into the audit trail that nobody actually approved.

It also answers only from the stored comparison it is given. Asked something the
comparison does not cover, it abstains rather than reaching for general knowledge
about supplier pricing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from landed.core.providers import (
    ExtractionRequest,
    Provider,
    ProviderError,
    get_provider,
)
from landed.db.models import ChatMessage, Comparison, User
from landed.services import comparisons, resolutions
from landed.services.projects import get_project

SYSTEM = (
    "You assist a sourcing analyst reading a landed-cost comparison.\n"
    "Rules, in order of importance:\n"
    "1. Never calculate. Do not add, convert, total, or estimate any figure. If the "
    "user asks what a different order quantity would cost, propose a recompute and "
    "say the engine will produce the number.\n"
    "2. Answer only from the comparison JSON provided. If it does not contain the "
    "answer, set abstained true and say what is missing. Never use general "
    "knowledge about suppliers, prices, freight rates, or duties.\n"
    "3. Quote figures exactly as they appear in the comparison, and name the "
    "supplier they belong to.\n"
    "4. You cannot change anything yourself. To supply a missing value or settle a "
    "contradiction, return a proposed action; a person confirms it.\n"
    "5. Be brief. Two or three sentences unless asked for detail.\n"
)

ACTION_KINDS = ("none", "supply_value", "choose_source", "recompute")

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "the reply, in plain prose"},
        "abstained": {
            "type": "boolean",
            "description": "true when the comparison does not support an answer",
        },
        "action": {
            "type": "object",
            "description": "a change to propose, or kind 'none'",
            "properties": {
                "kind": {"enum": list(ACTION_KINDS)},
                "supplier_id": {"type": ["string", "null"]},
                "field_path": {"type": ["string", "null"]},
                "value": {"type": ["string", "null"]},
                "currency": {"type": ["string", "null"]},
                "chosen_file": {"type": ["string", "null"]},
                "quantity": {"type": ["integer", "null"]},
                "summary": {
                    "type": "string",
                    "description": "what confirming this would do, in one sentence",
                },
            },
            "required": ["kind", "summary"],
        },
    },
    "required": ["answer", "abstained", "action"],
}


class ProposedAction(BaseModel):
    """A change the assistant suggests. Inert until a person confirms it."""

    kind: Literal["none", "supply_value", "choose_source", "recompute"] = "none"
    supplier_id: str | None = None
    field_path: str | None = None
    value: str | None = None
    currency: str | None = None
    chosen_file: str | None = None
    quantity: int | None = None
    summary: str = ""

    @property
    def is_actionable(self) -> bool:
        if self.kind == "recompute":
            return bool(self.quantity and self.quantity > 0)
        if self.kind in {"supply_value", "choose_source"}:
            return bool(self.supplier_id and self.field_path and self.value)
        return False


class Answer(BaseModel):
    reply: str
    abstained: bool = False
    action: ProposedAction = Field(default_factory=ProposedAction)


def ask(
    session: Session,
    user: User,
    project_id: int,
    question: str,
    provider: Provider | None = None,
) -> ChatMessage:
    """Answer a question about the project's latest comparison.

    Both turns are stored. A recorded assumption that traces back to a conversation
    should be readable alongside it afterwards.
    """
    project = get_project(session, user, project_id)
    session.add(
        ChatMessage(project_id=project.id, role="user", content=question.strip())
    )
    session.commit()

    comparison = comparisons.get_version(session, user, project.id)
    answer = _ask_model(question, comparison, provider)

    reply = ChatMessage(
        project_id=project.id,
        role="assistant",
        content=answer.reply,
        action=answer.action.model_dump() if answer.action.is_actionable else None,
    )
    session.add(reply)
    session.commit()
    return reply


def confirm(
    session: Session,
    user: User,
    project_id: int,
    message_id: int,
    provider: Provider | None = None,
) -> None:
    """Carry out a proposal, now that a person has approved it.

    Routed through the same services the forms use, so a change made from the
    conversation lands in the audit trail identically to one made by hand.
    """
    project = get_project(session, user, project_id)
    message = session.scalars(
        select(ChatMessage).where(
            ChatMessage.id == message_id, ChatMessage.project_id == project.id
        )
    ).one_or_none()
    if message is None or not message.is_pending:
        return

    action = ProposedAction.model_validate(message.action)
    if action.kind == "supply_value":
        resolutions.supply_value(
            session, user, project.id, action.supplier_id, action.field_path,
            action.value, currency=action.currency,
            rationale="confirmed from the assistant",
        )
    elif action.kind == "choose_source":
        resolutions.choose_source(
            session, user, project.id, action.supplier_id, action.field_path,
            action.value, action.chosen_file or "",
            rationale="confirmed from the assistant",
        )
    elif action.kind == "recompute":
        comparisons.run_and_save(
            session, user, project.id, action.quantity, provider=provider
        )

    message.confirmed_at = datetime.now(UTC)
    session.commit()


def history(session: Session, user: User, project_id: int, limit: int = 40):
    project = get_project(session, user, project_id)
    rows = list(
        session.scalars(
            select(ChatMessage)
            .where(ChatMessage.project_id == project.id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
    )
    return list(reversed(rows))


def _ask_model(
    question: str, comparison: Comparison | None, provider: Provider | None
) -> Answer:
    if comparison is None:
        return Answer(
            reply=(
                "There is no comparison to read yet. Upload the documents you have "
                "been sent and run one, then ask again."
            ),
            abstained=True,
        )

    engine = provider or get_provider()
    request = ExtractionRequest(
        instruction=f"Comparison:\n{_state(comparison)}\n\nAnalyst asks: {question}",
        json_schema=REPLY_SCHEMA,
        system=SYSTEM,
        max_tokens=1024,
    )
    try:
        payload = engine.extract(request).payload
    except ProviderError as failure:
        return Answer(
            reply=f"The assistant is unavailable right now ({failure}).",
            abstained=True,
        )
    except ValueError:
        return Answer(
            reply="The assistant returned something unreadable. Try rephrasing.",
            abstained=True,
        )

    action = ProposedAction.model_validate(payload.get("action") or {})
    return Answer(
        reply=str(payload.get("answer", "")).strip() or "No answer was produced.",
        abstained=bool(payload.get("abstained")),
        action=action,
    )


def _state(comparison: Comparison) -> str:
    """A compact, faithful rendering of what the comparison found.

    Only what is stored. Nothing derived here, so the assistant cannot be handed a
    number that the cost engine did not produce.
    """
    lines = [
        f"version {comparison.version}, {comparison.quantity} units, "
        f"currency {comparison.currency}"
    ]
    for result in comparison.results:
        name = result.supplier_name or result.supplier_id
        lines.append(f"\n[{result.supplier_id}] {name} — state: {result.state}")
        if result.breakdown:
            for key in (
                "goods", "tooling_amortized", "freight",
                "insurance", "duty", "financing", "total", "per_unit",
            ):
                term = result.breakdown.get(key) or {}
                lines.append(f"    {key}: {term.get('value')}")
        if result.refusal:
            lines.append(f"    refused: {result.refusal.get('reason')}")
            missing = result.refusal.get("missing_fields") or []
            if missing:
                lines.append(f"    missing: {', '.join(missing)}")
        for conflict in result.conflicts or []:
            if conflict.get("resolved_with"):
                continue
            blocking = "blocking" if conflict.get("blocks_total") else "advisory"
            lines.append(f"    conflict ({blocking}): {conflict.get('message')}")
            for source in conflict.get("sources") or []:
                lines.append(f"        source: {source.get('file')}")
    return "\n".join(lines)

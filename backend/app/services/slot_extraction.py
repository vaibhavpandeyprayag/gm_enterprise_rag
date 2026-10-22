"""Structured LLM extraction for slot filling and confirmation replies.

Values are only *extracted* here; all validation happens in Python through the
task registry (`services/bank_tasks.py`).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.prompts import (
    CONFIRMATION_SYSTEM_PROMPT,
    CONFIRMATION_USER_PROMPT,
    SLOT_EXTRACTION_SYSTEM_PROMPT,
    SLOT_EXTRACTION_USER_PROMPT,
)
from app.services.bank_tasks import FieldSpec
from app.services.history import history_text
from app.services.llm import get_llm

Confirmation = Literal["confirm", "cancel", "unclear"]


class SlotExtraction(BaseModel):
    action: Literal["provide", "cancel"] = "provide"
    value: str | None = Field(
        default=None,
        description="The extracted value, or null when it is not in the message.",
    )


class ConfirmationDecision(BaseModel):
    decision: Confirmation = "unclear"


def extract_field(
    field: FieldSpec,
    question: str,
    history: list[dict[str, Any]],
    settings: Any,
) -> SlotExtraction:
    """Extract `field` from the user's message (falls back to the raw message)."""
    llm = get_llm().with_structured_output(
        SlotExtraction, method="function_calling"
    )
    prompt = SLOT_EXTRACTION_USER_PROMPT.format(
        history=history_text(history, settings.max_history_messages),
        question=question,
    )
    try:
        result = llm.invoke(
            [
                SystemMessage(
                    content=SLOT_EXTRACTION_SYSTEM_PROMPT.format(
                        field_name=field.name,
                        field_description=field.description,
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        return result
    except Exception as exc:
        # The raw message still goes through Python validation, so a parsing
        # failure can never write an unvalidated value.
        print(f"[slots] extraction failed ({exc}); using the raw message")
        return SlotExtraction(action="provide", value=question.strip())


def classify_confirmation(
    question: str,
    history: list[dict[str, Any]],
    settings: Any,
) -> str:
    """Return 'confirm', 'cancel' or 'unclear' for a confirmation reply."""
    llm = get_llm().with_structured_output(
        ConfirmationDecision, method="function_calling"
    )
    prompt = CONFIRMATION_USER_PROMPT.format(
        history=history_text(history, settings.max_history_messages),
        question=question,
    )
    try:
        result = llm.invoke(
            [
                SystemMessage(content=CONFIRMATION_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        return result.decision
    except Exception as exc:
        print(f"[confirm] classification failed ({exc}); treating as unclear")
        return "unclear"

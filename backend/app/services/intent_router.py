"""LLM-based intent routing for the workflow (no keyword matching).

The router returns one operation key. Anything unexpected - including an LLM
failure - falls back to `document_qa`, which is the safe, unauthenticated path.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.prompts import INTENT_ROUTER_SYSTEM_PROMPT, INTENT_ROUTER_USER_PROMPT
from app.services.bank_tasks import DOCUMENT_QA, TASKS, task_catalogue
from app.services.history import history_text
from app.services.llm import get_llm


class IntentDecision(BaseModel):
    operation: str = Field(
        description=(
            "The selected operation key. Use 'document_qa' for document "
            "questions and anything that is not a clear bank request."
        )
    )


def _structured_llm() -> Any:
    # Function calling keeps the output constrained to the declared schema.
    return get_llm().with_structured_output(
        IntentDecision, method="function_calling"
    )


def classify_intent(
    question: str,
    history: list[dict[str, Any]],
    settings: Any,
) -> str:
    """Return a key from `TASKS` or `DOCUMENT_QA`."""
    prompt = INTENT_ROUTER_USER_PROMPT.format(
        history=history_text(history, settings.max_history_messages),
        question=question,
    )
    try:
        decision = _structured_llm().invoke(
            [
                SystemMessage(
                    content=INTENT_ROUTER_SYSTEM_PROMPT.format(
                        operations=task_catalogue()
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        operation = (decision.operation or "").strip()
        if operation == DOCUMENT_QA or operation in TASKS:
            return operation
        print(f"[intent] unknown operation {operation!r}; using document_qa")
    except Exception as exc:
        print(f"[intent] classification failed ({exc}); using document_qa")
    return DOCUMENT_QA

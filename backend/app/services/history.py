"""Conversation history rendering.

A user turn is stored twice: the original wording the user typed, and the
query that was actually used for the retrieval (the rephrased, standalone
query). When the history is handed to the LLM the rephrased query is preferred
for user turns - it is self-contained, so the model follows the thread better
than an elliptical follow-up such as "what about its pricing?".
"""

from __future__ import annotations

# Key of the stored, rephrased (standalone) query inside a user message.
REPHRASED_KEY = "rephrased"


def history_text(history: list[dict], limit: int) -> str:
    """Render the last `limit` messages as a plain transcript.

    User turns use their rephrased query when one was stored, otherwise their
    original content.
    """
    recent = history[-limit:] if history else []
    lines = []
    for message in recent:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "user":
            content = message.get(REPHRASED_KEY) or content
        content = str(content).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no previous messages)"

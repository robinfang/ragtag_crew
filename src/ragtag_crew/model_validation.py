"""Helpers for validating that a model is reachable before switching to it."""

from __future__ import annotations

from ragtag_crew.llm import stream_chat


async def validate_model(model: str) -> str:
    """Run a minimal request and return a short human-readable summary."""
    response = await stream_chat(
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Reply with OK only.",
            }
        ],
    )
    content = response.content.strip() or "(empty response)"
    if len(content) > 120:
        content = content[:117] + "..."
    return content

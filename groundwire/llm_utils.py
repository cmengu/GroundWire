"""
Shared LLM helpers: rate-limit retries and structured output parsing.

Uses Anthropic messages.parse when possible; falls back to messages.create +
model_validate_json so older paths still validate with Pydantic.
"""
from __future__ import annotations

import random
import time
from typing import Callable, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


def llm_call_with_retry(fn: Callable[[], R], max_attempts: int = 3) -> R:
    """Retry on rate limits with exponential backoff + jitter. Re-raise other API errors."""
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except anthropic.RateLimitError as e:
            last_exc = e
            if attempt == max_attempts - 1:
                raise
            wait = (2**attempt) + random.uniform(0, 1)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def parse_structured(
    client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict],
    response_model: Type[T],
    max_attempts: int = 3,
) -> T:
    """
    Prefer messages.parse (schema-constrained). On any failure, fall back to
    create + model_validate_json for strict Pydantic validation.
    """

    def _once() -> T:
        try:
            pm = client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                messages=messages,
                output_format=response_model,
            )
            if pm.parsed_output is not None:
                return pm.parsed_output
        except (anthropic.APIError, ValidationError, TypeError, ValueError, AttributeError):
            pass
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        raw = msg.content[0].text.strip()
        return response_model.model_validate_json(raw)

    return llm_call_with_retry(_once, max_attempts=max_attempts)

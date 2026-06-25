from __future__ import annotations

"""Gemini JSON-schema clients for text and visual invoice parsing."""

import logging
import re
from pathlib import Path
from typing import Any

from ...config import GEMINI_MODEL, GOOGLE_API_KEY
from ...domain.schemas import InvoiceData
from .ai_prompts import SYSTEM_PROMPT, VISUAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AIClientError(RuntimeError):
    """Base exception for AI client failures shown to the workflow."""


class AIRateLimitError(AIClientError):
    """Raised when Gemini rejects a request because quota or rate limits are exhausted."""


def invoke_invoice_parser(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Parse extracted digital-PDF text through Gemini's direct JSON-schema API."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

    try:
        return invoke_invoice_text_json_parser(raw_markdown, vendor_hint)
    except Exception as exc:
        if is_rate_limit_error(exc):
            message = clean_ai_error_message(exc, prefix="Gemini quota or rate limit reached")
            logger.warning("AI parsing quota/rate limit: %s", message)
            raise AIRateLimitError(message) from exc
        logger.exception("AI parsing failed: %s", exc)
        raise AIClientError(f"AI parsing failed: {exc}") from exc


def invoke_invoice_text_json_parser(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini's direct JSON-schema API for extracted digital-PDF text."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GOOGLE_API_KEY)
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Vendor hint from filename: {vendor_hint or 'Unknown'}\n\n"
        f"Invoice text:\n\n{raw_markdown}\n\n"
        "Return only schema-valid JSON."
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=InvoiceData,
        ),
    )
    return invoice_response_to_dict(response)


def invoke_invoice_file_parser(file_path: str | Path, mime_type: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini once with inline file bytes and return an InvoiceData-shaped dictionary."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

    path = Path(file_path)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GOOGLE_API_KEY)
        prompt = (
            f"{VISUAL_SYSTEM_PROMPT}\n\n"
            f"Vendor hint from filename: {vendor_hint or path.name or 'Unknown'}\n"
            "Extract the invoice from the attached file and return only schema-valid JSON."
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=InvoiceData,
            ),
        )
        return invoice_response_to_dict(response)
    except Exception as exc:
        if is_rate_limit_error(exc):
            message = clean_ai_error_message(exc, prefix="Gemini quota or rate limit reached")
            logger.warning("Visual AI parsing quota/rate limit for %s: %s", path, message)
            raise AIRateLimitError(message) from exc
        logger.exception("Visual AI parsing failed for %s: %s", path, exc)
        raise AIClientError(f"Visual AI parsing failed: {exc}") from exc


def invoice_response_to_dict(response: Any) -> dict[str, Any]:
    """Convert a direct Gemini response into an InvoiceData dictionary."""
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return invoice_result_to_dict(parsed)
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise ValueError("Gemini returned no structured invoice data")
    return InvoiceData.model_validate_json(text).model_dump()


def invoice_result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a structured parser result into an InvoiceData dictionary."""
    if isinstance(result, InvoiceData):
        return result.model_dump()
    if isinstance(result, dict):
        return InvoiceData.model_validate(result).model_dump()
    if hasattr(result, "model_dump"):
        return result.model_dump()
    raise ValueError("Gemini returned no structured invoice data")


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return True when an exception chain indicates Gemini quota exhaustion."""
    for current in iter_exception_chain(exc):
        text = f"{current.__class__.__name__}: {current}".lower()
        if "resourceexhausted" in text or "resource_exhausted" in text:
            return True
        if "quota exceeded" in text or "rate limit" in text or "rate-limits" in text:
            return True
        if "429" in text and ("quota" in text or "rate" in text):
            return True
    return False


def iter_exception_chain(exc: BaseException):
    """Yield an exception and its causes/contexts without looping forever."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def clean_ai_error_message(exc: BaseException, prefix: str) -> str:
    """Build a concise user-facing message from a verbose Gemini exception."""
    text = " ".join(str(exc).split())
    retry_match = re.search(r"retry in ([0-9.]+)s", text, flags=re.IGNORECASE)
    retry_text = f" Retry after about {retry_match.group(1)} seconds." if retry_match else ""
    return f"{prefix}.{retry_text} Please wait or switch to a Gemini API key/model with available quota."

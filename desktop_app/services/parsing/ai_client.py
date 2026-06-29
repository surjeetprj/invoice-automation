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

ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
MAX_INLINE_FILE_BYTES = 15 * 1024 * 1024


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
        logger.exception("AI parsing failed")
        raise AIClientError("AI parsing failed. Please try again or use manual review.") from exc


def invoke_invoice_text_json_parser(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini's direct JSON-schema API for extracted digital-PDF text."""
    from google.genai import types

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Vendor hint from filename: {vendor_hint or 'Unknown'}\n\n"
        f"Invoice text:\n\n{raw_markdown}\n\n"
        "Return only schema-valid JSON."
    )
    return _generate_invoice_content([types.Part.from_text(text=prompt)])


def invoke_invoice_file_parser(file_path: str | Path, mime_type: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Call Gemini once with inline file bytes and return an InvoiceData-shaped dictionary."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

    path = Path(file_path)
    validate_inline_invoice_file(path, mime_type)
    try:
        from google.genai import types

        prompt = (
            f"{VISUAL_SYSTEM_PROMPT}\n\n"
            f"Vendor hint from filename: {vendor_hint or path.name or 'Unknown'}\n"
            "Extract the invoice from the attached file and return only schema-valid JSON."
        )
        return _generate_invoice_content(
            [
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type),
            ]
        )
    except Exception as exc:
        if is_rate_limit_error(exc):
            message = clean_ai_error_message(exc, prefix="Gemini quota or rate limit reached")
            logger.warning("Visual AI parsing quota/rate limit for %s: %s", path, message)
            raise AIRateLimitError(message) from exc
        logger.exception("Visual AI parsing failed for %s", path)
        raise AIClientError("Visual AI parsing failed. Please try again or use manual review.") from exc


def _generate_invoice_content(contents: list[Any]) -> dict[str, Any]:
    """Call Gemini with structured output enabled and validate the response."""
    from google import genai
    from google.genai import types

    with genai.Client(api_key=GOOGLE_API_KEY) as client:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=InvoiceData,
            ),
        )
    return invoice_response_to_dict(response)


def validate_inline_invoice_file(path: Path, mime_type: str) -> None:
    """Validate an invoice file before reading it into an inline Gemini request."""
    if not path.exists():
        raise FileNotFoundError(f"Invoice file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Invoice path is not a file: {path}")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"Unsupported invoice file MIME type: {mime_type}")
    size = path.stat().st_size
    if size > MAX_INLINE_FILE_BYTES:
        raise ValueError(f"Invoice file is too large for inline Gemini parsing: {size} bytes")


def invoice_response_to_dict(response: Any) -> dict[str, Any]:
    """Convert a direct Gemini response into an InvoiceData dictionary."""
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return invoice_result_to_dict(parsed)
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise ValueError("Gemini returned no structured invoice data")
    return InvoiceData.model_validate_json(text).model_dump(mode="json")


def invoice_result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a structured parser result into a validated InvoiceData dictionary."""
    if result is None:
        raise ValueError("Gemini returned no structured invoice data")
    if isinstance(result, InvoiceData):
        invoice = result
    elif isinstance(result, dict):
        invoice = InvoiceData.model_validate(result)
    elif hasattr(result, "model_dump"):
        invoice = InvoiceData.model_validate(result.model_dump())
    else:
        raise ValueError(f"Unsupported Gemini invoice result type: {type(result).__name__}")
    return invoice.model_dump(mode="json")


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return True when an exception chain indicates Gemini quota exhaustion."""
    for current in iter_exception_chain(exc):
        cls_name = current.__class__.__name__.lower()
        text = f"{cls_name}: {current}".lower()
        status = str(getattr(current, "status", "")).lower()
        code = str(getattr(current, "code", "")).lower()

        if "resourceexhausted" in cls_name or "resourceexhausted" in text:
            return True
        if "resource_exhausted" in status or "resource_exhausted" in text:
            return True
        if "quota exceeded" in text or "rate limit" in text or "rate-limits" in text:
            return True
        if "429" in text or code == "429":
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
    retry_match = (
        re.search(r"retry\s+(?:in|after)\s+about\s+([0-9.]+)\s*seconds?", text, flags=re.IGNORECASE)
        or re.search(r"retry\s+(?:in|after)\s+([0-9.]+)\s*s(?:ec(?:onds?)?)?", text, flags=re.IGNORECASE)
        or re.search(r"retryDelay['\"]?\s*:\s*['\"]?([0-9.]+)s", text, flags=re.IGNORECASE)
    )
    retry_text = f" Retry after about {retry_match.group(1)} seconds." if retry_match else ""
    return f"{prefix}.{retry_text} Please wait or switch to a Gemini API key/model with available quota."
